"""TCP :9100 transport for the VC-500W.

Verified sequence (2026-06-14, see specs/design.md):
  1. lock      -> reply carries <job_token>
  2. print XML -> then the raw JPEG bytes (datasize declares the count)
  3. poll status.xml (with job_token) until IDLE & READY
  4. lock cancel (release)

Each XML message is sent as raw UTF-8 with no length prefix; the JPEG bytes follow
the <print> message directly. Only ONE connection to :9100 is possible at a time, so
we open, run the whole job, and close cleanly.
"""

from __future__ import annotations

import re
import socket
import time
from typing import Callable

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


def _status_query(job_token: str | None = None) -> str:
    token = f"<job_token>{job_token}</job_token>\n" if job_token else ""
    return f"{_XML_HEAD}<read>\n<path>/status.xml</path>\n{token}</read>\n"


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
    """Print a JPEG and wait for the job to complete.

    Returns the final Status. Raises PrinterError on a reported print error.
    """
    if mode not in _MODE_PARAMS:
        raise ValueError(f"unknown mode {mode!r} (use 'vivid' or 'normal')")
    if cut not in ("none", "half", "full"):
        raise ValueError(f"unknown cut {cut!r} (use none/half/full)")

    speed, lpi = _MODE_PARAMS[mode]
    datasize = len(jpeg)

    def progress(stage: str) -> None:
        if on_progress:
            on_progress(stage)

    sock = _connect(host, timeout)
    try:
        # 1. lock -> job_token
        progress("locking")
        _send(sock, f"{_XML_HEAD}<lock>\n<op>set</op>\n<page_count>-1</page_count>\n"
                    f"<job_timeout>99</job_timeout>\n</lock>\n")
        reply = _recv(sock)
        m = re.search(r"<job_token>(.*?)</job_token>", reply)
        if not m:
            raise PrinterError(f"printer did not return a job_token; reply was:\n{reply}")
        token = m.group(1)

        # 2. print XML + JPEG bytes
        progress("sending")
        print_xml = (
            f"{_XML_HEAD}<print>\n<mode>{mode}</mode>\n<speed>{speed}</speed>\n"
            f"<lpi>{lpi}</lpi>\n<width>0</width>\n<height>0</height>\n"
            f"<dataformat>jpeg</dataformat>\n<autofit>1</autofit>\n"
            f"<datasize>{datasize}</datasize>\n<cutmode>{cut}</cutmode>\n"
            f"<job_token>{token}</job_token>\n</print>\n"
        )
        _send(sock, print_xml)
        ready = _recv(sock, settle=0.6)
        if "ready to receive" not in ready.lower():
            # Not fatal on all firmwares, but surface it for diagnosis.
            progress("printer not explicitly ready, sending anyway")
        _send(sock, jpeg)

        # 3. poll until IDLE & READY (or error)
        # Give the printer a beat to start the job before polling, so we observe the
        # BUSY/PRINTING stages rather than catching a stale pre-job IDLE.
        time.sleep(1.5)
        deadline = time.time() + job_timeout_s
        final = Status()
        last_stage = None
        saw_busy = False
        while time.time() < deadline:
            _send(sock, _status_query(token))
            st = Status.parse(_recv(sock, settle=0.5))
            if st.print_state is None and st.print_job_stage is None:
                # empty/partial reply — keep polling, don't treat as terminal
                time.sleep(1.0)
                continue
            final = st
            if final.print_job_stage and final.print_job_stage != last_stage:
                progress(final.print_job_stage)
                last_stage = final.print_job_stage
            if final.print_job_error and final.print_job_error != "NONE":
                raise PrinterError(f"print error: {final.print_job_error}")
            if final.print_state == "BUSY":
                saw_busy = True
            # Done when: the job ran and came back idle, OR a terminal SUCCESS stage.
            stage = final.print_job_stage or ""
            if "SUCCESS" in stage or (saw_busy and final.ready):
                break
            time.sleep(2.0)

        # 4. release the lock
        progress("releasing")
        _send(sock, f"{_XML_HEAD}<lock>\n<op>cancel</op>\n<job_token>{token}</job_token>\n</lock>\n")
        _recv(sock)
        return final
    finally:
        sock.close()
