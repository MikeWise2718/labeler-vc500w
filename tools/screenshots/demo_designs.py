"""Demo label designs for the landing-page screenshots (tools/screenshots/capture.py).

Everything here is invented and drawn in code, so the public pictures contain no real
data and no borrowed artwork. Each design is a display list exactly like the editor
saves (25 mm tape = 312 px across).
"""

from __future__ import annotations

import base64
import io
import math

from PIL import Image, ImageDraw, ImageFilter

W = 312  # 25 mm tape


def _uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def mountains(w=W, h=190) -> str:
    """Flat-art sunset: gradient sky, sun, three mountain ridges, a lake."""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    top, mid, low = (38, 32, 92), (214, 82, 110), (255, 176, 88)
    for y in range(h):
        t = y / (h * 0.62)
        c = _lerp(top, mid, min(t, 1) * 2) if t < .5 else _lerp(mid, low, min((t - .5) * 2, 1))
        d.line([(0, y), (w, y)], fill=c)
    d.ellipse([w * .58, h * .26, w * .58 + 64, h * .26 + 64], fill=(255, 226, 150))
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([w * .58 - 16, h * .26 - 16, w * .58 + 80, h * .26 + 80],
                                 fill=(255, 220, 140, 90))
    img.paste(glow.filter(ImageFilter.GaussianBlur(10)), (0, 0), glow.filter(ImageFilter.GaussianBlur(10)))
    d = ImageDraw.Draw(img)
    ridges = [((122, 54, 96), .50, 7, 38), ((74, 34, 78), .62, 5, 30), ((34, 20, 52), .74, 3.2, 22)]
    for colour, base, freq, amp in ridges:
        pts = [(x, h * base - amp * abs(math.sin(x / w * freq + base * 9))
                - amp * .35 * math.sin(x / w * freq * 3.1)) for x in range(0, w + 4, 4)]
        d.polygon(pts + [(w, h), (0, h)], fill=colour)
    for y in range(int(h * .86), h):
        d.line([(0, y), (w, y)], fill=_lerp((40, 46, 98), (22, 22, 50), (y - h * .86) / (h * .14)))
    for i in range(6):
        y = h * .89 + i * 4
        d.line([(w * .55 + i * 3, y), (w * .80 - i * 3, y)], fill=(255, 200, 120), width=1)
    return _uri(img)


def chilies(w=W, h=120) -> str:
    """Warm red field with a scatter of stylised chili pods."""
    img = Image.new("RGB", (w, h), (150, 24, 20))
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=_lerp((186, 38, 24), (118, 16, 18), y / h))
    import random
    rnd = random.Random(7)
    for _ in range(14):
        x, y = rnd.randint(-10, w), rnd.randint(-10, h)
        L, a = rnd.randint(34, 56), rnd.uniform(0, math.pi)
        pts = [(x + math.cos(a) * L * t + math.sin(a) * 7 * math.sin(t * math.pi),
                y + math.sin(a) * L * t - math.cos(a) * 7 * math.sin(t * math.pi))
               for t in [i / 12 for i in range(13)]]
        d.line(pts, fill=rnd.choice([(255, 92, 58), (255, 140, 70), (236, 58, 44)]), width=9)
        d.line([pts[0], (pts[0][0] - 6, pts[0][1] - 5)], fill=(70, 140, 60), width=4)
    return _uri(img)


def cable_stripes(w=W, h=40) -> str:
    """Colour-coded stripes, like a cable sleeve."""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    cols = [(255, 196, 0), (0, 160, 230), (230, 30, 90), (40, 200, 120)]
    for i, x in enumerate(range(-h, w + h, 26)):
        d.polygon([(x, h), (x + 13, h), (x + 13 + h, 0), (x + h, 0)], fill=cols[i % 4])
    return _uri(img)


def leaves(w=W, h=170) -> str:
    """Overlapping green leaf shapes on a pale ground."""
    img = Image.new("RGB", (w, h), (226, 241, 222))
    d = ImageDraw.Draw(img)
    greens = [(46, 125, 50), (76, 160, 70), (27, 94, 32), (104, 180, 84)]
    import random
    rnd = random.Random(3)
    for i in range(11):
        cx, cy = rnd.randint(0, w), rnd.randint(10, h + 20)
        r, a = rnd.randint(34, 62), rnd.uniform(-1.2, 1.2) - math.pi / 2
        pts = []
        for k in range(41):
            t = k / 40 * 2 * math.pi
            rr = r * (0.55 + 0.45 * math.cos(t)) if k else r
            px = rr * math.cos(t) * 0.55
            py = rr * math.sin(t) * 1.0
            pts.append((cx + px * math.cos(a) - py * math.sin(a), cy + px * math.sin(a) + py * math.cos(a)))
        d.polygon(pts, fill=greens[i % 4])
        d.line([(cx, cy), (cx + r * .9 * math.cos(a + math.pi / 2), cy + r * .9 * math.sin(a + math.pi / 2))],
               fill=(200, 230, 190), width=2)
    return _uri(img)


def _text(y, text, size, colour, *, bold=False, h=None, align="center", x=0, w=W):
    return {"type": "text", "x": x, "y": y, "w": w, "h": h or round(size * 1.25), "z": 2,
            "text": text, "font": "Arial", "font_size": size, "bold": bold,
            "italic": False, "color": colour, "align": align, "rotate": 0}


def _image(y, uri, h, z=0):
    return {"type": "image", "x": 0, "y": y, "w": W, "h": h, "z": z, "rotate": 0,
            "src": uri, "fit": "contain"}


def designs() -> list[dict]:
    camping = {
        "name": "Camping gear", "media_mm": 25, "length_px": "auto", "rotate": 0,
        "background": "#1b1f3b",
        "elements": [
            _image(0, mountains(), 190),
            _text(204, "CAMPING", 58, "#ffffff", bold=True),
            _text(276, "Box 3 · shelf B", 30, "#ffc46b"),
            {"type": "border", "z": 99, "color": "#ffc46b", "thickness": 5},
        ],
    }
    spice = {
        "name": "Smoked paprika", "media_mm": 25, "length_px": "auto", "rotate": 0,
        "background": "#fff6e8",
        "elements": [
            _image(0, chilies(), 120),
            _text(132, "Smoked", 44, "#8a1c12", bold=True),
            _text(184, "PAPRIKA", 54, "#c62828", bold=True),
            _text(252, "La Vera · hot", 28, "#6d4c41"),
        ],
    }
    cables = {
        "name": "USB-C cables", "media_mm": 25, "length_px": "auto", "rotate": 0,
        "background": "#ffffff",
        "elements": [
            _image(0, cable_stripes(), 40),
            _text(52, "USB-C", 76, "#111111", bold=True),
            _text(150, "100 W · 2 m", 34, "#0077b6"),
            _image(206, cable_stripes(), 40),
        ],
    }
    plant = {
        "name": "Monstera", "media_mm": 25, "length_px": "auto", "rotate": 0,
        "background": "#1f4d2b",
        "elements": [
            _image(0, leaves(), 170),
            _text(184, "Monstera", 54, "#ffffff", bold=True),
            _text(252, "water Sundays", 30, "#bfe7b0"),
            {"type": "border", "z": 99, "color": "#bfe7b0", "thickness": 4},
        ],
    }
    return [camping, spice, cables, plant]
