"""Capture the landing-page screenshots (fleet standard: pokeflute docs/service-screenshots.md).

Run against a LOCAL dev copy, never the deployed one:

    # 1. local server with an isolated, empty runtime dir (no real designs/history,
    #    printer host pointed at nothing, keep-awake off so it never touches the real
    #    printer — that has ONE connection slot and belongs to munchlax):
    #      USERPROFILE=<tmp> labeler-web -p 5091 -b 127.0.0.1
    #    with <tmp>/.labeler/settings.json = {"host":"127.0.0.1","keepalive_min":0}
    # 2. uv run --no-project --with playwright --with pillow python tools/screenshots/capture.py

The printer endpoints are faked in the browser (a ready 25 mm printer), so the pictures
show a healthy app without any hardware. Designs and history are demo data from
demo_designs.py, injected into the page's own IndexedDB.

Writes src/labeler/web/static/screenshot.png (an animated PNG: the editor cycling through
four designs; its first frame is the still) plus static/screenshots/{editor-light,history}.png.
The gallery order + captions live in web/app.py SCREENSHOTS.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from demo_designs import designs  # noqa: E402

BASE = os.environ.get("LABELER_DEV_URL", "http://127.0.0.1:5091")
STATIC = Path(__file__).resolve().parents[2] / "src/labeler/web/static"
OUT = STATIC / "screenshots"
VIEW = {"width": 1280, "height": 800}

FAKE_STATUS = {"ok": True, "host": "printer", "ready": True, "state": "IDLE",
               "stage": "READY FOR PRINT", "error": "NONE", "remain_in": 141.3,
               "remain_cm": 358.9, "cassette_type": "1", "loaded_media_mm": 25,
               "online": True, "capacity": 100}
FAKE_DEVICE = {**FAKE_STATUS, "media_name": "CZ-1004", "total_prints": 57,
               "last_printed": "2026-10-16T09:41:12+00:00",
               "raw": "<status>…</status>"}


def fake_printer(route, request):
    url = request.url
    if url.endswith("/api/status"):
        return route.fulfill(json=FAKE_STATUS)
    if url.endswith("/api/device"):
        return route.fulfill(json=FAKE_DEVICE)
    if url.endswith("/api/keepalive"):
        return route.fulfill(json={"ok": True, "interval_min": 5, "online": True,
                                   "last_ok": "2026-10-16T14:55:00+00:00",
                                   "offline_since": None})
    return route.continue_()


def new_page(browser, theme="dark"):
    ctx = browser.new_context(viewport=VIEW, locale="en-GB", timezone_id="Europe/Berlin",
                              color_scheme=theme)
    ctx.add_init_script(f"try{{localStorage.setItem('labeler_theme','{theme}')}}catch(e){{}}")
    page = ctx.new_page()
    page.route("**/api/*", fake_printer)
    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_timeout(800)
    return page


def show(page, dl, tab="edit"):
    page.evaluate("dl => loadDesignIntoEditor(dl)", dl)
    if tab != "edit":
        page.evaluate("t => switchTab(t)", tab)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1200)          # preview render + image decode
    page.mouse.move(1279, 799)           # nothing hovered


def shot(page) -> Image.Image:
    return Image.open(io.BytesIO(page.screenshot())).convert("RGB")


def seed_history(page, ds):
    """Put a few demo prints in this browser's History (IndexedDB), newest last."""
    for i, dl in enumerate(ds):
        page.evaluate("""async ([dl, used]) => {
            const thumb = await renderPreviewDataURI(dl);
            await store.addHistory(dl, {ok: true, tape_used_in: used,
                                        remain_before: 150, remain_after: 150 - used}, thumb);
        }""", [dl, round(1.1 + 0.15 * i, 2)])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ds = designs()
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # 1. Hero, animated: the editor cycling through the four designs (dark).
        page = new_page(browser, "dark")
        frames = []
        for dl in ds:
            show(page, dl)
            frames.append(shot(page))
        frames[0].save(STATIC / "screenshot.png", save_all=True, append_images=frames[1:],
                       duration=1800, loop=0, optimize=True)

        # 2. History with demo prints (dark). (The Print tab was tried and dropped: a
        #    small preview in a mostly empty page reads as nothing at thumbnail size.)
        seed_history(page, ds)
        page.evaluate("switchTab('history')")
        page.wait_for_timeout(1500)
        page.mouse.move(1279, 799)
        shot(page).save(OUT / "history.png", optimize=True)
        page.context.close()

        # 3. Light theme, editor.
        page = new_page(browser, "light")
        show(page, ds[3])
        shot(page).save(OUT / "editor-light.png", optimize=True)
        page.context.close()
        browser.close()

    for f in [STATIC / "screenshot.png", *sorted(OUT.glob("*.png"))]:
        print(f"{f.relative_to(STATIC)}  {f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
