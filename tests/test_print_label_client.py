"""The stdlib print client served at /static/print_label.py (for other projects /
Claude sessions). Its display lists must render correctly through the REAL
compositor, and it must never print without confirmation."""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest
from PIL import Image

from labeler import compose
from labeler.config import media_for

_PATH = Path(__file__).parent.parent / "src/labeler/web/static/print_label.py"
_spec = importlib.util.spec_from_file_location("print_label", _PATH)
pl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pl)


def _img(fmt, size=(1000, 300)):
    b = io.BytesIO()
    im = Image.new("RGB", size, "white")
    im.paste((255, 0, 0), (0, 0, 100, size[1]))      # red band on the LEFT edge
    im.save(b, fmt)
    return b.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "GIF"])
def test_image_size_without_pillow(fmt):
    assert pl.image_size(_img(fmt))[:2] == (1000, 300)


def test_image_size_rejects_unknown():
    with pytest.raises(ValueError):
        pl.image_size(b"BM not supported")


def test_width_px_matches_server_media_table():
    for mm in (9, 12, 19, 25, 50):
        assert pl.width_px(mm) == media_for(mm).width_px


def test_image_fills_tape_width_and_length_matches_aspect():
    dl = pl.image_display_list(_img("PNG"), 25)
    out = Image.open(io.BytesIO(compose.render_display_list(dl, fmt="PNG"))).convert("RGB")
    assert out.width == media_for(25).width_px + compose._RIGHT_BLEED_PX
    assert out.height == round(300 * 312 / 1000)
    assert out.getpixel((5, 5))[0] > 200 and out.getpixel((5, 5))[1] < 60   # red band left


def test_rotate_90_runs_image_along_tape_at_full_resolution():
    dl = pl.image_display_list(_img("PNG"), 25, rotate=90)
    out = Image.open(io.BytesIO(compose.render_display_list(dl, fmt="PNG"))).convert("RGB")
    assert out.height == round(1000 * 312 / 300)     # long axis along the tape
    assert compose.measure_display_list(dl)["length_px"] == out.height
    # clockwise: the image's left band ends up across the TOP of the label
    assert out.getpixel((150, 5))[0] > 200 and out.getpixel((150, 5))[1] < 60


def test_text_display_list_renders():
    dl = pl.text_display_list("Hello\nWorld", 25)
    assert compose.measure_display_list(dl)["length_px"] > 100


def test_refuses_to_print_unconfirmed_when_not_a_tty(monkeypatch, tmp_path, capsys):
    posted = []

    def fake_call(server, path, body=None, **k):
        if path == "/api/device":
            return {"ok": True}
        if path == "/api/settings":
            return {"settings": {"media_width": 25}}
        if path == "/api/measure":
            return {"ok": True, "length_cm": 1.0, "length_in": 0.39}
        posted.append(path)
        return {"ok": True}

    monkeypatch.setattr(pl, "call", fake_call)
    monkeypatch.setattr(pl.sys.stdin, "isatty", lambda: False, raising=False)
    img = tmp_path / "x.png"
    img.write_bytes(_img("PNG"))
    assert pl.main([str(img)]) == 5                   # needs -y
    assert "/api/print" not in posted
    assert "-y" in capsys.readouterr().err


def test_offline_printer_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(pl, "call", lambda *a, **k: {"ok": False, "error": "timed out"})
    assert pl.main(["--status"]) == 3
    assert "OFFLINE" in capsys.readouterr().out


def test_eof_at_prompt_means_needs_confirmation_not_a_crash(monkeypatch, tmp_path):
    # Git Bash on Windows: stdin claims to be a TTY but input() hits EOF. Must exit 5
    # (never print, never traceback).
    posted = []

    def fake_call(server, path, body=None, **k):
        if path == "/api/device":
            return {"ok": True}
        if path == "/api/measure":
            return {"ok": True, "length_cm": 1.0, "length_in": 0.39}
        posted.append(path)
        return {"ok": True}

    def eof(*a):
        raise EOFError

    monkeypatch.setattr(pl, "call", fake_call)
    monkeypatch.setattr(pl.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", eof)
    img = tmp_path / "x.png"
    img.write_bytes(_img("PNG"))
    assert pl.main([str(img), "-mw", "25"]) == 5
    assert "/api/print" not in posted
