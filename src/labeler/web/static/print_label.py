#!/usr/bin/env python3
"""print_label.py — print a bitmap or text on the shared Brother VC-500W.

Stdlib only (no pip install), so any project / Claude session can use it. It talks
to the labeler SERVICE (default http://munchlax:5001), never to the printer's :9100
directly — the printer takes ONE connection at a time and the service queues
everyone (web users included).

Get it from anywhere on the LAN:
    curl -s http://munchlax:5001/static/print_label.py -o print_label.py

Typical flow (what the `print-label` Claude skill does):
    python print_label.py --status                      # is the printer on?
    python print_label.py logo.png -n -o preview.png    # dry run: tape length + preview
    python print_label.py logo.png -y                   # print it

Geometry: the image is scaled to the tape WIDTH (25 mm tape = 312 px across); the
label's length is whatever the scaled height comes to. A wide image makes a short
label; use -r 90 to run it along the tape instead (bigger, uses more tape).

Exit codes: 0 ok · 1 usage/input error · 2 service unreachable · 3 printer offline
· 4 print failed · 5 needs confirmation (re-run with -y).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import struct
import sys
import urllib.error
import urllib.request

DEFAULT_SERVER = os.environ.get("LABELER_URL", "http://munchlax:5001")
PX_PER_MM = 12.48


# ---- image dimensions without Pillow ------------------------------------------
def image_size(data: bytes) -> tuple[int, int, str]:
    """(width, height, mime) for PNG / JPEG / GIF bytes; ValueError otherwise."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return w, h, "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", data[6:10])
        return w, h, "image/gif"
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h, "image/jpeg"
            i += 2 + seg_len
    raise ValueError("unsupported image (use PNG, JPEG or GIF)")


def width_px(media_mm: int) -> int:
    return round(media_mm * PX_PER_MM)


# ---- display lists --------------------------------------------------------------
def image_display_list(data: bytes, media_mm: int, *, rotate: int = 0,
                       background: str = "white") -> dict:
    """One image scaled to the tape width.

    rotate 90/270 turns the IMAGE ELEMENT (not the whole label), so the server scales
    the source straight to its final size and rotates it — full resolution. (The
    whole-label rotate would render small first and upscale, which blurs.) The
    element is laid out so that, once rotated, its old height spans the tape width
    and its old width runs along the tape.
    """
    iw, ih, mime = image_size(data)
    wpx = width_px(media_mm)
    if rotate in (90, 270):
        w = max(1, round(iw * wpx / ih))   # server: resize to w x wpx, rotate -> wpx x w
        length = w
    else:
        w = wpx
        length = max(1, round(ih * wpx / iw))
    uri = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    # `h` is only used by the server to size the label length (fit=contain keeps the
    # aspect from `w`), so it carries the along-tape length.
    el = {"type": "image", "x": 0, "y": 0, "w": w, "h": length, "z": 0,
          "rotate": rotate, "src": uri, "fit": "contain"}
    return {"media_mm": media_mm, "length_px": length,
            "background": background, "elements": [el]}


def text_display_list(text: str, media_mm: int, *, font_size: int = 56,
                      bold: bool = False, color: str = "black",
                      background: str = "white") -> dict:
    wpx = width_px(media_mm)
    lines = max(1, text.count("\n") + 1)
    el = {"type": "text", "x": 0, "y": 8, "w": wpx, "h": round(font_size * 1.25 * lines),
          "z": 0, "text": text, "font": "Arial", "font_size": font_size,
          "bold": bold, "color": color, "align": "center"}
    return {"media_mm": media_mm, "length_px": "auto", "background": background,
            "elements": [el]}


# ---- HTTP -------------------------------------------------------------------------
class ServiceError(Exception):
    pass


def call(server: str, path: str, body: dict | None = None, *, raw: bool = False,
         timeout: float = 120):
    url = server.rstrip("/") + path
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
    except urllib.error.HTTPError as e:     # 4xx/5xx still carry a JSON body
        payload = e.read()
        if raw:
            raise ServiceError(f"{path}: HTTP {e.code}")
    except (urllib.error.URLError, OSError) as e:
        raise ServiceError(f"cannot reach labeler service at {server}: {e}")
    return payload if raw else json.loads(payload or b"{}")


# ---- CLI --------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="print_label.py",
        description="Print a bitmap or text on the shared VC-500W via the labeler service.")
    ap.add_argument("image", nargs="?", help="PNG / JPEG / GIF to print")
    ap.add_argument("-t", "--text", help="print this text instead of an image (\\n = new line)")
    ap.add_argument("-fs", "--font-size", type=int, default=56, help="text size in px (default 56)")
    ap.add_argument("-B", "--bold", action="store_true", help="bold text")
    ap.add_argument("-mw", "--media-width", type=int, help="tape width in mm (default: the service's setting)")
    ap.add_argument("-r", "--rotate", type=int, default=0, choices=(0, 90, 180, 270),
                    help="90/270 runs a wide image ALONG the tape")
    ap.add_argument("-bg", "--background", default="white", help="background color")
    ap.add_argument("-m", "--mode", choices=("vivid", "normal"), help="print mode")
    ap.add_argument("-c", "--cut", choices=("none", "half", "full"), help="cut mode")
    ap.add_argument("-n", "--dry-run", action="store_true", help="measure (and preview) only; don't print")
    ap.add_argument("-o", "--output", help="save the exact print render as PNG here")
    ap.add_argument("-y", "--yes", action="store_true", help="print without asking (for scripts/Claude)")
    ap.add_argument("-st", "--status", action="store_true", help="show printer status and exit")
    ap.add_argument("-s", "--server", default=DEFAULT_SERVER,
                    help=f"labeler service URL (default {DEFAULT_SERVER}; env LABELER_URL)")
    a = ap.parse_args(argv)

    try:
        dev = call(a.server, "/api/device", timeout=30)
    except ServiceError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    online = bool(dev.get("ok"))
    if a.status:
        if online:
            media_name = (f"{dev['media_name']} ({dev.get('loaded_media_mm')} mm)" if dev.get("media_name")
                          else f"unknown (cassette type {dev.get('cassette_type')})")
            print(f"printer ONLINE — {dev.get('state')} / {dev.get('stage')}, "
                  f"media {media_name}, {dev.get('remain_in')}\" "
                  f"({dev.get('remain_cm')} cm) tape left")
        else:
            print("printer OFFLINE — it powers itself off when idle; someone has to press "
                  "its power button (basement, next to munchlax).\n"
                  f"  detail: {dev.get('error')}")
        return 0 if online else 3

    if not a.image and not a.text:
        ap.error("give an image path or --text (or --status)")

    # Default to the tape actually LOADED (read from the cassette), then the
    # service's setting. A label laid out for the wrong width prints wrong.
    media = a.media_width or dev.get("loaded_media_mm")
    if media is None:
        media = call(a.server, "/api/settings", timeout=30).get("settings", {}).get("media_width", 25)
    elif a.media_width and dev.get("loaded_media_mm") and a.media_width != dev["loaded_media_mm"]:
        print(f"warning: -mw {a.media_width} but a {dev['loaded_media_mm']} mm cassette is loaded",
              file=sys.stderr)
    try:
        if a.text:
            dl = text_display_list(a.text.replace("\\n", "\n"), media, font_size=a.font_size,
                                   bold=a.bold, background=a.background)
        else:
            with open(a.image, "rb") as f:
                dl = image_display_list(f.read(), media, rotate=a.rotate,
                                        background=a.background)
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    m = call(a.server, "/api/measure", dl)
    if not m.get("ok"):
        print(f"ERROR: {m.get('error')}", file=sys.stderr)
        return 1
    size = f"{m['length_cm']} cm ({m['length_in']}\") long on {media} mm tape"
    if a.output:
        with open(a.output, "wb") as f:
            f.write(call(a.server, "/api/render", dl, raw=True))
        print(f"preview saved: {a.output}")
    if a.dry_run:
        print(f"dry run: label would be {size}. Printer {'online' if online else 'OFFLINE'}.")
        return 0
    if not online:
        print("printer OFFLINE — it powers itself off when idle; someone has to press its "
              f"power button. ({dev.get('error')})", file=sys.stderr)
        return 3
    if not a.yes:
        if not sys.stdin.isatty():
            print(f"needs confirmation: label is {size}. Re-run with -y to print.", file=sys.stderr)
            return 5
        try:
            answer = input(f"Print label, {size}? [y/N] ")
        except EOFError:
            # stdin looked like a terminal but isn't (e.g. Git Bash on Windows with
            # input redirected) — same as no TTY: never print unconfirmed.
            print(f"\nneeds confirmation: label is {size}. Re-run with -y to print.",
                  file=sys.stderr)
            return 5
        if answer.strip().lower() not in ("y", "yes"):
            print("cancelled")
            return 0

    body = dict(dl)
    if a.mode:
        body["mode"] = a.mode
    if a.cut:
        body["cut"] = a.cut
    q = call(a.server, "/api/queue", timeout=30)
    if q.get("busy"):
        print("someone else is printing — waiting in the queue…")
    r = call(a.server, "/api/print", body, timeout=300)
    if not r.get("ok"):
        print(f"PRINT FAILED: {r.get('error') or r.get('state')}", file=sys.stderr)
        return 4
    used = r.get("tape_used_in")
    print(f"printed ✓ ({size}"
          + (f"; tape used {used}\" = {used * 2.54:.1f} cm" if used is not None else "")
          + (f"; {r.get('remain_after')}\" left" if r.get("remain_after") is not None else "")
          + "). Tear/swipe the label off before the next print.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
