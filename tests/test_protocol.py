"""Tests for the TCP protocol layer using a fake socket (no real printer)."""

import socket

import pytest

from labeler import protocol
from labeler.errors import ConnectionBusy, PrinterError


class FakeSocket:
    """One TCP connection. Records bytes sent; returns queued replies on recv.

    The lockless print flow opens several connections (one to send the job, then
    a fresh one per status poll). Each FakeSocket is one connection; FakeNet hands
    them out in order and accumulates all sent bytes across connections so tests
    can assert on the whole exchange.
    """

    def __init__(self, replies, net):
        self._replies = list(replies)
        self._net = net

    def settimeout(self, t):
        pass

    def sendall(self, data):
        self._net.sent.append(data)

    def recv(self, n):
        if self._replies:
            return self._replies.pop(0)
        return b""

    def close(self):
        self.closed = True


class FakeNet:
    """Hands out a FakeSocket per connection from a list of per-connection reply lists."""

    def __init__(self, connections):
        self.sent = []
        self._queue = list(connections)
        self.connect_calls = 0

    def connect(self, *a, **k):
        self.connect_calls += 1
        replies = self._queue.pop(0) if self._queue else []
        return FakeSocket(replies, self)

    def sent_text(self):
        return b"".join(d for d in self.sent if isinstance(d, (bytes, bytearray))).decode("utf-8", "replace")


def _patch_net(monkeypatch, net):
    monkeypatch.setattr(protocol.socket, "create_connection", net.connect)
    monkeypatch.setattr(protocol.time, "sleep", lambda *_: None)


def test_get_status_sends_read_and_parses(monkeypatch):
    reply = b"<status><print_state>IDLE</print_state><print_job_error>NONE</print_job_error></status>"
    net = FakeNet([[reply]])
    _patch_net(monkeypatch, net)
    st = protocol.get_status("printer.local")
    assert st.print_state == "IDLE"
    assert "/status.xml" in net.sent_text()


def test_connect_timeout_raises_connection_busy(monkeypatch):
    def boom(*a, **k):
        raise socket.timeout("timed out")
    monkeypatch.setattr(protocol.socket, "create_connection", boom)
    with pytest.raises(ConnectionBusy):
        protocol.get_status("printer.local")


def test_jpeg_size_parses_real_jpeg():
    # A real JPEG carries its dimensions in the SOF marker; _jpeg_size must read them
    # so the print XML sends the true width/height (autofit=0).
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (312, 96), "white").save(buf, "JPEG")
    assert protocol._jpeg_size(buf.getvalue()) == (312, 96)


def test_jpeg_size_unparseable_returns_zero():
    assert protocol._jpeg_size(b"\xff\xd8notjpeg\xff\xd9") == (0, 0)


def test_print_jpeg_sends_real_dimensions(monkeypatch):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (312, 50), "white").save(buf, "JPEG")
    jpeg = buf.getvalue()
    net = FakeNet([[
        b"<status><code>0</code><comment>ready to receive</comment></status>",
        b"<status><print_state>IDLE</print_state><print_job_stage>SUCCESS</print_job_stage>"
        b"<print_job_error>NONE</print_job_error></status>",
    ]])
    _patch_net(monkeypatch, net)
    protocol.print_jpeg("h", jpeg)
    text = net.sent_text()
    assert "<width>312</width>" in text and "<height>50</height>" in text
    assert "<autofit>0</autofit>" in text


def test_print_jpeg_full_sequence(monkeypatch):
    # ONE held connection: print ack, then status polls on the SAME socket. The
    # brief empty reply right after the JPEG is ignored; then BUSY/PRINTING, then
    # the job returns to IDLE/SUCCESS.
    net = FakeNet([
        [
            b"<status><code>0</code><comment>ready to receive</comment></status>",  # print ack
            b"",  # empty reply right after JPEG -- must be ignored, not treated as done
            b"<status><print_state>BUSY</print_state><print_job_stage>PRINTING</print_job_stage>"
            b"<print_job_error>NONE</print_job_error></status>",
            b"<status><print_state>IDLE</print_state><print_job_stage>SUCCESS</print_job_stage>"
            b"<print_job_error>NONE</print_job_error></status>",
        ],
    ])
    _patch_net(monkeypatch, net)

    stages = []
    st = protocol.print_jpeg("printer.local", b"\xff\xd8JPEGDATA\xff\xd9",
                             mode="vivid", cut="full", on_progress=stages.append)

    text = net.sent_text()
    assert "<mode>vivid</mode>" in text          # print params
    assert "<cutmode>full</cutmode>" in text
    assert "<autofit>0</autofit>" in text        # autofit OFF: we control geometry
    assert "/status.xml" in text                 # polled (on the held connection)
    assert "<lock>" not in text                  # lockless: no lock at all
    assert "job_token" not in text               # no token threaded anywhere
    assert b"\xff\xd8JPEGDATA\xff\xd9" in net.sent  # raw jpeg bytes sent
    assert net.connect_calls == 1                # single held connection, no reconnect
    assert st.ready
    assert "PRINTING" in stages                  # observed real imaging stage
    assert "SUCCESS" in stages


def test_print_jpeg_ignores_leftover_success_stage(monkeypatch):
    # Regression: a back-to-back print starts while the printer still reports the
    # PREVIOUS job's SUCCESS stage. The poll loop must NOT treat that stale SUCCESS
    # as "done" -- it must wait for THIS job to actually start (leave idle) before
    # accepting completion, else it closes mid-PROCESSING and silently aborts.
    net = FakeNet([
        [
            b"<status><code>0</code><comment>ready to receive</comment></status>",  # print ack
            # stale SUCCESS from the prior print -- must be ignored, not "done":
            b"<status><print_state>IDLE</print_state><print_job_stage>SUCCESS</print_job_stage>"
            b"<print_job_error>NONE</print_job_error></status>",
            # now THIS job starts:
            b"<status><print_state>BUSY</print_state><print_job_stage>PRINTING</print_job_stage>"
            b"<print_job_error>NONE</print_job_error></status>",
            # and finishes:
            b"<status><print_state>IDLE</print_state><print_job_stage>SUCCESS</print_job_stage>"
            b"<print_job_error>NONE</print_job_error></status>",
        ],
    ])
    _patch_net(monkeypatch, net)
    stages = []
    st = protocol.print_jpeg("printer.local", b"\xff\xd8x\xff\xd9", on_progress=stages.append)
    assert st.ready
    assert "PRINTING" in stages  # proves it waited for the real job, not the stale SUCCESS


def test_print_jpeg_raises_on_printer_error(monkeypatch):
    net = FakeNet([
        [
            b"<status><comment>ready to receive</comment></status>",  # print ack
            b"<status><print_state>BUSY</print_state><print_job_error>NO MEDIA</print_job_error></status>",
        ],
    ])
    _patch_net(monkeypatch, net)
    with pytest.raises(PrinterError):
        protocol.print_jpeg("printer.local", b"x", job_timeout_s=5)


def test_bad_mode_rejected():
    with pytest.raises(ValueError):
        protocol.print_jpeg("h", b"x", mode="turbo")
