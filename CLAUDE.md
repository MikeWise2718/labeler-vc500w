# Labler-VC500W — Brother VC-500W Label Printing Tool

## Overview
A program to print labels **directly** to a Brother **VC-500W** ZINK full-color label printer over
the LAN, replacing Brother's clunky official software. This is a subproject of the `D:\hw` home
network workspace.

**Current state (v0.9.0):** working CLI **and** a Flask web label designer, both printing to our
firmware (status read + print write verified on hardware). The verified core
(`protocol`/`render`/`compose`/`status`/`config`) is shared by both. The web app
(`src/labler/web/`) is a **6-tab** UI — **Edit / Print / Device / History / Settings / About** — over
a display-list editor. Run with `uv run labler-web` (or `run.bat`) → http://localhost:5000; **the app
opens on the Edit tab** (you land to design, not to print). See `specs/flask-app.md` for the design
and `README.md` for the printer/protocol background.

As of v0.8.x the app is a **shared, multi-person service** deployed to munchlax — see
**SHARED DEPLOYMENT** below before changing anything about storage or logging.

Editor capabilities (Edit tab): stacked display-list of **text / image / border** elements; drag to
move, corner-handle resize, **click an element on the canvas to select it**; whole-label rotate
(0/90/180/270) to flip a long design across the tape; **per-design background color** (swatch +
picker); **color preset swatches** (standard 8 + greys + user-customizable presets saved in Settings);
**multiline text** (the Text field is a textarea; auto-length grows to fit all lines, never clipped);
**bold / italic** per text element via font *families* (defaults to Arial, not the style-less
bitmap "(default)"); **paste a bitmap with Ctrl+V** straight onto the label (uploaded via
`/api/assets`, which saves a copy under `~/.labler/assets/`); live "print preview" that is
byte-identical to what feeds out (preview == print). Saved designs live under `~/.labler/designs/<id>/`;
every print is logged to History with a thumbnail and hardware tape-used stats.

CLI surface (`labler ...`): `status`, `print-image`, `print-text`, `print-qr` (rich/rich-argparse,
short flags). Web entry point `labler-web`; CLI entry point `labler`.

### SHARED DEPLOYMENT (v0.8.x) — read this before touching storage or logging
The printer is a **shared, multi-person resource** living in the basement next to
**munchlax**, which runs the single `labler-web` that owns :9100. Full design +
task tracker: **`specs/central-deployment.md`**.

**The privacy rule is absolute: label content NEVER reaches the server.**

| Data | Lives | Notes |
|---|---|---|
| Designs, print history, thumbnails | **client browser (IndexedDB)** | `static/store.js` |
| Uploaded/pasted bitmaps | **inlined as data URIs** in the display list | decoded in-memory per render, never written |
| Tape statistics | **server** `~/.labler/stats.jsonl` | the one dataset meant to be shared |
| Settings | **server**, shared by everyone | properties of the printer, not of a person |

Enforcement (do not weaken any of these):
- `runtime.LOG_FIELD_ALLOWLIST` — an **allowlist**; anything not named is dropped
  before it hits disk. `message` is positional so it bypasses the filter and is
  capped at `MAX_MESSAGE_LEN` (exception strings can quote label text).
- `runtime.STATS_FIELDS` — a **closed schema**; `record_print_stats()` is
  keyword-only so content cannot slide in.
- `/api/assets`, `/api/designs*`, `/api/history*` are **gone**, and so is the
  history *write* path. `_read_history()` survives read-only, solely to feed the
  one-shot `/api/migrate/export` that pulls a pre-0.8.3 `~/.labler/` into a
  browser (without it, upgrading silently loses saved designs).
- `tests/test_privacy.py` fails if any of this regresses.

**Accepted tradeoff:** history is per-*browser*, not per-person — a phone and a
desktop keep separate histories, and clearing site data wipes them. Settings →
Export mitigates it. If this becomes annoying, the fallback is per-user server
directories behind a login (private between people, not from munchlax's admin);
do not build it pre-emptively.

### Web app runtime data (code/runtime split)
- **Code** lives in this repo. **Runtime data** lives under `~/.labler/` (Windows
  `%USERPROFILE%\.labler\`), created on first run: `settings.json`,
  `logs/events.jsonl`, `stats.jsonl`. `.venv` rebuilds / re-clones never lose it.
  (`designs/`, `history.jsonl`, `history/`, `assets/` are **legacy** — pre-0.8.3
  leftovers kept only for the migration export.)
- The web app's `~/.labler/settings.json` is the app authority and is SEPARATE from the CLI's
  `~/.config/labler/config.json`. Web default host is the IPv4 `192.168.25.219` (the mDNS name
  resolved to IPv6 this session and refused :9100).
- Printer access is serialized by `_PrintQueue` around a module-level lock (VC-500W = one
  :9100 connection at a time), so browser tabs — and now *people* — can't collide. The
  queue also reports position so a waiting browser says "someone else is printing"
  instead of hanging. The print path is the same verified `protocol.print_jpeg`.
- The server MUST run `threaded=True`: a print holds its request for 10–20 s, and a
  single-threaded server would block everyone else's status polls behind it.
- `settings.json` includes `custom_colors` (swatch presets) and `shelly_host`/`shelly_outlet`
  (remote power-cycle; blank host = disabled) alongside host/media/mode/cut/font/
  background/units.

### Fonts (bold / italic)
- Text style is modelled as a **font family + bold/italic flags**, NOT raw `.ttf` filenames.
  `render.FONT_FAMILIES` maps each family (Arial, Times New Roman, Courier New, Verdana, Calibri,
  Segoe UI, Comic Sans MS, DejaVu Sans) to its regular/bold/italic/bold-italic files.
- `render._load_font(font, size, bold=, italic=)` resolves family+style to the actual file, with
  **graceful degradation** (bold-italic → bold → italic → regular) and a cross-platform fallback
  (DejaVu / Arial). Raw filenames still load for back-compat.
- `/api/fonts` returns `[{name, has_bold, has_italic}]` families plus a `legacy` map
  (`file → {family, bold, italic}`). The UI disables a B/I toggle when the family lacks that face,
  and migrates old designs/settings that stored a raw `.ttf` name to family + inferred flags
  (`migrateFonts`/`normalizeFont` in `app.js`).

## The printer in one paragraph
ZINK (Zero-Ink) **full-color** printer — *not* a QL-/PT-series monochrome thermal unit. 313 DPI
(~12.48 px/mm), max print width 50 mm (2"), media widths 9/12/19/25/50 mm. Controlled by sending
**raw XML over TCP port 9100** followed by a **JPEG** payload — no HTTP, no encryption. To print
anything you render it to a JPEG at printer resolution and ship it. See `README.md` for the full
protocol breakdown and citations.

## Key external references
- **`sgrimee/labelprinter-vc500w`** (GitHub) — most complete existing Python CLI; likely fork base. AGPLv3.
- **m7i.org labelprinter** — original reverse-engineered module (the protocol source). AGPLv3+.
- **`brother_ql` does NOT apply** — that's the monochrome QL thermal protocol, different printer family.
- Full link list and details are in `README.md` → "Prior art / existing code".

## Conventions (inherited from workspace + global CLAUDE.md)
- Python with **`uv`** + a virtual environment.
- CLI uses **`rich`** for output and **`rich-argparse`** for help formatting.
- Provide short forms for all CLI flags: one-letter for common (`-o`, `-v`, `-h`), two-letter for
  project-specific (e.g. `-mw` media-width, `-ct` cut-type).
- Plans/specs go in `specs/` (e.g. `specs/cli-design.md`), tracked in git.
- This subproject gets its own `CLAUDE.md` + `README.md` (this file + the README).
- Pokemon naming theme for any host references.

## Network notes
- Printer lives on the `192.168.25.0/24` LAN (Fritzbox 7590 "Dungeon Door" is DHCP).
- **Confirmed unit** (scanned 2026-06-14, joined via WPS):
  - IP: `192.168.25.219` (DHCP — consider a static lease/reservation on the Fritzbox so it doesn't move)
  - mDNS / hostname: `VC-500W5087.local` (`VC-500W5087.fritz.box`); NetBIOS `BRVC-500W-5087`
  - MAC: `04:fe:a1:53:bf:2b`
  - Open ports: 80 (web UI, lighttpd — http://192.168.25.219/), 443, 631 (CUPS/IPP), **9100 (raw control — what our tool targets)**
  - Web UI title confirms model: "Brother VC-500W Printer"
- **Protocol verified (read path)** 2026-06-14: sent `<read><path>/status.xml</path></read>` raw to :9100,
  got the expected two-part XML reply (`code 0` envelope + status body). Reverse-engineered protocol matches
  our firmware. Reply fields: `print_state=IDLE`, `print_job_stage=READY FOR PRINT`, `print_job_error=NONE`,
  `remain=54.87`, `cassette_type=1`, `power.online=1`, `power.capacity=100`.
- **Loaded media** (2026-06-14): **CZ-1004 — 25 mm (1") continuous ZINK tape**. Maps to `cassette_type=1`
  in the status reply (one confirmed point in the cassette-type lookup). At 313 DPI (~12.48 px/mm), 25 mm
  ≈ **312 px** across the tape (minus small unprintable edge margins — confirm exact usable width on first print).
  Length axis is continuous (you choose it; ~17" max single pass).
- **First successful direct print** 2026-06-14: full sequence worked — `lock` (got job_token) →
  `<print>` XML (`mode=vivid`, `lpi=317`, `cutmode=full`, `datasize`) → raw JPEG bytes → poll
  `status.xml` (`PROCESSING → PREPARING PRINT → PREHEAT → PRINTING`) → `lock cancel`. Read AND
  write paths verified against our firmware. Test image: `tools/colortest.jpg` (312×720 color grid).
- **GOTCHA — `<autofit>1</autofit>` rescales/reorients the JPEG (tape-blowup).** With autofit on,
  the firmware fits the image to the tape itself: a **landscape** label (e.g. 312×86) gets scaled so
  it runs down the tape and consumes FAR more length than the design — confirmed 2026-06-24 from
  history `remain` deltas: a "0.7 cm" senckenberg design actually ate **10.6 cm** (`remain` 26.03→…→
  11.33). Fix (v0.5.0): send **`<autofit>0</autofit>`** with the JPEG's real `<width>/<height>`
  (parsed from the SOF marker via `protocol._jpeg_size`) so the printer prints our 312×N pixels 1:1
  and `measure == actual tape used`. **CONFIRMED ON HARDWARE 2026-06-24:** the landscape senckenberg
  label now prints correctly (short, across the tape) and the before/after `remain` delta read **3.4
  cm — matched a ruler measurement.** (The old colortest printed fine under autofit=1 only because it
  was already portrait/tall.)
- **Tape-used = hardware truth (remain before/after).** The web app now reads `remain` right BEFORE
  and AFTER each print; the delta is the authoritative tape consumed (pixel estimate is unreliable
  under autofit). Stored per history entry as `remain_before_in/remain_after_in/tape_used_in`.
- **Color quality on first print = AGED MEDIA, not a bug.** The CZ-1004 roll was bought ~April 2021,
  used very little, printed 2026 (~5 yr old). Result: **yellow very pale, red→magenta, magenta faded**
  (cyan/black/green OK). This is the textbook old-ZINK fingerprint — ZINK dye is embedded in the paper
  (no cartridges), the **yellow layer degrades first** with age/heat/light, and weak yellow collapses
  red→magenta. NOT a protocol/mode/JPEG issue: geometry, resolution, edges, ~312px usable width all
  printed correctly. **Fix = fresh CZ-1004 roll** (ZINK shelf life ~1-2 yr). Confirm by reprinting the
  color grid on new media before chasing any software color correction.
  - **Further confirmed 2026-06-15:** printed 5 colortest labels spanning ~35 cm of tape — color is
    **uniform along the entire length, no improvement further in.** Consistent with ZINK dye being
    embedded uniformly in the paper (whole roll ages evenly), not an exposed-end/print-path effect.
    Locks in the aged-media diagnosis. New CZ-1004 roll ordered; reprint the grid on it to confirm
    the stack was always correct (expect yellow vivid again, red→true-red).
- **GOTCHA — never release the lock until the job truly finishes.** If you send `<lock op=cancel>`
  while the print is still committing (e.g. a poll loop that exits early on an empty/partial status
  reply and releases too soon), the printer **wedges in `print_state=BUSY / stage=PRINTING` with no
  tape consumed** (`remain` unchanged), and then **refuses new locks** with `<code>2</code> Printer
  busy`. Only a **power-cycle** clears it. Seen 2026-06-14 with the first `print-qr`. The poll loop
  MUST: (a) ignore empty/None replies and keep polling, (b) wait until it has seen BUSY return to a
  ready/SUCCESS state, and only THEN release. The earlier manual color-grid print worked precisely
  because it polled to completion before releasing.
- **GOTCHA — single connection slot:** the VC-500W accepts only **one TCP connection on :9100 at a
  time**. Brother's setup/desktop app holds it; while held, our connects **time out even though ICMP
  ping succeeds** (looks like a dead/sleeping printer but isn't). Symptom seen 2026-06-14: setup
  program running → all TCP ports timed out, ping OK → killing the app + LED settling to solid blue
  freed the slot → print went through. **The CLI tool must close its socket cleanly after every op**,
  and "can't connect to :9100" should hint "is another app/the Brother software connected?"
- **Front-panel LEDs — quick decoder** (full chart + sources: `docs/led-indications.md`; vendor
  PDFs in `docs/vendor/`). The swipe-to-cut LED row is the fastest way to read print state:
  **center LED then both end LEDs alternating (~0.5s each) = printing in progress**;
  **blinking left→right = label done, swipe to cut**; **ALL LEDs blinking simultaneously = paper
  jam/error**; middle LED blinking = processing/firmware. Wi-Fi LED **solid blue = healthy
  (Infrastructure mode)**. Cassette LED **solid white = locked & ready**. GOTCHA seen 2026-06-15:
  a **wedged** printer shows the "printing in progress" panel pattern AND `status.xml`
  `State=BUSY/Stage=PRINTING` while `remain` and stage are **frozen** (no tape feeding) — looks
  like printing, isn't. A real print advances stage / drops `remain`; frozen = wedge →
  power-cycle. (center-then-outer pattern is Brother-FAQ-sourced, not yet confirmed on our firmware.)
- **Security:** the device runs outdated embedded Linux/CUPS and has zero transport encryption.
  Keep it on the trusted LAN only — never port-forward 9100 or expose it to the internet.

## License — DECIDED: MIT (build from scratch, do NOT fork)
**Decision (2026-06-14):** fresh implementation, **MIT licensed**. We do NOT fork
`sgrimee/labelprinter-vc500w` or the m7i.org module — we've already independently reproduced and
verified the full protocol against our firmware (status read + print write), so there's nothing left
to fork. The upstream projects are **AGPLv3**; forking would bind us (and especially the planned
Flask web app — AGPL is viral over the network) to AGPLv3. Building from scratch keeps us free to use
MIT.
- **Discipline:** write ALL code original. Do not copy code/snippets from the AGPL projects. Wire
  protocol *formats* (XML message shapes, byte framing) are factual and fine to reimplement; their
  source code is not.
- Add a top-level `LICENSE` (MIT) when code lands.

## Git
- **This subproject has its OWN dedicated remote**, separate from the parent `D:\hw` repo:
  `origin → https://github.com/MikeWise2718/labeler-vc500w.git` (branch `main`).
- **Push by default after every commit** unless told otherwise (standing user preference).
- Bump `__version__` (in `src/labler/__init__.py` AND `pyproject.toml`) on every code change — the
  web header shows the live build, which makes "is my browser seeing the new code?" answerable in two
  seconds. (See the stale-JS lesson below — the header version comes from `/api/ping`, NOT from the
  served `app.js`, so a matching header does NOT prove the JS is fresh.)
- Tracked: `CLAUDE.md`, `README.md`, `specs/`, `docs/`, `tests/`, `tools/`, `run.bat`,
  `package.json` (JS test harness). `.gitignore` excludes `.venv/`, `__pycache__/`,
  `node_modules/`, `misc/` (scratch test images — see lesson below).

## Testing
Two suites — **run both**; the Python one never touches browser JS (lesson #6):

```
.venv/Scripts/python.exe -m pytest       # 140 tests   (use this form if a server is up)
tools/run-js-tests.sh                    # 16 tests + syntax checks
```

- `tests/test_privacy.py` — the shared-printer privacy guarantees. If you are changing
  logging or storage and this goes red, you have re-opened a leak.
- `tests/test_queue.py` — printer serialization, with **real threads**. A queue bug is a
  concurrency bug; a single-threaded test will not see it.
- `tests/test_power.py` — wedge fingerprint + power-cycle safety rails. No real outlet is
  ever touched.
- `tests/test_store.mjs` — `static/store.js` against a **real IndexedDB** (fake-indexeddb).
  This is the layer where a bug silently eats someone's saved designs, so it is tested
  properly rather than eyeballed. Needs `npm install` once.

## Deployment (munchlax)
```
tools/deploy.sh                       # rsync + uv sync + launchd restart + version check
tools/munchlax/install.sh             # run ON munchlax, once, to install the agent
```
`deploy.sh` verifies that `/api/ping` reports the version it just shipped — "something
answered" is not proof the new code is live. Runtime data (`~/.labler/`) is never touched
by a deploy.

## Lessons learned (web app)
Hard-won, to stop re-paying for them:

1. **`<autofit>0</autofit>` + real JPEG width/height — never revert.** With autofit=1 the firmware
   rescales/reorients a landscape label down the tape and eats far more length than designed (a
   "0.7 cm" design ate 10.6 cm). Confirmed on hardware. Full detail in the GOTCHA above and the
   `autofit-off` memory.

2. **Preview must BE the print render, not a separate view.** Every "said short, printed long"
   misprint traced to the preview diverging from the actual JPEG (compounding view-rotations, or
   autofit). The Edit/Print previews now call the SAME `compose.render_display_list` the printer gets
   (just PNG vs JPEG). No separate "tape view" rotation. See the `preview-equals-print` memory.

3. **Tape-used = hardware truth (remain before/after delta), not a pixel estimate.** The pixel
   estimate is unreliable (autofit). Read `<remain>` right before and after each print; the delta is
   authoritative. Old history entries that predate this only have the bad estimate — we HIDE it
   rather than mislead.

4. **A thrown exception in `renderProps()` silently kills the canvas render** → browser shows a
   broken-image icon (v0.6.0–0.6.2 bug). Root cause: `wireSwatches` ran EVERY element property
   through `toHex()`, and `toHex(56)` called `.toLowerCase()` on a number → `TypeError`; that
   propagated out of `renderEditor()` before `scheduleRender()` ran, so the `<img>` never got a src.
   **Lessons:** (a) `renderEditor()` order is `renderElementList → renderProps → scheduleRender` — if
   props throws, the canvas dies; keep prop-building total. (b) `toHex` is now hardened with
   `String(c)`. (c) **A broken-image-icon canvas almost always means a JS exception aborted the render
   path, NOT a server/render bug** — check the browser console first.

5. **Don't chase a "stale cache" ghost — read the diff.** I spent several turns insisting the
   broken-image was a stale-JS / hard-refresh / reboot problem; it was a real bug I'd introduced. The
   header showing the new version (`/api/ping`) does NOT prove the served `app.js` is new. When a
   regression appears right after a change, `git diff <last-good>..HEAD` on the changed file first.

6. **Verify JS-side behavior with a real check, not reasoning.** The Python test suite never
   exercises the browser JS, so JS bugs (like #4) sail through green tests. `node --check app.js`
   catches syntax; for logic, reproduce the suspect call in `node -e '...'` (that's how the `toHex(56)`
   crash was pinned). There's opt-in editor debug logging in `app.js` (off by default; enable with
   `localStorage.setItem('labler_debug','1')`) that traces element positions through add/move/save/load.

7. **Server access log is colorized + tagged `labler`** (a `WSGIRequestHandler` subclass via rich).
   Polling routes (`/api/status`, `/api/ping`) are dimmed so real traffic stands out. If you don't
   recognize which app is logging to a terminal, that prefix is the tell.

8. **Two DIFFERENT broken-image causes — don't conflate them.** (a) A JS exception aborting the
   render (lesson #4). (b) An `<img>` that was simply never given a `src` — e.g. a tab's preview that
   only renders on first click, so the default-visible tab showed the broken glyph at load (fixed
   v0.7.4 by rendering the Print preview at boot). Guard: preview `<img>`s are `visibility:hidden`
   until a render actually loads (`setImgSrc` adds `.loaded` on `img.onload`), so an unset/failed src
   shows nothing rather than the broken glyph.

9. **Removing an endpoint is not the same as removing the data path.** When designs and
   history moved to the browser (v0.8.3), deleting `/api/history*` looked like the job was
   done — but `_append_history()` was still writing `name` + `display_list` to
   `~/.labler/history.jsonl` on **every print**. The leak was invisible because nothing
   *served* it. When relocating data for privacy, grep for the **writers**, not just the
   readers, and assert the file is absent in a test (`test_print_flow_monkeypatched`).

10. **A stale `labler-web` from an earlier session will masquerade as a bug in your new
    code.** Chasing a "migration doesn't run" failure cost several turns: every endpoint
    including `/api/ping` was hanging, and the cause was two orphaned server processes
    (one from a *previous session*) fighting over :5000. **Symptom: `curl` hangs on
    endpoints that have no reason to be slow → check for duplicate processes FIRST**
    (`Get-CimInstance Win32_Process | Where CommandLine -like '*labler*'`). Related to
    lesson #11 below — always kill your test server when done.

11. **A running `labler-web` locks `.venv/Scripts/labler-web.exe`, so `uv run` can't reinstall.** If a
   background/dev server is up when you next `run.bat` (or `uv run labler-web`), the version-bumped
   package fails to install with `os error 32` (file in use). Kill the stray server first
   (`Get-CimInstance Win32_Process ... CommandLine -like '*labler-web*'` → `Stop-Process`). When
   running a server yourself to test, **shut it down when done** — don't leave it holding the exe. To
   run the test suite while a server is up, call `.venv/Scripts/python.exe -m pytest` directly (skips
   the `uv` rebuild that would touch the locked exe).
