# Labler-VC500W — Task List

Master task tracker for building the VC-500W label tool. Design rationale lives in
[`specs/design.md`](design.md); device facts and gotchas in [`../CLAUDE.md`](../CLAUDE.md).

**Status legend:** ☐ todo · ◐ in progress · ✅ done · ⏸ blocked/waiting

Last updated: 2026-06-14 (Phase 1 complete)

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
| 2.2 | `protocol.py` — TCP transport, full print sequence, clean socket close | ◐ | **BUG**: poll loop releases lock too early → printer wedges in PRINTING with no tape used. Fix poll before next print. |
| 2.3 | Handle single-connection gotcha → `ConnectionBusy` with helpful hint | ✅ | timeout → ConnectionBusy w/ hint |
| 2.4 | `render.py` — image → JPEG @ tape width (rotate/crop/fit, flatten alpha) | ✅ | width 312/624 verified |
| 2.5 | `render.py` — text rendering | ✅ | system-font fallback chain (no bundle) |
| 2.6 | `render.py` — QR rendering | ✅ | square, fit to width |

## Phase 3 — CLI

| # | Task | Status | Notes |
|---|------|:--:|------|
| 3.1 | `labler status` | ✅ | live, rich table |
| 3.2 | `labler print-image FILE` (+ `-mw -m -ct -r -cr`) | ◐ | wired; blocked by 2.2 poll bug |
| 3.3 | `labler print-text "..."` | ◐ | wired; blocked by 2.2 poll bug |
| 3.4 | `labler print-qr "..."` | ◐ | wired; print attempt wedged printer (2.2 bug) |
| 3.5 | `--dry-run` / `--output` (render only, no print) | ✅ | works; flags accepted pre/post command |

## Phase 4 — Verify & test

| # | Task | Status | Notes |
|---|------|:--:|------|
| 4.1 | `tests/` for render (sizes, rotate, crop, fit) | ✅ | 9 tests |
| 4.2 | `tests/` for protocol/status with a fake socket | ✅ | 11 tests; 20 total pass |
| 4.3 | Reprint color grid on **fresh CZ-1004** to confirm color | ⏸ | waiting on new media |
| 4.4 | **Power-cycle printer to clear wedged job** (see 2.2) | ⏸ | needs physical access |

## Phase 5 — Flask web app

| # | Task | Status | Notes |
|---|------|:--:|------|
| 5.1 | `web/app.py` skeleton; version in header; `GET /api/ping` | ☐ | |
| 5.2 | `GET /api/status`, `POST /api/render` (preview JPEG) | ☐ | |
| 5.3 | `POST /api/print` (multipart image + params) | ☐ | |
| 5.4 | `GET/POST /api/settings` persisted server-side | ☐ | |
| 5.5 | Drag-drop UI + rotate/crop + 25/50 mm selection + preview | ☐ | |
| 5.6 | Runtime-data split (`~/labler/`) + JSONL event logging | ☐ | |
| 5.7 | Mobile-responsive CSS | ☐ | |

---

## Open questions (decide before the relevant task)

- **Static DHCP reservation** for the printer on the Fritzbox, or rely on mDNS `VC-500W5087.local`? (affects 1.5 default host)
- **Default font** for `print-text` — bundle one for reproducibility, or use a system font? (affects 2.5)
- **Repo name mismatch** — folder `labler-vc5002` vs. remote `labeler-vc500w` (cosmetic; leave or align?)
