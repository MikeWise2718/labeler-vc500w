#!/usr/bin/env python3
"""Generate a color test-grid label JPEG for the Brother VC-500W.

Sized for 25 mm continuous tape (CZ-1004) at 313 DPI (~12.48 px/mm) => ~312 px
across the tape. Exercises the full color gamut (RGB/CMY/K/W), gradient ramps,
a 1px-line resolution test, and corner/border markers to reveal edge clipping.

This is a throwaway diagnostic, intentionally standalone (no `labler` package
dependency yet). Output is git-ignored; regenerate with: python tools/make_colortest.py
"""
from PIL import Image, ImageDraw, ImageFont

W = 312   # across-tape printable width (25 mm @ ~12.48 px/mm)
L = 720   # along-tape length (~58 mm); continuous, arbitrary for a test
OUT = "tools/colortest.jpg"


def _font(size):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def main():
    img = Image.new("RGB", (W, L), "white")
    d = ImageDraw.Draw(img)

    def text(xy, s, fill="black", size=18):
        d.text(xy, s, fill=fill, font=_font(size))

    y = 0
    text((8, 6), "VC-500W COLOR TEST", "black", 20)
    y = 36

    swatches = [
        ("R", (255, 0, 0)), ("G", (0, 255, 0)), ("B", (0, 0, 255)), ("K", (0, 0, 0)),
        ("C", (0, 255, 255)), ("M", (255, 0, 255)), ("Y", (255, 255, 0)), ("W?", (255, 255, 255)),
    ]
    cols, cw, ch = 4, W // 4, 56
    for i, (name, rgb) in enumerate(swatches):
        x0, y0 = (i % cols) * cw, y + (i // cols) * ch
        d.rectangle([x0, y0, x0 + cw - 1, y0 + ch - 1], fill=rgb, outline="black")
        tc = "white" if sum(rgb) < 380 else "black"
        text((x0 + 6, y0 + ch // 2 - 9), name, tc, 16)
    y += 2 * ch + 8

    ramps = [
        ("Red", lambda t: (int(255 * t), 0, 0)),
        ("Green", lambda t: (0, int(255 * t), 0)),
        ("Blue", lambda t: (0, 0, int(255 * t))),
        ("Gray", lambda t: (int(255 * t),) * 3),
    ]
    rh = 40
    for name, fn in ramps:
        for px in range(W):
            d.line([(px, y), (px, y + rh - 1)], fill=fn(px / (W - 1)))
        text((6, y + rh // 2 - 9), name, "white", 14)
        y += rh + 4

    y += 4
    for px in range(0, W, 2):
        d.line([(px, y), (px, y + 30)], fill="black")
    text((6, y + 6), "1px lines", "white", 12)
    y += 38

    m = 18
    for (cx, cy) in [(0, 0), (W - m, 0), (0, L - m), (W - m, L - m)]:
        d.rectangle([cx, cy, cx + m - 1, cy + m - 1], fill="black")
    d.rectangle([0, 0, W - 1, L - 1], outline="black")

    img.save(OUT, "JPEG", quality=95)
    print(f"wrote {OUT}  size={img.size}")


if __name__ == "__main__":
    main()
