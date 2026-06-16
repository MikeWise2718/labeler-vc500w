# Labler-VC500W — Brother VC-500W Label Printing Tool

## Overview
A program to print labels **directly** to a Brother **VC-500W** ZINK full-color label printer over
the LAN, replacing Brother's clunky official software. This is a subproject of the `D:\hw` home
network workspace.

**Current state (v0.2.0):** working CLI **and** a Flask web label designer. The verified core
(`protocol`/`render`/`compose`/`status`/`config`) prints to our firmware; the web app
(`src/labler/web/`) is a 5-tab UI (Print/Edit/Device/Settings/About) over it. Run with
`uv run labler-web` → http://localhost:5000. See `specs/flask-app.md` for the design and
`README.md` for the printer/protocol background.

### Web app runtime data (code/runtime split)
- **Code** lives in this repo. **Runtime data** lives under `~/.labler/` (Windows
  `%USERPROFILE%\.labler\`), created on first run: `settings.json`, `logs/events.jsonl`,
  `assets/` (uploaded bitmaps), `designs/<id>/` (saved display-lists + previews),
  `history.jsonl` (print log). `.venv` rebuilds / re-clones never lose this state.
- The web app's `~/.labler/settings.json` is the app authority and is SEPARATE from the CLI's
  `~/.config/labler/config.json`. Web default host is the IPv4 `192.168.25.219` (the mDNS name
  resolved to IPv6 this session and refused :9100).
- Printer access is serialized with a module-level lock (VC-500W = one :9100 connection at a time),
  so two browser tabs can't collide. The print path is the same verified `protocol.print_jpeg`.

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
- Parent `D:\hw` is already a git repo (`origin` set, branch `main`).
- **TODO:** decide whether this subproject is committed into the `D:\hw` repo or split into its own
  repo with its own remote. Confirm with the user before `git init`-ing a separate repo here.
- Track `CLAUDE.md`, `README.md`, `specs/`, `docs/`, `tests/`, `tools/`. Add `.gitignore` for
  `.venv/`, `__pycache__/` once Python code lands.

## Next steps when building starts
1. Confirm printer IP/hostname on the LAN; capture a print job from the official app to verify the
   XML/JPEG protocol against *our* firmware (versions vary).
2. Decide: fork `sgrimee/labelprinter-vc500w` vs. fresh `uv`/`rich` implementation.
3. Write `specs/cli-design.md` with the command surface (`print-text`, `print-image`, `print-qr`,
   `status`) before coding.
