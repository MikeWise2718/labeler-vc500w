# `remain` is tape length in INCHES (calibrated 2026-06-15)

## Conclusion

The `<remain>` field in the VC-500W status reply is **inches of tape remaining**.
1.0 `remain` = 1 inch = 2.54 cm.

## How it was calibrated

We printed the `tools/colortest.jpg` grid (312×720 px) five times and measured
both the `remain` deltas and the physical labels:

- Each print dropped `remain` by a consistent **2.75** units.
- 2.75 inches = **6.99 cm**.
- All five printed labels measured **~7.0 cm** end to end (image + leader/trailer).

The prediction (2.75" → 7.0 cm) matched the ruler exactly, so the unit is inches,
not a percentage and not a firmware-internal count.

### Supporting deltas

| Event | remain before → after | Δ (in) | physical | in/print |
|---|---|---|---|---|
| colortest (CLI, debug run) | 39.87 → 37.12 | 2.75 | ~7.0 cm | 2.75 |
| colortest (CLI, clean) | 37.12 → 34.37 | 2.75 | ~7.0 cm | 2.75 |
| colortest (CLI, final) | 34.37 → 31.62 | 2.75 | ~7.0 cm | 2.75 |
| boot self-print (blank) | 47.3 → 45.37 | 1.93 | ~6 cm (eyeballed) | — |

The three measured colortest prints are the authoritative points (precise delta +
ruler). The boot blank was an eyeballed length, so it's only a rough cross-check.

## Practical notes

- At ~7 cm/colortest, current `remain ≈ 31.6"` ≈ 80 cm ≈ ~11 more colortest labels.
- A fresh CZ-1004 roll is much longer; this is a well-used ~5-year-old partial roll.
- The CLI now shows e.g. `Tape remaining  31.6" (80 cm)`.
- `print_line` in the status reply counts raster lines during PRINTING (seen
  ramping 0 → 755 for the 720px image), useful as a live progress indicator.
