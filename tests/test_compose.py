"""Tests for the display-list compositor (src/labler/compose.py)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from labler import compose
from labler.config import media_for


def _decode(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_empty_display_list_renders_blank_canvas_at_media_width():
    out = compose.render_display_list({"media_mm": 25, "elements": []}, fmt="PNG")
    img = _decode(out)
    assert img.width == media_for(25).width_px  # 312 px across 25 mm tape
    # auto length with no elements -> minimal height
    assert img.height >= 1


def test_explicit_length_is_respected():
    out = compose.render_display_list(
        {"media_mm": 25, "length_px": 400, "elements": []}, fmt="PNG"
    )
    assert _decode(out).height == 400


def test_auto_length_grows_to_lowest_element():
    dl = {
        "media_mm": 25,
        "length_px": "auto",
        "elements": [
            {"type": "text", "x": 0, "y": 100, "w": 300, "h": 50, "text": "hi", "font_size": 30}
        ],
    }
    img = _decode(compose.render_display_list(dl, fmt="PNG"))
    # bottom of element is 150; canvas adds a small margin
    assert img.height >= 150
    assert img.height < 200


def test_image_element_composited():
    src = Image.new("RGB", (50, 50), "red")
    dl = {
        "media_mm": 25,
        "length_px": 100,
        "elements": [{"type": "image", "x": 0, "y": 0, "w": 312, "h": 100,
                      "src": src, "fit": "stretch"}],
    }
    img = _decode(compose.render_display_list(dl, fmt="PNG")).convert("RGB")
    # top-left should be red-ish (stretched image fills the area)
    r, g, b = img.getpixel((5, 5))
    assert r > 180 and g < 80 and b < 80


def test_text_element_draws_dark_pixels_on_white():
    dl = {
        "media_mm": 25,
        "length_px": 120,
        "background": "white",
        "elements": [{"type": "text", "x": 0, "y": 0, "w": 312, "h": 120,
                      "text": "X", "font_size": 80, "color": "black"}],
    }
    img = _decode(compose.render_display_list(dl, fmt="PNG")).convert("L")
    # at least some near-black pixels from the glyph
    assert img.getextrema()[0] < 60


def test_border_element_frames_the_canvas():
    dl = {
        "media_mm": 25,
        "length_px": 100,
        "background": "white",
        "elements": [{"type": "border", "color": "black", "thickness": 4}],
    }
    img = _decode(compose.render_display_list(dl, fmt="PNG")).convert("L")
    # corners are inside the stroke -> dark
    assert img.getpixel((0, 0)) < 60
    assert img.getpixel((img.width - 1, img.height - 1)) < 60
    # center is untouched white
    assert img.getpixel((img.width // 2, img.height // 2)) > 200


def test_z_order_later_z_paints_on_top():
    bottom = Image.new("RGB", (50, 50), "red")
    top = Image.new("RGB", (50, 50), "blue")
    dl = {
        "media_mm": 25,
        "length_px": 100,
        "elements": [
            {"type": "image", "x": 0, "y": 0, "w": 312, "h": 100, "z": 0,
             "src": bottom, "fit": "stretch"},
            {"type": "image", "x": 0, "y": 0, "w": 312, "h": 100, "z": 5,
             "src": top, "fit": "stretch"},
        ],
    }
    img = _decode(compose.render_display_list(dl, fmt="PNG")).convert("RGB")
    r, g, b = img.getpixel((10, 10))
    assert b > 180 and r < 80  # blue on top wins


def test_unknown_element_type_raises():
    dl = {"media_mm": 25, "elements": [{"type": "wormhole"}]}
    with pytest.raises(ValueError, match="unknown element type"):
        compose.render_display_list(dl, fmt="PNG")


def test_jpeg_output_is_decodable_and_rgb():
    dl = {"media_mm": 25, "length_px": 80, "elements": []}
    img = _decode(compose.render_display_list(dl, fmt="JPEG"))
    assert img.format == "JPEG"
    assert img.mode == "RGB"
