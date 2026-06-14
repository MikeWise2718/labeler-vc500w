"""Tests for the TCP protocol layer using a fake socket (no real printer)."""

import socket

import pytest

from labler import protocol
from labler.errors import ConnectionBusy, PrinterError


class FakeSocket:
    """Records everything sent; returns queued replies on recv."""

    def __init__(self, replies):
        self.sent = []
        self._replies = list(replies)

    def settimeout(self, t):
        pass

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, n):
        if self._replies:
            return self._replies.pop(0)
        return b""

    def close(self):
        self.closed = True

    def sent_text(self):
        return b"".join(d for d in self.sent if isinstance(d, (bytes, bytearray))).decode("utf-8", "replace")


def _patch_connect(monkeypatch, fake):
    monkeypatch.setattr(protocol.socket, "create_connection", lambda *a, **k: fake)
    monkeypatch.setattr(protocol.time, "sleep", lambda *_: None)


def test_get_status_sends_read_and_parses(monkeypatch):
    reply = b"<status><print_state>IDLE</print_state><print_job_error>NONE</print_job_error></status>"
    fake = FakeSocket([reply])
    _patch_connect(monkeypatch, fake)
    st = protocol.get_status("printer.local")
    assert st.print_state == "IDLE"
    assert "/status.xml" in fake.sent_text()


def test_connect_timeout_raises_connection_busy(monkeypatch):
    def boom(*a, **k):
        raise socket.timeout("timed out")
    monkeypatch.setattr(protocol.socket, "create_connection", boom)
    with pytest.raises(ConnectionBusy):
        protocol.get_status("printer.local")


def test_print_jpeg_full_sequence(monkeypatch):
    replies = [
        b"<status><job_token>TOK123</job_token><code>0</code></status>",   # lock
        b"<status><code>0</code><comment>ready to receive</comment></status>",  # print ack
        b"<status><print_state>IDLE</print_state><print_job_stage>SUCCESS</print_job_stage>"
        b"<print_job_error>NONE</print_job_error></status>",              # poll -> done
        b"<status><comment>lock cancel successful</comment></status>",    # release
    ]
    fake = FakeSocket(replies)
    _patch_connect(monkeypatch, fake)

    stages = []
    st = protocol.print_jpeg("printer.local", b"\xff\xd8JPEGDATA\xff\xd9",
                             mode="vivid", cut="full", on_progress=stages.append)

    text = fake.sent_text()
    assert "<op>set</op>" in text            # lock
    assert "<mode>vivid</mode>" in text       # print params
    assert "<cutmode>full</cutmode>" in text
    assert "TOK123" in text                   # token threaded through
    assert "<op>cancel</op>" in text          # release
    assert b"\xff\xd8JPEGDATA\xff\xd9" in fake.sent  # raw jpeg bytes sent
    assert st.ready
    assert "SUCCESS" in stages


def test_print_jpeg_raises_on_printer_error(monkeypatch):
    replies = [
        b"<status><job_token>TOK</job_token></status>",
        b"<status><comment>ready to receive</comment></status>",
        b"<status><print_state>BUSY</print_state><print_job_error>NO MEDIA</print_job_error></status>",
    ]
    fake = FakeSocket(replies)
    _patch_connect(monkeypatch, fake)
    with pytest.raises(PrinterError):
        protocol.print_jpeg("printer.local", b"x", job_timeout_s=5)


def test_bad_mode_rejected():
    with pytest.raises(ValueError):
        protocol.print_jpeg("h", b"x", mode="turbo")
