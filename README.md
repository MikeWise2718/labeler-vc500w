# Labeler-VC500W

A program to print labels **directly** to a Brother **VC-500W** ZINK (Zero-Ink) full-color
label printer over the network — bypassing Brother's official desktop/mobile software, which is
clunky and gets in the way for quick, scriptable label printing.

> **Status:** Working (v0.9.0). A `rich` CLI **and** a Flask web label designer, both printing to our
> VC-500W over the LAN. The full wire protocol (status read + print write) is verified against our
> firmware on hardware. Deployed as a **shared, multi-person service** — see
> [Shared deployment](#shared-deployment). MIT-licensed, built from scratch — see [License](#license).

---

## Quick start

```bash
uv sync                      # install (Python ≥ 3.10)

# CLI
uv run labeler status                         # query the printer
uv run labeler print-text "Hello" -mw 25      # print text on 25 mm tape
uv run labeler print-image logo.png -mw 25
uv run labeler print-qr "https://example.com"

# Web label designer (6-tab UI)
uv run labeler-web            # or run.bat   →  http://localhost:5000
```

The web app is the richer surface and opens on the **Edit** tab: a display-list editor (text / image
/ border elements), drag & resize, click-to-select on the canvas, whole-label rotate, per-design
**background color**, color preset swatches, **multiline** text, **bold/italic** text, and
**Ctrl+V to paste a bitmap** straight onto the label. A live preview is byte-identical to what feeds
out of the printer, and every print is logged to History with a thumbnail and real hardware
tape-used stats.

---

## Shared deployment

The printer is a **shared resource**: it sits in the basement next to `munchlax`, which runs the one
`labeler-web` instance that everybody points a browser at. This is not merely convenient — the
VC-500W accepts **exactly one TCP connection on :9100 at a time**, and a client that dies mid-job
wedges it until someone power-cycles it. Exactly one process may own the printer.

```bash
tools/deploy.sh                 # rsync + uv sync + restart + verify the shipped version
tools/munchlax/install.sh       # run once ON munchlax to install the launchd agent
```

**Your label content stays on your machine.** The server never stores what you print:

| Data | Where it lives |
|---|---|
| Your designs, print history, thumbnails | **your browser** (IndexedDB) |
| Images you paste or upload | **inlined into the design**, decoded in memory, never written to the server |
| Tape statistics (used, remaining, when) | the server — shared, so everyone can see when the roll needs replacing |
| Printer settings (host, media, cut) | the server — they describe the shared printer |

The tradeoff: history is per-**browser**. Your phone and your laptop keep separate histories, and
clearing site data erases them — so **Settings → Export** writes a JSON backup of your designs and
history. Import restores it on another machine.

Two more shared-printer conveniences: the Print button tells you when **someone else is printing**
instead of hanging silently, and if the printer wedges, **Device → Power-cycle** restarts it through
a Shelly smart outlet (configure it in Settings; blank = disabled) rather than requiring a trip to
the basement.

**Code vs. runtime split:** code lives in this repo; server-side state lives under `~/.labeler/`
(Windows `%USERPROFILE%\.labeler\`) — `settings.json`, `logs/events.jsonl`, `stats.jsonl`. `.venv`
rebuilds and re-clones never lose it. The web app's settings are separate from the CLI's
`~/.config/labeler/config.json`.

---

## The printer

The Brother VC-500W is a **ZINK (Zero-Ink) full-color** label/photo printer — fundamentally
different from Brother's monochrome thermal **QL-** and **PT-** series. That distinction matters
for code reuse (see below).

| Spec | Value |
|---|---|
| Print technology | ZINK (Zero-Ink), full color |
| Resolution | 313 × 313 DPI (~12.48 px/mm) |
| Max print width | 50 mm (2") |
| Tape/media widths | 9, 12, 19, 25, 50 mm |
| Max label length | ~17" (single pass) |
| Print speed | ~8 mm/sec |
| Connectivity | Wi-Fi (infrastructure + Wi-Fi Direct), USB |
| Control port | TCP **9100** |

Sources: [Brother USA product page](https://www.brother-usa.com/products/vc500w),
[Brother EU specifications](https://support.brother.com/g/b/spec.aspx?c=eu_ot&lang=en&prod=vc500weuk).

---

## How it's controlled (protocol)

The VC-500W does **not** use the documented Brother raster command language. It was
reverse-engineered via man-in-the-middle packet capture (tcpdump/Wireshark) of the official apps:

- Control is **raw XML sent over a plain TCP socket on port 9100** — *not* HTTP-encapsulated.
- **No encryption at all** on the wire.
- Three message types flow on the same channel:
  1. **XML commands** — ASCII operation/parameter instructions (some with a custom
     `content-length` preamble).
  2. **Binary image data** — a **JPEG** (identified by its JFIF header) sent right after the XML.
  3. **XML responses** — status and acknowledgements from the printer.
- Supported operations observed: print a JPEG, query status / remaining tape, lock/release job,
  wait-for-idle. Print quality modes (**vivid / normal**) and cut modes (**none / half / full**).

**Implication for our tool:** to print a label you render whatever you want (text, barcode, image)
to a **JPEG at the printer's pixel resolution** (~12.48 px/mm; 50 mm ≈ 624 px wide), then ship the
XML + JPEG to `printer:9100`. All the "label design" work is just image generation — the wire
protocol is dumb.

---

## Prior art / existing code

There is real, working open-source code for this exact printer. We should fork or learn from it
rather than starting cold.

### Primary references

| Project | What it is | Notes |
|---|---|---|
| **[sgrimee/labelprinter-vc500w](https://github.com/sgrimee/labelprinter-vc500w)** | Python CLI fork — the most complete starting point | Adds text→label rendering, `uv`/Nix packaging, optional CUPS queue mode. Commands: `label-text`, `label-raw`, `label-queue*`. AGPLv3. |
| **[m7i.org labelprinter](https://m7i.org/projects/labelprinter-linux-python-for-vc-500w/)** | The original reverse-engineered Python module (Andrea Micheloni, 2021, v0.2) | Source of the protocol work. Prints JPEGs, checks status, configurable print/cut modes. AGPLv3+. |

The `sgrimee` fork is `uv`-installable directly:

```bash
uv tool install git+https://github.com/sgrimee/labelprinter-vc500w
label-text "Hello World"
label-raw --host 192.168.25.x --print-jpeg image.jpg
```

Its config lives at `~/.config/labelprinter/config.json` (host, `label_width_mm`, `font_size`,
`font`, `pixels_per_mm: 12.48`, `rotate`, optional `cups` section). Media widths 12/19/25/29/38/50 mm;
text rotation 0/90/180/270°.

### Context / background

- **[Hackster.io — "These Popular Brother, Zink Label Printers Prove Extremely Hackable"](https://www.hackster.io/news/these-popular-brother-zink-label-printers-prove-extremely-hackable-thanks-to-ancient-software-9d08669c3c8f)**
  — security writeup; the VC-500W runs an outdated embedded Linux with an old, vulnerable CUPS.
  Good context for *why* the protocol is so open (and a caution: keep this thing on a trusted LAN
  segment, never expose 9100 to the internet).

### NOT applicable (common confusion)

- **[pklaus/brother_ql](https://github.com/pklaus/brother_ql)** / [`brother-ql` on PyPI](https://pypi.org/project/brother-ql/)
  — excellent, but it targets the **QL-series monochrome thermal** raster language. The VC-500W is
  ZINK color with a different (XML+JPEG) protocol. Do **not** expect `brother_ql` to drive this printer.
- Brother's published **Raster Command Reference** SDK manuals (PT-/RJ-/QL- series) likewise do
  **not** cover the VC-500W.

---

## Why this project (vs. Brother's software)

Brother's bundled desktop/mobile app is GUI-only, slow, and awkward for the common case: "print
*this* text / barcode / image on a label, now, from the command line or a script." Since the wire
protocol is trivial (XML + JPEG to a TCP port), a small CLI gives us:

- Scriptable, repeatable label printing (inventory tags, cable labels, shelf/bin labels).
- Render-anything: text, barcodes, QR, logos — all just become a JPEG.
- No vendor account, no cloud, works headless on the LAN.

---

## Project layout

```
src/labeler/
  protocol.py     raw XML+JPEG over :9100 — lock/print/status (the verified wire core)
  render.py       image/text/QR → print-ready JPEG; font families + bold/italic resolver
  compose.py      display-list compositor (stack text/image/border, rotate, measure)
  status.py       parse the status.xml reply
  config.py       media table (widths → px), host defaults
  cli.py          `labeler` CLI (status / print-image / print-text / print-qr)
  power.py        Shelly outlet power-cycle + wedge fingerprint (remote recovery)
  web/
    app.py        Flask factory + JSON API (/api/render, /print, /queue, /stats, /fonts, …)
                  plus _PrintQueue: serializes the printer, reports queue position
    runtime.py    ~/.labeler/ layout, WebSettings, allowlisted event log, stats stream
    static/app.js   vanilla-JS SPA (no build step)
    static/store.js client-side designs + history (IndexedDB), export/import, migration
    templates/    dark-theme SPA shell
tests/            pytest — 140 tests (protocol / render / compose / web / status /
                  privacy / queue / power) + test_store.mjs (16, real IndexedDB)
tools/            deploy.sh, munchlax/ (launchd agent), run-js-tests.sh
specs/            design docs (central-deployment.md, flask-app.md, design.md, tasks.md)
docs/             LED indications, vendor PDFs
```

The wire protocol was **built from scratch** and verified against our own firmware (decided against
forking the AGPLv3 prior art — see [License](#license)). Roadmap items from the research phase
(confirm IP, verify protocol on our unit, pick media widths, define the CLI surface, build the web
UI) are all **done**. See `CLAUDE.md` for working conventions and hard-won lessons, and `specs/` for
design docs.

---

## License

**MIT** (see `LICENSE`). This is a **from-scratch** implementation — we do *not* fork the upstream
AGPLv3 projects ([sgrimee/labelprinter-vc500w](https://github.com/sgrimee/labelprinter-vc500w),
[m7i.org labelprinter](https://m7i.org/projects/labelprinter-linux-python-for-vc-500w/)). We learned
the wire protocol from their write-ups and verified it independently against our own printer; no code
is copied from them, so their AGPLv3 does not apply. MIT keeps the project (and everything built on
top of it, including the Flask web UI — AGPL would otherwise be viral over the network) free of
copyleft obligations.
