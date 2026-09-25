"""Printer keep-awake + online/offline tracking.

The VC-500W goes dark after a stretch of no use: it drops off the network
entirely (not even in ARP), so there is no Wake-on-LAN to bring it back — someone
has to walk down and press the power button. Its web UI has no power settings.

Hypothesis this module tests: the auto power-off is an IDLE timer that a status
read resets. Every `keepalive_min` minutes we read status.xml (cheap, prints
nothing). Whether or not that keeps it awake, the transition log answers the
question: `printer.offline` records how long it had been idle (`idle_s`, time since
the last successful print) when it vanished. Constant idle_s across several drops
= a fixed auto-off timer that our polls do NOT reset.

Rules:
  - Never block a print. The tick uses a NON-blocking acquire of the printer lock
    and simply skips a round if someone holds it (a print is activity anyway).
  - Started only by `labeler-web`'s main(), never by create_app(), so tests and
    WSGI imports don't spawn threads.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .. import protocol
from ..errors import LabelerError
from . import runtime
from .runtime import log_event


@dataclass
class KeepAliveState:
    """What the Device tab / /api/keepalive report. Times are ISO strings."""

    interval_min: int = 0
    online: bool | None = None          # None = not polled yet
    last_ok: str | None = None
    last_fail: str | None = None
    offline_since: str | None = None
    polls: int = 0
    skipped_busy: int = 0
    _offline_t: float | None = field(default=None, repr=False)

    def public(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


class KeepAlive:
    def __init__(self, lock: threading.Lock, settings_loader, *,
                 status_fn=protocol.get_status, clock=time.time):
        self._lock = lock
        self._settings = settings_loader
        self._status = status_fn
        self._clock = clock
        self.state = KeepAliveState()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- one poll; unit-testable without a thread ------------------------------
    def tick(self) -> str:
        """Poll once. Returns 'ok' | 'fail' | 'busy' | 'off'."""
        s = self._settings()
        self.state.interval_min = int(s.keepalive_min or 0)
        if self.state.interval_min <= 0:
            return "off"
        if not self._lock.acquire(blocking=False):
            self.state.skipped_busy += 1
            return "busy"
        try:
            try:
                st = self._status(s.host, timeout=5.0)
                ok = True
            except LabelerError:
                ok = False
        finally:
            self._lock.release()
        self.state.polls += 1
        now = runtime.now_iso()
        if ok:
            if self.state.online is False:
                down_s = (round(self._clock() - self.state._offline_t)
                          if self.state._offline_t else None)
                log_event("printer.online", "printer is back on the network",
                          host=s.host, duration_s=down_s, state=st.print_state)
            self.state.online = True
            self.state.last_ok = now
            self.state.offline_since = None
            self.state._offline_t = None
            return "ok"
        if self.state.online is not False:           # first failure of a run
            log_event("printer.offline", "printer stopped answering",
                      host=s.host, idle_s=_idle_seconds(self._clock()),
                      last_ok=self.state.last_ok)
            self.state.offline_since = now
            self.state._offline_t = self._clock()
        self.state.online = False
        self.state.last_fail = now
        return "fail"

    # -- background loop ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="printer-keepalive",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        log_event("keepalive.start", "printer keep-awake loop started")
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # never let the loop die
                log_event("keepalive.error", str(e), kind=type(e).__name__)
            # Re-read the interval each round so a Settings change applies without a
            # restart; when disabled, check back every minute.
            mins = self.state.interval_min if self.state.interval_min > 0 else 1
            self._stop.wait(mins * 60)


def _idle_seconds(now: float) -> int | None:
    """Seconds since the last recorded print (stats stream), or None."""
    from datetime import datetime
    recs = runtime.read_stats()
    if not recs or not recs[-1].get("timestamp"):
        return None
    try:
        last = datetime.fromisoformat(recs[-1]["timestamp"]).timestamp()
    except ValueError:
        return None
    return max(0, round(now - last))
