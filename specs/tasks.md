# Labler-VC500W — Task List

Master task tracker for building the VC-500W label tool. Design rationale lives in
[`specs/design.md`](design.md); device facts and gotchas in [`../CLAUDE.md`](../CLAUDE.md).

**Status legend:** ☐ todo · ◐ in progress · ✅ done · ⏸ blocked/waiting

Last updated: 2026-07-04 (through v0.7.5 — CLI + web app shipped).

> **This tracker is historical.** Phases 0–6 are all complete: the CLI and the full Flask web app
> ship and print to hardware. The live feature record is the changelog in
> [`specs/flask-app.md`](flask-app.md) (v0.2.0 → v0.7.5) and the lessons/current-state in
> [`../CLAUDE.md`](../CLAUDE.md). The per-task ◐/☐ marks below reflect the state on 2026-06-14 and are
> kept for provenance, not as current status — the notable ones are annotated `[resolved]`.

---

## Phase 0 — Research & design ✅

| # | Task | Status | Notes |
|---|------|:--:|------|
| 0.1 | Confirm printer on LAN (IP/hostname, port 9100) | ✅ | `192.168.25.219` / `VC-500W5087.local` |
| 0.2 | Verify protocol READ path (status.xml) | ✅ | parsed status reply 2026-06-14 |
| 0.3 | Verify protocol WRITE path (lock→print→poll→release) | ✅ | real color label printed |
| 0.4 | Record loaded media + cassette mapping | ✅ | CZ-1004 25 mm, `cassette_type=1`, ~312 px |
| 0.5 | Decide fork-vs-fresh + license | ✅ | fresh build, **MIT** |
| 0.6 | Write design spec | ✅ | `specs/design.md` |
| 0.7 | Commit design phase | ✅ | commit `cb6580b` (not pushed yet) |

## Phase 1 — Scaffold ✅

| # | Task | Status | Notes |
|---|------|:--:|------|
| 1.1 | `uv` project: `pyproject.toml`, deps (pillow, qrcode, rich, rich-argparse) | ✅ | `uv sync` ok; web/dev extras |
| 1.2 | `src/labler/__init__.py` with `__version__` | ✅ | `0.1.0` |
| 1.3 | Console entry point `labler = labler.cli:main` | ✅ | `labler --version/--help/status` run |
| 1.4 | `errors.py` — typed exceptions (ConnectionBusy, PrinterError, MediaError) | ✅ | |
| 1.5 | `config.py` — host default, media table (25/50 mm), settings load/save | ✅ | 25→312px, 50→624px verified |
| 1.6 | Push scaffold + design to `origin` | ☐ | |

## Phase 2 — Core library

| # | Task | Status | Notes |
|---|------|:--:|------|
| 2.1 | `status.py` — parse status.xml into `Status` dataclass (+ `.ready`) | ✅ | live-tested; `ready` accepts SUCCESS stage |
| 2.2 | `protocol.py` — TCP transport, full print sequence, clean socket close | ✅ | `[resolved]` the early-release wedge is fixed: hold ONE connection through imaging, parse the LAST status block (see `close-socket-to-cut` memory / `docs/print-sequence-finding.md`). |
| 2.3 | Handle single-connection gotcha → `ConnectionBusy` with helpful hint | ✅ | timeout → ConnectionBusy w/ hint |
| 2.4 | `render.py` — image → JPEG @ tape width (rotate/crop/fit, flatten alpha) | ✅ | width 312/624 verified |
| 2.5 | `render.py` — text rendering | ✅ | system-font fallback chain (no bundle) |
| 2.6 | `render.py` — QR rendering | ✅ | square, fit to width |

## Phase 3 — CLI

| # | Task | Status | Notes |
|---|------|:--:|------|
| 3.1 | `labler status` | ✅ | live, rich table |
| 3.2 | `labler print-image FILE` (+ `-mw -m -ct -r -cr`) | ✅ | `[resolved]` 2.2 fixed |
| 3.3 | `labler print-text "..."` | ✅ | `[resolved]` 2.2 fixed |
| 3.4 | `labler print-qr "..."` | ✅ | `[resolved]` 2.2 fixed |
| 3.5 | `--dry-run` / `--output` (render only, no print) | ✅ | works; flags accepted pre/post command |

## Phase 4 — Verify & test

| # | Task | Status | Notes |
|---|------|:--:|------|
| 4.1 | `tests/` for render (sizes, rotate, crop, fit) | ✅ | 9 tests |
| 4.2 | `tests/` for protocol/status with a fake socket | ✅ | 11 tests; 20 total pass |
| 4.3 | Reprint color grid on **fresh CZ-1004** to confirm color | ⏸ | waiting on new media |
| 4.4 | **Power-cycle printer to clear wedged job** (see 2.2) | ✅ | `[resolved]` wedge fixed in code; no longer occurs |

## Phase 5 — Flask web app ✅

All done — the full app shipped and the ongoing feature record is the changelog in
[`specs/flask-app.md`](flask-app.md) (v0.2.0 → v0.7.5).

| # | Task | Status | Notes |
|---|------|:--:|------|
| 5.1 | `web/app.py` skeleton; version in header; `GET /api/ping` | ✅ | |
| 5.2 | `GET /api/status`, `POST /api/render` (preview PNG) | ✅ | preview == print render |
| 5.3 | `POST /api/print` (display-list → JPEG + params) | ✅ | serialized printer lock |
| 5.4 | `GET/POST /api/settings` persisted server-side | ✅ | + `custom_colors` |
| 5.5 | Editor: elements, rotate, 25/50 mm, live preview | ✅ | text/image/border; multiline; bold/italic; bg color; paste |
| 5.6 | Runtime-data split (`~/.labler/`) + JSONL event logging | ✅ | |
| 5.7 | Mobile-responsive CSS | ✅ | |

## Phase 6 — Editor depth (post-MVP) ✅

Font families + bold/italic, color preset swatches, per-design background, multiline text,
click-to-select on canvas, paste-a-bitmap, History tab with hardware tape-used, colorized server log,
default-to-Edit-tab. See `specs/flask-app.md` changelog for details and versions.

---

## Open questions (historical)

- **Static DHCP reservation** for the printer on the Fritzbox, or rely on mDNS `VC-500W5087.local`?
  → **Resolved:** default host is the IPv4 `192.168.25.219` (mDNS resolved to IPv6 and refused :9100).
  A static reservation on the Fritzbox is still worth doing so the lease can't move.
- **Default font** — bundle one, or use a system font? → **Resolved:** use system fonts via a family
  registry (`render.FONT_FAMILIES`); the web app defaults to Arial with a cross-platform fallback.
- **Repo name mismatch** — folder `labler-vc5002` vs. remote `labeler-vc500w`. → Still cosmetic; the
  remote is `github.com/MikeWise2718/labeler-vc500w`. Left as-is.
