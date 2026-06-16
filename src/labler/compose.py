"""Display-list compositor — stack N elements onto one label canvas.

`render.py` renders ONE primitive per call (one image / text / QR). The web editor
needs to composite several stacked elements onto a single label, so this module
defines a JSON "display list" and renders it to print-ready bytes using the SAME
Pillow helpers as render.py. That shared code path is what makes the editor WYSIWYG:
the PNG preview the browser shows and the JPEG sent to the printer come out of this
one function, so there is no canvas/Pillow mismatch.

Coordinate system
------------------
The across-tape axis is fixed by the media (e.g. 25 mm -> 312 px) and is the canvas
WIDTH. The along-tape (length) axis is continuous and is the canvas HEIGHT; it either
grows to fit content (`length_px: "auto"`) or is pinned to an explicit pixel length.
Every element carries a box in label-pixel coordinates: {x, y, w, h, rotate, z}.

Display-list schema (JSON)
--------------------------
{
  "media_mm":   25,                  # tape width; must be in config.MEDIA
  "length_px":  "auto" | <int>,      # along-tape length; auto = fit content + margin
  "background": "white",             # canvas fill
  "elements": [                      # rendered in ascending z (ties: list order)
    {"type": "image",  "x":0,"y":0,"w":312,"h":200,"rotate":0,"z":0,
     "src": <PIL.Image|path>, "fit": "contain"},
    {"type": "text",   "x":10,"y":210,"w":292,"h":80,"rotate":0,"z":1,
     "text": "Hi", "font": null, "font_size": 48, "color": "black", "align": "left"},
    {"type": "border", "z":99, "color": "black", "thickness": 4}   # whole-label frame
  ]
}

`border` ignores the box (it frames the whole canvas). Deferred element types
(rect/line/polygon/ellipse) will slot in here later; unknown types raise ValueError
so a typo never silently prints a blank label.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .config import media_for
from .render import _apply_rotate, _encode_jpeg, _fit_width, _load_font

# Fallback canvas length (px) when length_px is "auto" and nothing constrains height.
_MIN_LENGTH_PX = 1
# Margin (px) added below the lowest element when auto-sizing the length.
_AUTO_MARGIN_PX = 8


def _resolve_image(src) -> Image.Image:
    """An element's `src` may be a PIL image or a path/file — normalise to RGBA."""
    img = src if isinstance(src, Image.Image) else Image.open(src)
    return img.convert("RGBA")


def _element_bottom(el: dict) -> int:
    """Lowest pixel an element occupies, for auto length sizing. Border = 0 (frame)."""
    if el.get("type") == "border":
        return 0
    return int(el.get("y", 0)) + int(el.get("h", 0))


def _render_image_element(canvas: Image.Image, el: dict) -> None:
    src = el.get("src")
    if src is None:
        # Image element with no source picked yet. Draw a faint placeholder box so
        # the editor shows "an image goes here" instead of crashing or rendering
        # nothing. The placeholder never reaches the printer: by the time a design
        # is printed, an unsourced image either has a file or is removed.
        if el.get("placeholder", True):
            d = ImageDraw.Draw(canvas)
            x, y = int(el.get("x", 0)), int(el.get("y", 0))
            w, h = int(el.get("w", 0)), int(el.get("h", 0))
            if w > 1 and h > 1:
                d.rectangle([x, y, x + w - 1, y + h - 1], outline="#bbbbbb", width=1)
        return
    img = _resolve_image(src)
    w, h = int(el.get("w", img.width)), int(el.get("h", img.height))
    fit = el.get("fit", "contain")
    if fit == "stretch":
        img = img.resize((max(1, w), max(1, h)), Image.LANCZOS)
    else:  # contain / cover: scale to width, preserve aspect (matches render._fit_width)
        scale = w / img.width if img.width else 1.0
        img = img.resize((max(1, w), max(1, round(img.height * scale))), Image.LANCZOS)
    img = _apply_rotate(img, int(el.get("rotate", 0)))
    canvas.alpha_composite(img.convert("RGBA"), (int(el.get("x", 0)), int(el.get("y", 0))))


def _render_text_element(canvas: Image.Image, el: dict) -> None:
    text = el.get("text", "")
    if not text:
        return
    size = int(el.get("font_size", 48))
    fnt = _load_font(el.get("font"), size)
    color = el.get("color", "black")
    align = el.get("align", "left")
    box_w = int(el.get("w", canvas.width))

    # Render the text on its own transparent layer, then rotate + paste. This keeps
    # multi-line alignment correct independent of the canvas.
    layer = Image.new("RGBA", (max(1, box_w), max(1, int(el.get("h", size * 2)))), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    bbox = d.multiline_textbbox((0, 0), text, font=fnt, align=align)
    tw = bbox[2] - bbox[0]
    if align == "center":
        tx = max(0, (box_w - tw) // 2)
    elif align == "right":
        tx = max(0, box_w - tw)
    else:
        tx = 0
    d.multiline_text((tx, -bbox[1]), text, fill=color, font=fnt, align=align)
    layer = _apply_rotate(layer, int(el.get("rotate", 0)))
    canvas.alpha_composite(layer, (int(el.get("x", 0)), int(el.get("y", 0))))


def _render_border_element(canvas: Image.Image, el: dict) -> None:
    color = el.get("color", "black")
    t = max(1, int(el.get("thickness", 2)))
    d = ImageDraw.Draw(canvas)
    # Inset rectangle so the full stroke stays on-canvas.
    d.rectangle([0, 0, canvas.width - 1, canvas.height - 1], outline=color, width=t)


_RENDERERS = {
    "image": _render_image_element,
    "text": _render_text_element,
    "border": _render_border_element,
}


def render_display_list(dl: dict, *, fmt: str = "JPEG") -> bytes:
    """Composite a display-list to print-ready bytes.

    fmt="JPEG" (default) is what goes to the printer — flattened on white with 4:4:4
    subsampling for color fidelity (via render._encode_jpeg). fmt="PNG" is for the
    editor preview (keeps it cheap and lossless; alpha flattened on the background).
    """
    media = media_for(int(dl.get("media_mm", 25)))
    width = media.width_px

    elements = sorted(
        dl.get("elements", []),
        key=lambda e: (int(e.get("z", 0)),),
    )

    length = dl.get("length_px", "auto")
    if length == "auto":
        bottom = max((_element_bottom(e) for e in elements), default=0)
        height = max(_MIN_LENGTH_PX, bottom + _AUTO_MARGIN_PX)
    else:
        height = max(_MIN_LENGTH_PX, int(length))

    background = dl.get("background", "white")
    canvas = Image.new("RGBA", (width, height), background)

    for el in elements:
        etype = el.get("type")
        renderer = _RENDERERS.get(etype)
        if renderer is None:
            raise ValueError(f"unknown element type {etype!r}")
        renderer(canvas, el)

    if fmt.upper() == "PNG":
        import io

        flat = Image.new("RGB", canvas.size, background)
        flat.paste(canvas, mask=canvas.split()[-1])
        buf = io.BytesIO()
        flat.save(buf, "PNG")
        return buf.getvalue()
    return _encode_jpeg(canvas)
