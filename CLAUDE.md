# Labler-VC500W — Brother VC-500W Label Printing Tool

## Overview
A program to print labels **directly** to a Brother **VC-500W** ZINK full-color label printer over
the LAN, replacing Brother's clunky official software. This is a subproject of the `D:\hw` home
network workspace.

**Current state:** research/scaffolding. `README.md` documents the printer, its reverse-engineered
protocol, and existing open-source code. No application code exists yet — read `README.md` first to
pick up context.

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
- Printer lives on the `192.168.25.0/24` LAN (Fritzbox 7590 "Dungeon Door" is DHCP). **TODO:** pin
  down its IP/hostname (likely `VC-500W####.local` via mDNS) and record it here once confirmed.
- **Security:** the device runs outdated embedded Linux/CUPS and has zero transport encryption.
  Keep it on the trusted LAN only — never port-forward 9100 or expose it to the internet.

## Licensing caution
Both upstream reference projects are **AGPLv3** (strong copyleft). If we fork either, the
derivative is bound by AGPLv3. Decide the license deliberately before publishing a repo —
flag this to the user.

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
