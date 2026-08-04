# Labeler-VC500W — Design Spec

Design for a from-scratch tool to print labels directly to a Brother **VC-500W** ZINK full-color
printer over the LAN. Shared core library with two front-ends: a **CLI** (built first) and a **Flask
web app** (later). MIT licensed.

**Status:** shipped (v0.7.5). This is the original design rationale; the CLI and the full Flask web
app are built and print to hardware. Protocol reverse-engineered and **verified end-to-end against
our unit** (status read + a real color label printed, 2026-06-14). See `README.md` / `docs/research.md`
for the protocol record, `specs/flask-app.md` for the web-app design + changelog, and `CLAUDE.md` for
confirmed device facts and gotchas.

---

## Goals

- Print **text, images, QR codes** to the VC-500W from the command line or scripts — no Brother
  software, no cloud, no GUI required.
- A **Flask web UI** (later) for drag-drop image printing with rotate/crop and 25/50 mm selection,
  reusing the same core.
- Be a clean, small, **MIT-licensed** codebase others can build on.

## Non-goals (for now)

- CUPS / print-queue integration (upstream had it; we don't need it).
- USB transport (our path is network-only — the reverse-engineered protocol is TCP :9100; USB needs
  Brother's driver). See `CLAUDE.md`.
- Windows printer-driver registration.

---

## Architecture: shared core → {CLI, Flask}

The real logic lives in a **core library**. Both front-ends import it directly and call the same
functions — the Flask app does **not** shell out to the CLI (we'd lose return values, exceptions, and
the print-progress poll loop).

```
src/labeler/
  __init__.py        # __version__ (single source of truth, rendered in Flask header)
  config.py          # host/IP, media table, defaults; load/save settings
  protocol.py        # TCP :9100 transport — lock -> token -> print -> poll -> release
  status.py          # parse status.xml into a Status dataclass
  render.py          # image/text/QR -> JPEG @ tape width; rotate, crop, fit
  errors.py          # typed exceptions (ConnectionBusy, PrinterError, MediaError, ...)
  cli.py             # thin: argparse (+ rich/rich-argparse) -> core calls
  web/               # Flask app (phase 2) -> SAME core
    app.py
    templates/
    static/
```

Packaging: **`uv`** project, `pyproject.toml`. Console entry point `labeler = "labeler.cli:main"`.
Deps: `pillow` (render), `qrcode` (QR), `rich` + `rich-argparse` (CLI). Flask phase adds `flask`.

### Core API (the contract both front-ends use)

```python
# protocol.py
def get_status(host: str, *, timeout=8.0) -> Status: ...
def print_jpeg(host: str, jpeg: bytes, *, mode="vivid", cut="full",
               on_progress=None, timeout=60.0) -> Status: ...
    # on_progress(stage:str) callback so CLI shows a spinner and Flask can stream/poll

# render.py
def render_image(src, *, media_mm=25, rotate=0, crop=None, fit="contain",
                 bg="white") -> bytes:      # returns JPEG bytes at tape px width
def render_text(text, *, media_mm=25, font=None, font_size=None, rotate=0) -> bytes: ...
def render_qr(data, *, media_mm=25, quiet=4, rotate=0) -> bytes: ...
```

`render_*` return **JPEG bytes** — the same artifact the CLI sends, the Flask preview shows, and
`--dry-run` writes to disk. One pipeline, three consumers.

---

## Media table (`config.py`)

Printer is **313 DPI ≈ 12.48 px/mm**. Printable width = tape width × px/mm (minus small unprintable
edge margins — confirmed on 25 mm that the full ~312 px prints with no clipping).

| Media | Width | Across-tape px | Notes |
|-------|-------|----------------|-------|
| CZ-1003 | 9 mm  | ~112 px | (not owned) |
| CZ-1002 | 12 mm | ~150 px | (not owned) |
| CZ-1001 | 19 mm | ~237 px | (not owned) |
| **CZ-1004** | **25 mm** | **~312 px** | **owned/loaded; verified printing** |
| CZ-1005 | 50 mm | ~624 px | target for large/photo labels |

Length axis is **continuous** (caller chooses; ~17" max single pass). Phase-1 supports **25 and 50
mm**; others are table entries we can enable later. Status reply exposes `cassette_type` (25 mm = `1`,
one confirmed point) — the tool can warn if requested media-width ≠ loaded cassette.

---

## Render pipeline (`render.py`) — shared by CLI and Flask

This is the heart of the "label design" work (the wire protocol is dumb; everything is image gen).

1. Load source (PNG/JPEG/etc. via Pillow; or rasterize text/QR).
2. **Rotate** (0/90/180/270) — needed for landscape content on narrow tape; web UI exposes it live.
3. **Crop** (optional box) — web UI drag-crop; CLI `--crop x,y,w,h`.
4. **Fit** to tape width:
   - `contain` (default): scale so the across-tape dimension = media px, pad length as needed.
   - `cover` / `stretch` as options.
5. Convert to **RGB on white** (flatten alpha — ZINK has no transparency).
6. Encode **JPEG**. Default `quality=95`. Offer `subsampling=0` (no chroma subsampling) for
   saturated colors — JPEG 4:2:0 can degrade strong reds/yellows.

`--dry-run` / web preview = stop here and surface the JPEG instead of printing.

---

## Transport (`protocol.py`) — already verified

Sequence (each XML message = raw UTF-8, no length prefix; JPEG bytes follow `<print>` directly;
`datasize` declares the byte count):

1. **lock** `<lock><op>set</op><page_count>-1</page_count><job_timeout>99</job_timeout></lock>`
   → reply carries `<job_token>`.
2. **print** `<print><mode>vivid|normal</mode><speed>…</speed><lpi>317|264</lpi><dataformat>jpeg`
   `</dataformat><autofit>1</autofit><datasize>N</datasize><cutmode>none|half|full</cutmode>`
   `<job_token>…</job_token></print>` → then send the **N JPEG bytes**.
   - `vivid` = speed 0 / lpi 317; `normal` = speed 1 / lpi 264.
3. **poll** `<read><path>/status.xml</path><job_token>…</job_token></read>` until
   `print_state=IDLE` & stage `READY` (stages seen: PROCESSING → PREPARING PRINT → PREHEAT →
   PRINTING). Surface `print_job_error`.
4. **release** `<lock><op>cancel</op><job_token>…</job_token></lock>`.

**Must:** open one connection, do the whole job, **close cleanly** (see gotcha below).

### Status object (`status.py`)

Parse from `status.xml`: `print_state`, `print_job_stage`, `print_job_error`, `remain` (tape left),
`cassette_type`, `power.online`, `power.capacity`. Expose as a dataclass with a `.ready` property.

---

## Gotchas baked into the design (learned 2026-06-14)

- **Single connection slot.** The VC-500W accepts **one TCP connection on :9100 at a time**. While
  Brother's setup/desktop app (or a stale socket) holds it, our connects **time out though ICMP ping
  succeeds**. Design response: always close the socket after each op; on connect-timeout, raise
  `ConnectionBusy` with the hint *"another app (or Brother's software) may be connected — close it."*
- **Aged ZINK media → color skew.** Old paper (yellow layer degrades first) prints red→magenta,
  yellow pale. NOT a software bug. The tool should not try to "fix" this; doc it and recommend fresh
  media. (Optional later: a `--color-profile` hook if we ever want correction.)

---

## CLI surface (`cli.py`) — phase 1

`rich` output, `rich-argparse` help, short forms for every flag.

```
labeler status                       # query printer; pretty-print Status
labeler print-image FILE             # render image -> print
labeler print-text "TEXT"            # render text   -> print
labeler print-qr "DATA"              # render QR     -> print
```

Common flags (one-letter for common, two-letter for project-specific):

| Flag | Short | Meaning | Default |
|------|-------|---------|---------|
| `--host` | `-H` | printer IP/hostname | `VC-500W5087.local` (fallback `192.168.25.219`) |
| `--media-width` | `-mw` | 25 or 50 (mm) | 25 |
| `--mode` | `-m` | vivid / normal | vivid |
| `--cut` | `-ct` | none / half / full | full |
| `--rotate` | `-r` | 0/90/180/270 | 0 |
| `--crop` | `-cr` | `x,y,w,h` | none |
| `--output` | `-o` | save rendered JPEG (implies dry-run) | — |
| `--dry-run` | `-n` | render, don't print | off |
| `--verbose` | `-v` | show protocol/progress detail | off |

Config file (`~/.config/labeler/config.json` or via `config.py`): default host, media width, font.

---

## Flask web app (`web/`) — phase 2

Drag-drop image → live rotate/crop → pick 25/50 mm → preview (the render JPEG) → print. Calls the
**same core**. Must follow the workspace Flask conventions:

- **Version in header** (top-left, from `labeler.__version__`); bump version on every code change.
- **`GET /api/ping`** → `{hostname, status, timestamp, version}`, no auth.
- **REST/JSON for every UI action** — no server-rendered form POSTs:
  - `POST /api/print` (multipart image + params) → JSON result/status
  - `GET  /api/status` → Status JSON
  - `POST /api/render` → returns preview JPEG (dry-run)
  - `GET/POST /api/settings` → persisted server-side
- **Runtime data split**: code in repo; runtime data (settings, uploads, logs) under `~/labeler/`.
- **Structured JSONL logging** to `~/labeler/logs/events.jsonl` (start/print/error/etc.).
- **Settings persisted server-side** (default host, media, mode, cut).
- Mobile-responsive (`@media max-width:480px`). No build step — Jinja + vanilla JS.
- (Auth only if exposed beyond the trusted LAN — default is LAN-only; **never** expose :9100.)

---

## Security

Device runs outdated embedded Linux/CUPS, zero transport encryption. **LAN-only**; never port-forward
:9100; never expose the Flask app to the internet without auth. (See `CLAUDE.md`.)

---

## Build order / task tracker

| # | Task | Status |
|---|------|--------|
| 0 | Confirm printer on LAN; verify protocol read+write | ✅ done (2026-06-14) |
| 1 | Decide fork-vs-fresh + license | ✅ done — fresh, MIT |
| 2 | Write this spec | ✅ done |
| 3 | `uv` scaffold: `pyproject.toml`, `src/labeler/`, `__version__`, `.gitignore` | ☐ |
| 4 | Core: `config.py` (media table, host) + `errors.py` | ☐ |
| 5 | Core: `protocol.py` + `status.py` (port the verified sequence; tests with a fake socket) | ☐ |
| 6 | Core: `render.py` (image/text/QR, rotate/crop/fit, JPEG) | ☐ |
| 7 | `cli.py`: `status`, `print-image`, `print-text`, `print-qr` (+ `--dry-run`) | ☐ |
| 8 | Reprint color grid on **fresh CZ-1004** to confirm color (waiting on media) | ☐ |
| 9 | Flask `web/`: ping/status/render/print/settings, drag-drop + crop/rotate UI | ☐ |
| 10 | `tests/` for render + protocol; `tools/` print helpers | ☐ |

---

## Open questions

- **Repo home:** commit into `D:\hw` monorepo, or split into its own repo with own remote + MIT? (Per
  `CLAUDE.md` TODO — confirm with user.) Affects whether MIT `LICENSE` here is authoritative.
- **Static DHCP reservation** for the printer on the Fritzbox so `192.168.25.219` doesn't move (or
  rely on mDNS `VC-500W5087.local`).
- **Font** default for `print-text` (bundle one for reproducibility vs. use a system font?).
