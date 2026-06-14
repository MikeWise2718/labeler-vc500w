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
