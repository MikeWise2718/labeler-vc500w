# VC-500W Research Summary

Findings from researching what exists for controlling the Brother VC-500W programmatically.
Compiled June 2026. See `README.md` for the user-facing version; this is the fuller record.

## The printer

The Brother VC-500W is a **ZINK (Zero-Ink) full-color** label/photo printer — a different family
from Brother's monochrome thermal **QL-** and **PT-** series. This distinction drives every
code-reuse decision below.

| Spec | Value |
|---|---|
| Print technology | ZINK (Zero-Ink), full color |
| Resolution | 313 × 313 DPI (~12.48 px/mm) |
| Max print width | 50 mm (2") |
| Media widths | 9, 12, 19, 25, 50 mm |
| Max label length | ~17" single pass |
| Print speed | ~8 mm/sec |
| Connectivity | Wi-Fi (infrastructure + Wi-Fi Direct), USB |
| Control port | TCP 9100 |

Sources: [Brother USA product page](https://www.brother-usa.com/products/vc500w),
[Brother EU specifications](https://support.brother.com/g/b/spec.aspx?c=eu_ot&lang=en&prod=vc500weuk).

## Protocol (reverse-engineered)

The VC-500W does **not** use Brother's documented raster command language. The protocol was
recovered by Andrea Micheloni via man-in-the-middle packet capture (tcpdump → Wireshark) of the
official apps talking to the printer.

- Control is **raw XML over a plain TCP socket on port 9100** — not HTTP-wrapped.
- **No encryption** anywhere on the wire.
- Three message types share the one channel:
  1. **XML commands** — ASCII operation/parameter text; some carry a custom `content-length` preamble.
  2. **Binary image data** — a **JPEG** (JFIF header) sent immediately after the XML.
  3. **XML responses** — status / acknowledgements.
- Observed operations: print JPEG, query status / remaining tape, lock-release job, wait-for-idle.
  Print quality modes **vivid / normal**; cut modes **none / half / full**.

**Design implication:** printing = render content (text, barcode, QR, image) to a **JPEG at printer
resolution** (~12.48 px/mm; 50 mm ≈ 624 px wide), then send XML + JPEG to `printer:9100`. The label
"design" problem is entirely image generation; the wire protocol is dumb.

## Existing open-source code

### Primary references (fork/learn-from candidates)

| Project | What it is | Notes |
|---|---|---|
| [sgrimee/labelprinter-vc500w](https://github.com/sgrimee/labelprinter-vc500w) | Python CLI fork — most complete starting point | Adds text→label rendering, `uv`/Nix packaging, optional CUPS queue. Commands: `label-text`, `label-raw`, `label-queue*`. Config at `~/.config/labelprinter/config.json` (`host`, `label_width_mm`, `font_size`, `font`, `pixels_per_mm: 12.48`, `rotate`). Text rotation 0/90/180/270°. **AGPLv3.** |
| [m7i.org labelprinter](https://m7i.org/projects/labelprinter-linux-python-for-vc-500w/) | Original reverse-engineered Python module (Andrea Micheloni, 2021, v0.2) | The protocol source. Prints JPEGs, status, configurable print/cut modes. **AGPLv3+.** |

### Context / background

- [Hackster.io — "These Popular Brother, Zink Label Printers Prove Extremely Hackable"](https://www.hackster.io/news/these-popular-brother-zink-label-printers-prove-extremely-hackable-thanks-to-ancient-software-9d08669c3c8f)
  — the VC-500W runs an outdated embedded Linux with an old, vulnerable CUPS. Explains why the
  protocol is so open and is a strong reason to keep the device on a trusted LAN segment only.

### NOT applicable (avoid this confusion)

- [pklaus/brother_ql](https://github.com/pklaus/brother_ql) / [`brother-ql` on PyPI](https://pypi.org/project/brother-ql/)
  — targets the **QL-series monochrome thermal** raster language. The VC-500W is ZINK color with a
  different XML+JPEG protocol. Will **not** drive this printer.
- Brother's published **Raster Command Reference** SDK manuals (PT-/RJ-/QL-) — do not cover the VC-500W.

## Takeaways for this project

1. **Don't re-implement the protocol** — fork or vendor `sgrimee/labelprinter-vc500w`, the protocol
   is already solved and working.
2. **Mind the license** — both upstreams are **AGPLv3** (strong copyleft); a fork inherits it.
3. **Verify against our unit** — capture one official-app print job to confirm the XML/JPEG protocol
   matches our firmware (versions vary), and pin the printer's IP/hostname on `192.168.25.0/24`.
4. **Security** — never expose port 9100 to the internet; LAN-only.
