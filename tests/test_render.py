"""Tests for the render pipeline — sizes, rotation, fit, encoding."""

import io

import pytest
from PIL import Image

from labler import render
from labler.config import media_for


def _size(jpeg: bytes):
    return Image.open(io.BytesIO(jpeg)).size


def test_image_width_matches_25mm():
    src = Image.new("RGB", (100, 50), "red")
    jpeg = render.render_image(src, media_mm=25)
    w, h = _size(jpeg)
    assert w == media_for(25).width_px == 312
    assert h == round(50 * (312 / 100))  # aspect preserved


def test_image_width_matches_50mm():
    src = Image.new("RGB", (200, 200), "blue")
    jpeg = render.render_image(src, media_mm=50)
    assert _size(jpeg)[0] == media_for(50).width_px == 624


def test_rotate_90_swaps_aspect():
    src = Image.new("RGB", (400, 100), "green")  # wide
    jpeg = render.render_image(src, media_mm=25, rotate=90)
    w, h = _size(jpeg)
    assert w == 312
    assert h > w  # became tall after 90deg rotation + width-fit


def test_crop_then_fit():
    src = Image.new("RGB", (200, 200), "white")
    jpeg = render.render_image(src, media_mm=25, crop=(0, 0, 100, 50))
    w, h = _size(jpeg)
    assert w == 312
    assert h == round(50 * (312 / 100))


def test_rgba_flattened_to_rgb():
    src = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
    jpeg = render.render_image(src, media_mm=25)
    assert Image.open(io.BytesIO(jpeg)).mode == "RGB"


def test_text_renders_at_width():
    jpeg = render.render_text("Test", media_mm=25)
    assert _size(jpeg)[0] == 312


def test_qr_is_square_width():
    jpeg = render.render_qr("hello", media_mm=25)
    assert _size(jpeg)[0] == 312


def test_unknown_media_raises():
    with pytest.raises(ValueError):
        render.render_text("x", media_mm=7)


def test_bad_rotate_raises():
    src = Image.new("RGB", (10, 10), "white")
    with pytest.raises(ValueError):
        render.render_image(src, media_mm=25, rotate=45)


# ---- font family + bold/italic resolution -----------------------------------

def _font_path(fnt):
    # FreeTypeFont has .path; the bitmap fallback (load_default) does not.
    return getattr(fnt, "path", None)


def test_load_font_never_crashes_on_non_string():
    # Regression guard: callers must be able to pass odd values without an
    # exception (the editor once fed numeric props through the color path).
    assert render._load_font(None, 16) is not None
    assert render._load_font("does-not-exist.ttf", 16) is not None


def test_font_family_bold_italic_pick_distinct_files():
    # On a box with Arial installed, each style maps to a different file. If Arial
    # isn't present, skip — the resolver still degrades gracefully.
    fam = render.FONT_FAMILIES["Arial"]
    reg = render._try_truetype(fam["r"], 16)
    if reg is None:
        pytest.skip("Arial not installed on this host")
    paths = {
        (b, i): _font_path(render._load_font("Arial", 24, bold=b, italic=i))
        for b in (False, True) for i in (False, True)
    }
    # All four faces resolve to distinct files when the family is fully installed.
    assert len(set(paths.values())) == 4


def test_font_resolver_degrades_when_style_missing():
    # DejaVu Sans is the cross-platform fallback family; even if its exact files
    # aren't present, asking for bold/italic must still return *some* usable font.
    f = render._load_font("DejaVu Sans", 20, bold=True, italic=True)
    assert f is not None


def test_legacy_file_to_family_reverse_map():
    m = render.FONT_FILE_TO_FAMILY
    assert m["arial.ttf"] == {"family": "Arial", "bold": False, "italic": False}
    assert m["arialbd.ttf"] == {"family": "Arial", "bold": True, "italic": False}
    assert m["ariali.ttf"] == {"family": "Arial", "bold": False, "italic": True}
    assert m["arialbi.ttf"] == {"family": "Arial", "bold": True, "italic": True}
