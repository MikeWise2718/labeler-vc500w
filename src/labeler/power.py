"""Remote power-cycle for a wedged VC-500W, via a Shelly smart outlet.

WHY: the printer wedges in ``BUSY/PRINTING`` with no tape moving if a client
releases the lock mid-job, and **only a power-cycle clears it** (see CLAUDE.md
GOTCHAs). While the printer sat on a desk that meant reaching over and pulling the
plug. Centrally located in the basement it blocks *everyone* and nobody is next to
it — so recovery has to be an API call.

The Shelly Power Strip 4 Gen4 speaks unauthenticated HTTP RPC on the LAN:
``http://<ip>/rpc/Switch.Set?id=<0-3>&on=<true|false>``. Device inventory and full
API notes live in ``D:\\hw\\shelly/CLAUDE.md``.

SAFETY: cutting mains power to a printer mid-print is destructive — it can leave a
partially-fed label in the mechanism. Nothing here fires automatically. The web
route requires an explicit confirmation from the user, and ``power_cycle()``
refuses to run unless the caller has already decided the printer is wedged.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

from .errors import LabelerError

# How long to leave the outlet off. The VC-500W needs its capacitors to drain for
# the power-cycle to actually reset the controller; a brief blip can leave it in
# the same wedged state. 8 s is conservative but this is a rare recovery path.
OFF_SECONDS = 8.0

# Per-request timeout talking to the Shelly (it is on the LAN and answers fast).
HTTP_TIMEOUT = 5.0


def _rpc(host: str, method: str, params: str = "") -> str:
    """One Shelly RPC GET. Returns the raw body; raises LabelerError on failure."""
    url = f"http://{host}/rpc/{method}"
    if params:
        url += f"?{params}"
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise LabelerError(f"Shelly at {host} unreachable: {e}") from e


def outlet_state(host: str, outlet: int) -> bool:
    """True if the outlet is currently energised."""
    body = _rpc(host, "Switch.GetStatus", f"id={int(outlet)}")
    # Avoid a json import round-trip for one boolean; the field is unambiguous.
    return '"output":true' in body.replace(" ", "").lower()


def set_outlet(host: str, outlet: int, on: bool) -> None:
    _rpc(host, "Switch.Set", f"id={int(outlet)}&on={'true' if on else 'false'}")


def power_cycle(host: str, outlet: int, *, off_seconds: float = OFF_SECONDS,
                sleep=time.sleep) -> dict:
    """Turn the outlet off, wait, turn it back on.

    `sleep` is injectable so tests do not actually wait 8 seconds.

    Returns a dict describing what happened. Raises LabelerError if the Shelly is
    unreachable — the caller should surface that rather than pretend it worked,
    since a failed power-cycle leaves the printer just as wedged as before.
    """
    was_on = outlet_state(host, outlet)
    set_outlet(host, outlet, False)
    sleep(off_seconds)
    set_outlet(host, outlet, True)
    return {
        "host": host,
        "outlet": int(outlet),
        "was_on": was_on,
        "off_seconds": off_seconds,
    }


def looks_wedged(samples: list) -> bool:
    """True if a series of status samples shows the wedge fingerprint.

    The fingerprint (CLAUDE.md): ``print_state=BUSY`` / ``stage=PRINTING`` while
    ``remain`` does NOT move. A real print advances the stage or consumes tape; a
    wedge shows the "printing" pattern frozen. Needs >= 2 samples taken a few
    seconds apart, else a healthy print looks identical to a stuck one.
    """
    if len(samples) < 2:
        return False
    busy = all(getattr(s, "print_state", None) in ("BUSY", "PRINTING")
               for s in samples)
    if not busy:
        return False
    remains = [getattr(s, "remain", None) for s in samples]
    if any(r is None for r in remains):
        return False
    stages = {getattr(s, "print_job_stage", None) for s in samples}
    # frozen tape AND frozen stage = wedged
    return len(set(remains)) == 1 and len(stages) == 1
