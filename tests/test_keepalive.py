"""Keep-awake poller (src/labeler/web/keepalive.py). No real printer, no thread."""

from __future__ import annotations

import threading

import pytest

from labeler.errors import ConnectionBusy
from labeler.web import keepalive as ka
from labeler.web import runtime
from labeler.web.runtime import WebSettings


class _St:
    print_state = "IDLE"


@pytest.fixture
def events(monkeypatch, tmp_path):
    got = []
    monkeypatch.setattr(ka, "log_event", lambda ev, msg, **f: got.append((ev, f)))
    monkeypatch.setattr(runtime, "read_stats", lambda: [])
    return got


def _make(status_fn, minutes=5, clock=None):
    lock = threading.Lock()
    s = WebSettings(host="printer", keepalive_min=minutes)
    k = ka.KeepAlive(lock, lambda: s, status_fn=status_fn,
                     clock=clock or (lambda: 1000.0))
    return k, lock


def test_disabled_does_nothing(events):
    calls = []
    k, _ = _make(lambda h, timeout: calls.append(h), minutes=0)
    assert k.tick() == "off"
    assert calls == [] and events == []


def test_skips_when_printer_busy_never_blocks_a_print(events):
    calls = []
    k, lock = _make(lambda h, timeout: calls.append(h))
    lock.acquire()                     # someone is printing
    try:
        assert k.tick() == "busy"      # returns immediately, doesn't wait
    finally:
        lock.release()
    assert calls == [] and k.state.skipped_busy == 1


def test_ok_poll_marks_online_and_releases_lock(events):
    k, lock = _make(lambda h, timeout: _St())
    assert k.tick() == "ok"
    assert k.state.online is True and k.state.last_ok
    assert lock.acquire(blocking=False)   # lock was released
    lock.release()


def test_offline_then_online_logs_each_transition_once(events):
    up = {"v": True}
    t = {"now": 1000.0}

    def status(h, timeout):
        if not up["v"]:
            raise ConnectionBusy("timed out")
        return _St()

    k, _ = _make(status, clock=lambda: t["now"])
    k.tick()                                   # online
    up["v"] = False
    assert k.tick() == "fail"
    assert k.tick() == "fail"                  # second failure: no duplicate event
    assert [e for e, _ in events] == ["printer.offline"]
    assert k.state.offline_since

    t["now"] = 1600.0
    up["v"] = True
    assert k.tick() == "ok"
    assert [e for e, _ in events] == ["printer.offline", "printer.online"]
    assert events[-1][1]["duration_s"] == 600
    assert k.state.offline_since is None


def test_log_fields_are_allowlisted():
    # printer.offline/online carry idle_s / last_ok / duration_s — all must survive
    # the privacy allowlist, or the whole point (learning the auto-off timer) is lost.
    for f in ("idle_s", "last_ok", "duration_s", "host", "state"):
        assert f in runtime.LOG_FIELD_ALLOWLIST
