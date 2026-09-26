---
name: print-label
description: Print a label or sticker on the house's shared Brother VC-500W color label printer (ZINK, 12/25/50 mm tape) — from any project. Use when the user asks to print a label, sticker, tag, name label, QR/bitmap/image onto the label printer, or asks whether the label printer is on. Sends a PNG/JPEG/GIF or text through the labeler service at http://munchlax:5001 (never directly to the printer).
---

# Print a label on the shared VC-500W

The printer sits in the basement next to **munchlax**. Everything goes through the
**labeler service** (`http://munchlax:5001`), which queues everyone —
the web app, other Claude sessions. **Never connect to the printer's port 9100
yourself**: it accepts one connection at a time, and bypassing the queue collides
with whoever else is printing. (The `labeler` CLI in the labeler repo does connect
directly — don't use it from other projects.)

## 1. Get the client (stdlib only, nothing to install)

```bash
curl -s http://munchlax:5001/static/print_label.py -o "$TMP/print_label.py"
```

Run it with any Python ≥ 3.9. On spearow, `uv run --no-project python ...` avoids
the Windows Store `python` stub. Use your session scratchpad for `$TMP`.

## 2. Check the printer is on

```bash
python print_label.py --status
```

Exit code 3 / "OFFLINE" is normal after a quiet spell: **the printer switches itself
off when idle and has no Wake-on-LAN**. Tell the user someone has to press its power
button; don't retry in a loop. (The service polls it every few minutes to try to keep
it awake.)

## 3. Make the bitmap

- The image is scaled to the **tape width** — 12 mm = **150 px**, 25 mm = **312 px**,
  50 mm = **624 px** across (12.48 px/mm). The client reads which cassette is loaded
  (`--status` shows it) and uses that width automatically; `-mw` overrides. Render at that width (or a multiple) for crisp
  output; the label's **length** = the scaled height.
- A **wide** image makes a short, tiny label. Pass `-r 90` to run it ALONG the
  tape instead (full width, longer label).
- Full color prints (ZINK). Thin lines under ~2 px and text under ~20 px get mushy.
- Text-only? Skip the bitmap: `--text "Line one\nLine two" -fs 56 [-B]`.
  Text wider than the tape is silently CLIPPED (56 px fits ~11 characters on 25 mm) —
  break lines with `\n` or lower `-fs`, and check the dry-run preview.

## 4. Dry run — ALWAYS, and show the user

```bash
python print_label.py label.png -n -o "$TMP/preview.png"
```

This reports the length in cm and saves the **exact** render that will print (preview
== print). Show the preview to the user (or describe it) and state the length:
printing consumes real tape, so **get the user's OK before printing**.

## 5. Print

```bash
python print_label.py label.png -y
```

`-y` is required when not on a terminal (exit 5 = "needs confirmation"). Prints take
10–20 s; if someone else is printing the call waits its turn. On success it reports the
tape actually used (hardware-measured).

Tell the user to **tear/swipe the label off** — the next print jams if the previous
label is still in the slot.

## Options

| flag | meaning |
|---|---|
| `-mw 25` | tape width mm (default: whatever the service is set to) |
| `-r 0/90/180/270` | rotate the image; 90 runs it along the tape |
| `-bg black` | background color (edges bleed to the tape edge) |
| `-m vivid/normal` · `-c full/half/none` | print mode · cut mode |
| `-s URL` / env `LABELER_URL` | different service URL |

Exit codes: 0 ok · 1 bad input · 2 service unreachable · 3 printer offline · 4 print
failed · 5 needs `-y`.

## When it fails

- **Printer offline** → power button (see above).
- **Print failed / EJECT JAM / NO MEDIA** → the cassette must be **locked**: press the
  cassette button until its LED is solid white. And remove any label still in the slot.
- **Service unreachable** → munchlax or the labeler service is down; the owning repo is
  `D:\hw\labeler-vc500w` (deploy notes in its `CLAUDE.md`).
- Web UI for humans: http://munchlax:5001 (no login). Designs there are per-browser.

Privacy: the service renders in memory and keeps only tape statistics — label content
is never stored on munchlax.
