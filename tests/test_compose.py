"""Tests for the display-list compositor (src/labeler/compose.py)."""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from labeler import compose
from labeler.config import media_for


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


def _one(text, **over):
    el = {"type": "text", "x": 0, "y": 8, "w": 312, "h": 80,
          "text": text, "font": "Arial", "font_size": 40, "align": "center"}
    el.update(over)
    return {"media_mm": 25, "length_px": "auto", "elements": [el]}


def test_multiline_text_grows_canvas():
    one = _decode(compose.render_display_list(_one("One"), fmt="PNG")).height
    three = _decode(compose.render_display_list(_one("One\nTwo\nThree"), fmt="PNG")).height
    assert three > one  # more lines -> taller canvas, not clipped to box h


def test_multiline_not_clipped_below_box_h():
    # 5 lines at size 40 far exceed h=80; the element bottom must reflect real text
    # height so auto-length includes all of it.
    dl = _one("a\nb\nc\nd\ne", h=80)
    img = _decode(compose.render_display_list(dl, fmt="PNG"))
    assert img.height > 8 + 80  # grew past y + box h
    # render and measure agree (preview == print length)
    assert compose.measure_display_list(dl)["length_px"] == img.height


def test_single_line_unchanged_by_multiline_support():
    # explicit length is still honored exactly (multiline logic only affects auto)
    dl = _one("Hi")
    dl["length_px"] = 200
    assert _decode(compose.render_display_list(dl, fmt="PNG")).height == 200


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


def test_image_element_without_source_does_not_crash():
    # An image element added in the editor before a file is picked has no src/src_id.
    # It must render a placeholder, not raise KeyError.
    dl = {
        "media_mm": 25,
        "length_px": 100,
        "elements": [{"type": "image", "x": 0, "y": 0, "w": 100, "h": 80, "z": 0}],
    }
    out = compose.render_display_list(dl, fmt="PNG")
    assert _decode(out).width == media_for(25).width_px  # rendered fine


def test_unknown_element_type_raises():
    dl = {"media_mm": 25, "elements": [{"type": "wormhole"}]}
    with pytest.raises(ValueError, match="unknown element type"):
        compose.render_display_list(dl, fmt="PNG")


def test_rotate_90_swaps_axes_and_refits_to_media_width():
    # A tall design (312 wide x 600 long) rotated 90 should still be 312 wide (the
    # printer's fixed across-tape width) but now SHORT in length.
    dl = {"media_mm": 25, "length_px": 600, "rotate": 90, "elements": []}
    img = _decode(compose.render_display_list(dl, fmt="PNG"))
    assert img.width == media_for(25).width_px      # 312, re-fit to media width
    assert img.height < 600                          # the long axis became short


def test_measure_reports_length_in_cm_and_inches():
    # 600 px length at 12.48 px/mm = 48.08 mm = ~4.8 cm = ~1.89 in
    dl = {"media_mm": 25, "length_px": 600, "elements": []}
    m = compose.measure_display_list(dl)
    assert m["width_px"] == media_for(25).width_px
    assert m["length_px"] == 600
    assert m["length_cm"] == pytest.approx(4.8, abs=0.2)
    assert m["length_in"] == pytest.approx(1.89, abs=0.05)


def test_measure_matches_rendered_dimensions_after_rotation():
    dl = {"media_mm": 25, "length_px": 600, "rotate": 90, "elements": []}
    m = compose.measure_display_list(dl)
    img = _decode(compose.render_display_list(dl, fmt="PNG"))
    assert (m["width_px"], m["length_px"]) == (img.width, img.height)


def test_jpeg_output_is_decodable_and_rgb():
    dl = {"media_mm": 25, "length_px": 80, "elements": []}
    img = _decode(compose.render_display_list(dl, fmt="JPEG"))
    assert img.format == "JPEG"
    assert img.mode == "RGB"


# ---- inlined images (data URIs) --------------------------------------------
# Bitmaps are inlined rather than uploaded so that label content never reaches
# the shared server (specs/central-deployment.md). compose decodes them per render.

def _png_data_uri(w=20, h=10, color="red") -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_data_uri_decodes_to_image():
    img = compose._resolve_image(_png_data_uri(20, 10))
    assert img.size == (20, 10)
    assert img.mode == "RGBA"


def test_data_uri_renders_same_as_file_path(tmp_path):
    """A data URI and the identical bitmap on disk must produce the same label.

    This is the guarantee that removing /api/assets changed nothing visually.
    """
    p = tmp_path / "x.png"
    Image.new("RGB", (20, 10), "red").save(p, "PNG")
    box = {"type": "image", "x": 0, "y": 0, "w": 40, "h": 20, "fit": "contain"}
    from_path = compose.render_display_list(
        {"media_mm": 25, "length_px": 60, "elements": [{**box, "src": str(p)}]}, fmt="PNG")
    from_uri = compose.render_display_list(
        {"media_mm": 25, "length_px": 60, "elements": [{**box, "src": _png_data_uri(20, 10)}]},
        fmt="PNG")
    assert from_path == from_uri


@pytest.mark.parametrize("bad", [
    "data:image/png;base64,",                 # no payload
    "data:image/png;base64,!!!!",             # not base64
    "data:image/png,rawnotbase64",            # missing ;base64
    "data:image/png;base64,QQ==",             # valid base64, not an image
])
def test_malformed_data_uri_raises(bad):
    with pytest.raises(Exception):
        compose._resolve_image(bad)


def test_oversized_data_uri_rejected():
    payload = "A" * (compose.MAX_DATA_URI_BYTES + 1)
    with pytest.raises(ValueError, match="too large"):
        compose._resolve_image("data:image/png;base64," + payload)


def test_plain_path_still_works(tmp_path):
    """Back-compat: a filesystem path is still a valid src (used by the CLI)."""
    p = tmp_path / "y.png"
    Image.new("RGB", (8, 8), "blue").save(p, "PNG")
    assert compose._resolve_image(str(p)).size == (8, 8)


def test_pil_image_still_works():
    im = Image.new("RGB", (6, 6), "green")
    assert compose._resolve_image(im).size == (6, 6)
