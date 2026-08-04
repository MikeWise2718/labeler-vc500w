"""Render image / text / QR to JPEG bytes at the tape's printable width.

The wire protocol is dumb: everything becomes a JPEG sized to the across-tape pixel
width for the chosen media. This module is the shared "label design" core used by both
the CLI and the future Flask UI.

Orientation: the across-tape axis is fixed by the media (e.g. 25 mm -> 312 px); the
along-tape (length) axis is continuous. We render with the media width as the image
WIDTH and let height grow with content.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from .config import media_for

JPEG_QUALITY = 95


def _encode_jpeg(img: Image.Image, *, no_subsampling: bool = True) -> bytes:
    """Flatten to RGB on white and encode JPEG.

    no_subsampling=True (4:4:4) preserves saturated reds/yellows that 4:2:0 degrades.
    """
    if img.mode != "RGB":
        bg = Image.new("RGB", img.size, "white")
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1])
        else:
            bg.paste(img.convert("RGB"))
        img = bg
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY, subsampling=0 if no_subsampling else 2)
    return buf.getvalue()


def _apply_rotate(img: Image.Image, rotate: int) -> Image.Image:
    if rotate == 0:
        return img
    if rotate not in (90, 180, 270):
        raise ValueError("rotate must be 0, 90, 180, or 270")
    # PIL rotates counter-clockwise; expand to keep the whole image.
    return img.rotate(-rotate, expand=True)


def _apply_crop(img: Image.Image, crop: tuple[int, int, int, int] | None) -> Image.Image:
    if not crop:
        return img
    x, y, w, h = crop
    return img.crop((x, y, x + w, y + h))


def _fit_width(img: Image.Image, target_w: int, fit: str = "contain", bg: str = "white") -> Image.Image:
    """Scale so the image width == target_w (the tape width)."""
    if img.width == 0:
        raise ValueError("empty image")
    scale = target_w / img.width
    new_h = max(1, round(img.height * scale))
    resized = img.resize((target_w, new_h), Image.LANCZOS)
    if fit in ("contain", "cover", "stretch"):
        return resized
    raise ValueError(f"unknown fit {fit!r}")


def render_image(
    src,
    *,
    media_mm: int = 25,
    rotate: int = 0,
    crop: tuple[int, int, int, int] | None = None,
    fit: str = "contain",
    bg: str = "white",
    no_subsampling: bool = True,
) -> bytes:
    """Render an image source (path or PIL.Image) to print-ready JPEG bytes."""
    media = media_for(media_mm)
    img = src if isinstance(src, Image.Image) else Image.open(src)
    img = _apply_crop(img, crop)
    img = _apply_rotate(img, rotate)
    img = _fit_width(img, media.width_px, fit=fit, bg=bg)
    return _encode_jpeg(img, no_subsampling=no_subsampling)


# Font families mapped to their (regular, bold, italic, bold-italic) files. Lets the
# UI offer a family name + Bold/Italic toggles instead of raw .ttf filenames. Files
# that don't exist on a given box are skipped at resolve time, with graceful fallback
# (e.g. DejaVu has bold/italic/bold-oblique under different names; cross-platform).
FONT_FAMILIES: dict[str, dict[str, str]] = {
    # name:           regular            bold               italic             bold-italic
    "Arial":          {"r": "arial.ttf",    "b": "arialbd.ttf",  "i": "ariali.ttf",   "bi": "arialbi.ttf"},
    "Times New Roman":{"r": "times.ttf",    "b": "timesbd.ttf",  "i": "timesi.ttf",   "bi": "timesbi.ttf"},
    "Courier New":    {"r": "cour.ttf",     "b": "courbd.ttf",   "i": "couri.ttf",    "bi": "courbi.ttf"},
    "Verdana":        {"r": "verdana.ttf",  "b": "verdanab.ttf", "i": "verdanai.ttf", "bi": "verdanaz.ttf"},
    "Calibri":        {"r": "calibri.ttf",  "b": "calibrib.ttf", "i": "calibrii.ttf", "bi": "calibriz.ttf"},
    "Segoe UI":       {"r": "segoeui.ttf",  "b": "segoeuib.ttf", "i": "segoeuii.ttf", "bi": "segoeuiz.ttf"},
    "Comic Sans MS":  {"r": "comic.ttf",    "b": "comicbd.ttf",  "i": "comici.ttf",   "bi": "comicz.ttf"},
    # cross-platform fallback family (Linux/macOS): DejaVu ships these names
    "DejaVu Sans":    {"r": "DejaVuSans.ttf", "b": "DejaVuSans-Bold.ttf",
                       "i": "DejaVuSans-Oblique.ttf", "bi": "DejaVuSans-BoldOblique.ttf"},
}


# Reverse map: any style file -> {family, bold, italic}. Lets the UI migrate
# designs/settings that stored a raw filename (e.g. "arialbd.ttf") back to the
# family + style flags ("Arial", bold=true).
_STYLE_FLAGS = {"r": (False, False), "b": (True, False), "i": (False, True), "bi": (True, True)}
FONT_FILE_TO_FAMILY: dict[str, dict] = {
    fname: {"family": fam, "bold": _STYLE_FLAGS[style][0], "italic": _STYLE_FLAGS[style][1]}
    for fam, files in FONT_FAMILIES.items()
    for style, fname in files.items()
}


def _try_truetype(name: str | None, size: int):
    if not name:
        return None
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return None


def _load_font(
    font: str | None,
    size: int,
    *,
    bold: bool = False,
    italic: bool = False,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Resolve a font to a PIL font object, honoring bold/italic.

    `font` may be a family name from FONT_FAMILIES (e.g. "Arial") or a raw .ttf
    filename (back-compat). When it's a known family and bold/italic are set, the
    matching style file is loaded; if that exact style is missing, we degrade
    gracefully (bold-italic -> bold -> italic -> regular) rather than fail. Raw
    filenames are tried as-is first.
    """
    style = ("bi" if bold and italic else "b" if bold else "i" if italic else "r")

    # Known family -> pick the style file, with graceful degradation.
    fam = FONT_FAMILIES.get(font) if font else None
    if fam:
        # preference order so a missing exact style still gets *some* emphasis
        order = {
            "bi": ["bi", "b", "i", "r"],
            "b": ["b", "bi", "r"],
            "i": ["i", "bi", "r"],
            "r": ["r"],
        }[style]
        for s in order:
            f = _try_truetype(fam.get(s), size)
            if f:
                return f

    # Raw filename (or unknown family string): try it directly first.
    f = _try_truetype(font, size)
    if f:
        return f

    # Last-ditch generic fallbacks, honoring style where the file names are known.
    generic = {
        "bi": ["arialbi.ttf", "DejaVuSans-BoldOblique.ttf"],
        "b": ["arialbd.ttf", "DejaVuSans-Bold.ttf"],
        "i": ["ariali.ttf", "DejaVuSans-Oblique.ttf"],
        "r": [],
    }[style]
    for name in generic + ["arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"]:
        f = _try_truetype(name, size)
        if f:
            return f
    return ImageFont.load_default()


def render_text(
    text: str,
    *,
    media_mm: int = 25,
    font: str | None = None,
    font_size: int | None = None,
    rotate: int = 0,
    padding: int = 8,
) -> bytes:
    """Render text to a label. Text fills the tape width; height grows to fit.

    Rendered upright at native tape width, then rotated. font_size defaults to a
    fraction of the tape width so it scales with media.
    """
    media = media_for(media_mm)
    size = font_size or max(16, int(media.width_px * 0.45))
    fnt = _load_font(font, size)

    # Measure
    tmp = Image.new("RGB", (media.width_px, size * 3), "white")
    d = ImageDraw.Draw(tmp)
    bbox = d.multiline_textbbox((0, 0), text, font=fnt)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    img = Image.new("RGB", (media.width_px, text_h + 2 * padding), "white")
    d = ImageDraw.Draw(img)
    x = max(padding, (media.width_px - text_w) // 2)
    d.multiline_text((x, padding - bbox[1]), text, fill="black", font=fnt)

    img = _apply_rotate(img, rotate)
    img = _fit_width(img, media.width_px)
    return _encode_jpeg(img)


def render_qr(
    data: str,
    *,
    media_mm: int = 25,
    quiet: int = 4,
    rotate: int = 0,
) -> bytes:
    """Render a QR code sized to the tape width."""
    import qrcode

    media = media_for(media_mm)
    qr = qrcode.QRCode(border=quiet, box_size=10)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    img = _apply_rotate(img, rotate)
    img = _fit_width(img, media.width_px)
    return _encode_jpeg(img)
