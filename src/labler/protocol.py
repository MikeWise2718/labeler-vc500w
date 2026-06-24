"""TCP :9100 transport for the VC-500W.

Lockless print sequence (see docs/print-sequence-finding.md):
  1. connect, send <print> XML, then the raw JPEG bytes (datasize declares the count)
  2. CLOSE the socket -- this is what triggers the printer's cut/eject cycle. The
     VC-500W has no explicit eject/cut command; the cut fires on connection close.
  3. RECONNECT and poll status.xml until the job finishes (idle / SUCCESS / error)

We deliberately do NOT use the <lock>/<job_token> mechanism. It exists to reserve
the printer for multi-page batch jobs against concurrent clients -- but the VC-500W
already allows only ONE :9100 connection at a time, so for a single-label tool the
lock is redundant and, worse, an unreleased lock (on any error) wedges the printer in
BUSY/PRINTING until a power-cycle. Three reverse-engineering sources independently
call the lock "error-prone / usually unnecessary." See docs/print-sequence-finding.md.

Each XML message is sent as raw UTF-8 with no length prefix; the JPEG bytes follow
the <print> message directly.
"""

from __future__ import annotations

import os
import socket
import time
from typing import Callable

_DEBUG = bool(os.environ.get("LABLER_DEBUG"))

from .config import PORT
from .errors import ConnectionBusy, PrinterError
from .status import Status

ProgressCb = Callable[[str], None]

_XML_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n'

# vivid = speed 0 / lpi 317; normal = speed 1 / lpi 264 (from the reverse-engineered protocol)
_MODE_PARAMS = {
    "vivid": (0, 317),
    "normal": (1, 264),
}


def _connect(host: str, timeout: float) -> socket.socket:
    try:
        s = socket.create_connection((host, PORT), timeout=timeout)
    except socket.timeout as e:
        raise ConnectionBusy(
            f"timed out connecting to {host}:{PORT} — the printer may be asleep, or "
            f"another app (e.g. Brother's software) is holding its single connection slot. "
            f"Close other apps and retry."
        ) from e
    except OSError as e:
        raise ConnectionBusy(f"could not connect to {host}:{PORT}: {e}") from e
    s.settimeout(timeout)
    return s


def _recv(sock: socket.socket, settle: float = 0.4) -> str:
    """Read whatever the printer sends back for one request."""
    time.sleep(settle)
    chunks: list[bytes] = []
    try:
        while True:
            data = sock.recv(8192)
            if not data:
                break
            chunks.append(data)
            if len(data) < 8192:
                break
    except socket.timeout:
        pass
    return b"".join(chunks).decode("utf-8", errors="replace")


def _send(sock: socket.socket, payload: bytes | str) -> None:
    sock.sendall(payload if isinstance(payload, bytes) else payload.encode("utf-8"))


def _status_query() -> str:
    return f"{_XML_HEAD}<read>\n<path>/status.xml</path>\n</read>\n"


def _jpeg_size(jpeg: bytes) -> tuple[int, int]:
    """(width, height) of a JPEG, parsed from its SOF marker — no PIL dependency here.

    Falls back to (0, 0) if it can't be parsed (then the caller's autofit=0 width/height
    are 0, which the firmware treats as 'use the embedded image size')."""
    i = 2  # skip SOI (FFD8)
    n = len(jpeg)
    try:
        while i + 9 < n:
            if jpeg[i] != 0xFF:
                i += 1
                continue
            marker = jpeg[i + 1]
            # SOF0..SOF15 carry the frame dimensions (skip DHT/DAC/RST/SOI/EOI/SOS).
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h = (jpeg[i + 5] << 8) | jpeg[i + 6]
                w = (jpeg[i + 7] << 8) | jpeg[i + 8]
                return w, h
            seg_len = (jpeg[i + 2] << 8) | jpeg[i + 3]
            i += 2 + seg_len
    except IndexError:
        pass
    return 0, 0


def get_status(host: str, *, timeout: float = 8.0) -> Status:
    """Query the printer once and return its parsed Status."""
    sock = _connect(host, timeout)
    try:
        _send(sock, _status_query())
        return Status.parse(_recv(sock))
    finally:
        sock.close()


def print_jpeg(
    host: str,
    jpeg: bytes,
    *,
    mode: str = "vivid",
    cut: str = "full",
    on_progress: ProgressCb | None = None,
    timeout: float = 8.0,
    job_timeout_s: float = 60.0,
) -> Status:
    """Print a JPEG and wait for the job to complete (lockless).

    Sequence (verified on our firmware 2026-06-15, see docs/print-sequence-finding.md):
      connect -> <print> XML -> JPEG bytes -> HOLD the connection open and poll
      status on the SAME socket until imaging completes -> THEN close. Closing is
      what triggers the cut, but it MUST happen only after the job finishes.

    The job must NOT be ended early. Closing the socket (or releasing a lock)
    before imaging completes aborts the job: the printer ejects a blank leader,
    reports EJECT JAM, and can wedge in BUSY/PRINTING. So we hold the connection
    through PROCESSING -> PREPARING PRINT -> PREHEAT -> PRINTING -> EJECTING ->
    SUCCESS, ignoring the brief empty status reply right after the JPEG, and only
    close in the finally once we've seen the job finish (or genuinely error/time out).

    Returns the final Status. Raises PrinterError on a reported print error.
    """
    if mode not in _MODE_PARAMS:
        raise ValueError(f"unknown mode {mode!r} (use 'vivid' or 'normal')")
    if cut not in ("none", "half", "full"):
        raise ValueError(f"unknown cut {cut!r} (use none/half/full)")

    speed, lpi = _MODE_PARAMS[mode]
    datasize = len(jpeg)

    # Read the JPEG's real pixel size so we can tell the printer to print it AS-IS
    # (autofit=0). With autofit=1 the firmware rescales/reorients the image — a
    # landscape label then prints stretched down the tape, consuming far more length
    # than the design (e.g. a 0.7cm design ate 10.6cm). We render at the exact tape
    # geometry already, so autofit must be off for measure==print to hold.
    img_w, img_h = _jpeg_size(jpeg)

    def progress(stage: str) -> None:
        if on_progress:
            on_progress(stage)

    progress("sending")
    sock = _connect(host, timeout)
    try:
        # 1. print XML (no lock, no job_token) + raw JPEG bytes. autofit=0 with the
        #    real width/height -> printer prints our pixels 1:1 across the tape.
        print_xml = (
            f"{_XML_HEAD}<print>\n<mode>{mode}</mode>\n<speed>{speed}</speed>\n"
            f"<lpi>{lpi}</lpi>\n<width>{img_w}</width>\n<height>{img_h}</height>\n"
            f"<dataformat>jpeg</dataformat>\n<autofit>0</autofit>\n"
            f"<datasize>{datasize}</datasize>\n<cutmode>{cut}</cutmode>\n</print>\n"
        )
        _send(sock, print_xml)
        ready = _recv(sock, settle=0.6)
        if _DEBUG:
            print(f"[print-ack] {ready!r}", flush=True)
        if "ready to receive" not in ready.lower():
            # Not fatal on all firmwares, but surface it for diagnosis.
            progress("printer not explicitly ready, sending anyway")
        _send(sock, jpeg)

        # 2. HOLD this connection and poll until the job truly finishes. Give the
        #    printer a clear beat to consume the JPEG and start the job before we
        #    query -- polling too eagerly on this single-slot firmware races the
        #    job start and can abort it. Never end early.
        progress("printing")
        time.sleep(2.5)
        deadline = time.time() + job_timeout_s
        final = Status()
        last_stage = None
        saw_busy = False
        while time.time() < deadline:
            # The held socket may be reset by the printer as imaging begins, and
            # the printer briefly refuses new connections mid-commit. Either way we
            # reconnect and keep polling -- a dropped/refused connection is NEVER
            # treated as job end.
            if sock is None:
                try:
                    sock = _connect(host, timeout)
                except ConnectionBusy:
                    time.sleep(1.0)
                    continue
            try:
                _send(sock, _status_query())
                raw = _recv(sock, settle=0.5)
                if _DEBUG:
                    print(f"[poll] {raw!r}", flush=True)
                st = Status.parse(raw)
            except OSError:
                try:
                    sock.close()
                except OSError:
                    pass
                sock = None
                time.sleep(1.0)
                continue

            if st.print_state is None and st.print_job_stage is None:
                # empty/partial reply -- keep polling, don't treat as terminal
                time.sleep(1.0)
                continue
            final = st
            if final.print_job_stage and final.print_job_stage != last_stage:
                progress(final.print_job_stage)
                last_stage = final.print_job_stage
            if final.print_job_error and final.print_job_error != "NONE":
                raise PrinterError(f"print error: {final.print_job_error}")
            # "The job has started" = state left IDLE, or the stage is an active
            # (non-idle) one. Active stages seen: PROCESSING, PREPARING PRINT,
            # PREHEAT, PRINTING, EJECTING. Idle stages: READY*, SUCCESS, IDLE.
            stage_now = final.print_job_stage or ""
            active_stage = bool(stage_now) and not any(
                k in stage_now for k in Status._IDLE_STAGES
            )
            if final.print_state in ("BUSY", "ERROR") or active_stage:
                saw_busy = True
            # Completion requires that THIS job actually started first (saw_busy).
            # Without that guard, the leftover SUCCESS/READY stage from the PREVIOUS
            # print satisfies the check on the very first poll -- so we'd close mid
            # PROCESSING and silently abort the new job (no tape, no error). Only
            # treat SUCCESS/idle as done once we've observed the job leave idle.
            if saw_busy and ("SUCCESS" in stage_now or final.ready):
                break
            time.sleep(2.0)

        return final
    finally:
        # Close only here -- after the job has finished polling. The close
        # triggers the cut; doing it earlier aborts the print. (sock may be None
        # if the last reconnect attempt was still pending.)
        if sock is not None:
            sock.close()
