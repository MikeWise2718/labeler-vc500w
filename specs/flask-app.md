# Labler-VC500W — Flask Label Designer App

**Status:** in progress · **Owner:** Mike · **Started:** 2026-06-16
**Source requirements:** `flaskreqs.md` (repo root)

## Goal

A web UI to design and print labels to the Brother VC-500W, replacing Brother's
software with a browser-based designer. Sits on top of the already-built and
verified core (`protocol.py` print path, `render.py` primitives, `status.py`,
`config.py`). Follows the workspace Flask conventions (version-in-header,
`/api/ping`, JSON endpoints, code/runtime split, JSONL event log, server-side
settings).

## Decisions (2026-06-16)

- **Editor architecture: server-renders, client-overlays.** The canvas preview is
  the *real* Pillow render returned by the server as a PNG. Direct manipulation
  (drag/scale/select) is a lightweight HTML/JS overlay of absolutely-positioned
  boxes over the preview `<img>`. On any edit the client POSTs the display-list and
  the server re-renders. **Guarantees WYSIWYG**: the preview is produced by the same
  compositor that produces the print JPEG — no canvas/Pillow font mismatch.
- **Scope: core vertical slice first.** All 5 tabs present. Edit v1 ships
  image-import + crop/scale + border + text + display-list reorder/move. Shapes,
  polygons, lines, explicit z-order UI, and CJK fonts are deferred (see Phase 7).
- **No build step.** Jinja templates + `static/` vanilla JS, served by Flask
  (workspace "no build step for small apps" pattern).
- **File-based, no database** (per requirements). History + saved designs are
  JSON files on disk under the runtime dir.

## Architecture

```
browser (tabs + editor overlay)
   |  JSON display-list  (POST /api/render, /api/print, /api/designs/*)
   v
Flask app  (src/labler/web/app.py)
   |  uses
   v
core:  compose.render_display_list()  -->  JPEG/PNG bytes
       protocol.print_jpeg() / get_status()
       config.Settings / MEDIA
```

### The new core piece: `compose.py` (display-list compositor)

`render.py` today renders ONE primitive per call. The editor needs to stack N
elements onto one label canvas. New module `src/labler/compose.py`:

- **Display-list = JSON**: a label is `{media_mm, length_px|auto, background, elements: [...]}`.
- Each **element** is a dict with `type` and a common box `{x, y, w, h, rotate, z}`
  in *label pixel coordinates* (across-tape width fixed by media; length axis grows):
  - `image`  — `{src_id, fit, crop}` (src_id refers to an uploaded asset file)
  - `text`   — `{text, font, font_size, color, align}`
  - `border` — `{color, thickness}` (whole-label frame; convenience element)
  - *(deferred)* `rect`, `line`, `polygon`, `ellipse` — `{stroke, fill, thickness, points}`
- `render_display_list(dl, *, fmt="JPEG") -> bytes` composites elements in z-order
  onto an RGB canvas at media width, encodes via the existing `_encode_jpeg`
  (reused for color-fidelity 4:4:4 subsampling) or PNG for preview.
- Reuses `render.py` helpers (`_load_font`, `_fit_width`, `_encode_jpeg`,
  `_apply_rotate`, `media_for`). Refactor those to be importable; no behavior change
  to existing CLI renders.

### Runtime data layout (code/runtime split — workspace rule)

Code in repo (`D:\hw\labler-vc5002`). Runtime data under `~/.labler/` (Windows:
`%USERPROFILE%\.labler\`), created on first run:

```
~/.labler/
  settings.json          # server-side settings (GET/POST /api/settings)
  logs/events.jsonl      # structured event log
  assets/                # uploaded source bitmaps, content-addressed (sha1.ext)
  designs/<id>/          # saved display-lists (one dir per design, per requirements)
      design.json        #   the display-list
      preview.png        #   thumbnail for the history/load list
  history.jsonl          # append-only print log (loadable + deletable entries)
```

(Note: `config.py` currently uses `~/.config/labler/config.json` for CLI Settings.
The web app's settings live in `~/.labler/settings.json` and are a superset. We
keep CLI config separate to avoid breaking the CLI; the web Settings tab is the
authority for the app. Revisit unifying later.)

## API surface

All return JSON unless noted. `/api/` prefix per convention.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/ping` | health: `{hostname, status, timestamp, version}` |
| GET | `/api/status` | live printer status (state, stage, remain", cassette, ready) |
| POST | `/api/reset` | best-effort clear/wake (status poll; documents power-cycle if wedged) |
| GET | `/api/device` | detailed device info: status + firmware + tape left + counters |
| POST | `/api/render` | body = display-list → `image/png` preview (returns bytes, not JSON) |
| POST | `/api/print` | body = display-list → render JPEG, print, log to history |
| POST | `/api/assets` | multipart upload of a bitmap → `{id, w, h}` |
| GET | `/api/assets/<id>` | serve an uploaded asset (for client preview/cropping) |
| GET | `/api/designs` | list saved designs (id, name, mtime, preview url) |
| GET | `/api/designs/<id>` | load one display-list |
| POST | `/api/designs` | save/update a display-list (returns id) |
| DELETE | `/api/designs/<id>` | delete a saved design |
| GET | `/api/history` | list print-history entries |
| DELETE | `/api/history/<entry>` | delete a history entry |
| GET/POST | `/api/settings` | server-side settings get/set |
| GET | `/api/fonts` | list available fonts on the server (for the text element) |

## UI / Tabs

Tabbed single page (`templates/index.html` + `static/app.js` + `static/style.css`).
Version in top-left header (`v{{ version }}` from `__version__`). Mobile-responsive
`@media (max-width:480px)` from the start.

- **Print** — current design preview + Print button; tape-width selector (25/50 mm);
  live status line; **Reset device** button. Confirms before printing.
- **Edit** — the designer. Left: element display-list (reorderable, select, delete).
  Center: live preview `<img>` with draggable/resizable selection box overlay.
  Right: properties of the selected element (image: crop/scale/fit; text: content/
  font/size/color/align; border: color/thickness). "Add image / Add text / Add
  border" buttons. Add-image opens the asset upload + crop. Save / Load design.
- **Device** — per-device detail card: status, firmware version, tape left (in/cm),
  cassette type → media name, last printed, total prints (from history). Diagnostics:
  raw `status.xml` dump, single-connection-slot hint on connect failure.
- **Settings** — collapsible sections: Printer (host, default media, mode, cut),
  Editor defaults (default font, bg), Display (units in/cm). Persists to
  `~/.labler/settings.json`.
- **About** — app version, Python version, free memory, runtime dir path, links to
  docs, printer model/protocol one-liner.

## Phases & task tracker

| # | Phase | Status |
|---|---|---|
| 1 | `compose.py` display-list compositor + tests (reuses render.py helpers) | ✅ 2026-06-16 |
| 2 | Flask skeleton: app factory, `/api/ping`, runtime dir bootstrap, event log, settings store | ✅ 2026-06-16 |
| 3 | Device/status/reset endpoints + Device & Print tabs (printing a design end-to-end) | ✅ 2026-06-16 |
| 4 | Assets upload/serve + Edit tab: image import, crop/scale, border, text, display-list reorder | ✅ 2026-06-16 |
| 5 | Designs save/load/delete + History log/load/delete (file-based) | ✅ 2026-06-16 |
| 6 | Settings tab + About tab + mobile CSS pass | ✅ 2026-06-16 |
| 7 | (Deferred) shapes/lines/polygons, z-order UI, CJK fonts | ☐ |

**v0.2.0 shipped** the full vertical slice: run with `uv run labler-web` → http://localhost:5000.
41 tests pass (`uv run pytest`). The print path is the verified `protocol.print_jpeg`;
the web layer serializes printer access with a module-level lock.

**v0.7.0 — Bold + italic text.** Text style is now a font **family** + bold/italic flags, not
raw `.ttf` filenames. `render.FONT_FAMILIES` maps families → regular/bold/italic/bold-italic
files; `render._load_font(..., bold=, italic=)` resolves with graceful degradation. `/api/fonts`
returns `[{name, has_bold, has_italic}]` + a `legacy` migration map. Edit tab shows B/I toggle
buttons (disabled when the family lacks that face); old designs/settings storing raw filenames are
migrated on load. 60 tests pass.

**v0.6.x — Edit-tab polish + the broken-image bug.** v0.6.0: color preset swatches (standard +
user-customizable, saved as `settings.custom_colors`), click-an-element-on-canvas to select, and a
50%-bigger print preview. v0.6.1: stagger new elements so they don't pile up at y=8. **v0.6.3 fixed
a regression** where the edit canvas showed a broken-image icon: `wireSwatches` fed every element
property through `toHex()`, and `toHex(56)` threw (`.toLowerCase` on a number), aborting
`renderProps()` before the canvas render ran. Fix: sync only color keys; harden `toHex` with
`String()`. (See CLAUDE.md → Lessons learned.)

**v0.5.x — autofit fix (hardware-confirmed) + colorized server log.** v0.5.0: print XML sends
`<autofit>0</autofit>` with the JPEG's real width/height so landscape labels print 1:1 instead of
blowing up down the tape; tape-used now read from the hardware `remain` before/after delta
(**confirmed on hardware: 3.4 cm matched a ruler**). v0.5.1 hides the unreliable pixel estimate on
old history entries. v0.5.2: colorized, `labler`-tagged Werkzeug access log.

**v0.4.0 — History as its own tab, with thumbnails.** Moved Print history out of About
into a dedicated **History** tab. Each entry now shows: a thumbnail of what printed,
name, timestamp, an orientation badge (portrait/landscape), the tape width, and the
print length (cm + inches). Load reopens the design in the editor; delete removes the
entry and its thumbnail.
- On print, `_append_history` saves a thumbnail PNG (`~/.labler/history/<id>.png`) and
  records `width_px/length_px/length_cm/length_in/orientation` via `measure_display_list`.
- `GET /api/history` backfills orientation/length for pre-v0.4 entries from their stored
  `display_list`; `GET /api/history/<id>/thumb.png` serves the saved thumbnail or, for old
  entries, renders one on the fly. Thumbnails are returned as in-memory bytes (NOT
  send_file) so a viewed thumbnail's open handle can't block its later delete on Windows.
- 50 tests pass.

**v0.3.0 — tape view + whole-label rotation.** Added so the user can SEE how much tape
a label will consume and flip it to minimize that:
- `compose.py`: `rotate` (0/90/180/270) applied to the whole composed canvas (re-fit to
  media width after 90/270, since the across-tape dimension is physically fixed); new
  `measure_display_list()` returns px + mm/cm/inches without re-encoding (shared `_compose`).
- API: `/api/render` returns `X-Label-{Width,Length}-Px` + `X-Label-Length-{Cm,In}` headers;
  new `POST /api/measure`. Print confirm dialog states the tape length first.
- UI: horizontal **tape view** (label shown as it exits the printer: 25 mm tall, length
  running right) with a cm ruler + "Tape used: X cm" readout on both Edit and Print tabs.
  "⟳ Rotate label" button cycles 90°. The edit canvas stays unrotated (so the drag overlay
  coords remain valid); the tape view shows the real rotated result.
- 48 tests pass.

## Conventions checklist (workspace Flask rules)

- [x] Version in header from `__version__`; bump on every code change
- [x] `GET /api/ping` with the 4 standard fields
- [x] Every UI action hits `/api/...` returning JSON (no form POSTs)
- [x] Runtime data under `~/.labler/`, documented in CLAUDE.md
- [x] Structured JSONL event log at `~/.labler/logs/events.jsonl`
- [x] Settings persisted server-side via `/api/settings`
- [x] (No login flow planned — LAN-only personal app — so Remember-me N/A)
- [x] No build step; mobile-responsive from the start

## Notes / risks

- **Printer connection is single-slot and slow** — the Flask process must serialize
  printer access (a module-level lock around `print_jpeg`/`get_status`) so two
  browser tabs can't collide on :9100. Status polling during a print is already
  handled inside `print_jpeg`.
- **Preview re-render cost**: each edit POSTs + re-renders with Pillow. Debounce on
  the client; renders are tens of ms for these small images, fine.
- **Host default** *(decided)*: the web Settings default is the IPv4 `192.168.25.219`
  (`runtime.FALLBACK_HOST`), because the mDNS name resolved to IPv6 and refused :9100.
