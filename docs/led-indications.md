# VC-500W LED Indications

Front-panel LED meanings for the Brother VC-500W. Compiled 2026-06-15 while
diagnosing a stuck-busy printer (see CLAUDE.md "lock-wedge" gotcha). The panel
patterns turned out to be the fastest way to tell a *real* print from a wedged
one, so they're worth having on hand.

## The three LED groups

The VC-500W has three indicator areas:

1. **Wi-Fi Button LED** (the round Wi-Fi button)
2. **Cassette Button LED** (the round cassette button)
3. **Swipe-to-Cut Panel** — the row of LEDs along the front edge (a center LED
   plus two outer/end LEDs). This is both a status display *and* the touch
   strip you swipe to cut a finished label.

## Wi-Fi Button LED

| State | Meaning |
|---|---|
| Off | No wireless connection |
| White, solid | Wireless Direct mode active (device connects straight to printer; no internet) |
| White, blinking | Setting up Wireless Direct |
| Blue, solid | Infrastructure mode — connected via your wireless router (**our normal state**) |
| Blue, blinking | Setting up Infrastructure mode |
| Blue, fast blinking | Connection failed — restart printer and retry |

Our unit runs Infrastructure mode on the home LAN, so **solid blue = healthy network**.

## Cassette Button LED

| State | Meaning |
|---|---|
| Off | Roll cassette not installed / unlocked |
| White, slow blinking | Loading or unloading the cassette |
| White, fast blinking | Cannot load cassette, **or no roll remaining** |
| White, solid | Cassette correctly installed, **locked and ready to print** |

> **Do not remove the cassette while this LED is blinking or lit** — the manual
> warns label tape may advance/waste if you pull it mid-state. Wait for it to go
> off (after a press-and-hold of the Cassette button).

## Swipe-to-Cut Panel (the LED row)

This is the one that matters for diagnosing print state. **Two sources, two
detail levels** — noted per row:

| Pattern | Meaning | Source |
|---|---|---|
| **Blinking left → right** | A label finished printing; **swipe left-to-right to cut it** | User's Guide PDF §10 ("Print is completed") + p.18 |
| **Center LED lights, then both end LEDs light** (alternating, ~0.5s each) | **Printing in progress** | Brother support FAQ (not in the on-disk PDF) |
| **Middle LED blinking** | Image processing or firmware update | Brother support FAQ |
| **All LEDs blink simultaneously** | **Paper jam / error** — remove the cassette | Brother support FAQ |
| **All three LEDs stay solidly lit** | Cassette not recognized | Brother support FAQ |
| Left-to-right lighting sweep | Printer powering **on** | Brother support FAQ |
| Right-to-left dimming | Printer powering **off** | Brother support FAQ |

### ⚠️ Caveat on "center-then-outer = printing"

The **on-disk User's Guide PDF (§10)** is terse and only documents the
swipe-to-cut "blinking left to right = print completed" pattern. The richer
patterns above (center-then-outer = printing, all-simultaneous = jam, etc.) come
from the **Brother online support FAQ**, a separate Brother source. We have
**not** independently confirmed center-then-outer against our own firmware yet.
Treat it as Brother-documented-but-unverified-by-us until we watch the panel
during a known-good print.

## Why this mattered (2026-06-15 diagnosis)

We saw the panel doing **center LED ~0.5s → both end LEDs ~0.5s, repeating**, and
over TCP `status.xml` reported `State=BUSY / Stage=PRINTING`. The FAQ says that
panel pattern = "printing in progress," which *agrees* with the XML.

**But** polling showed `remain` frozen (51.18) and the stage never advancing over
~12 s, with no tape feeding. A real print advances the stage and consumes tape.
Conclusion: this is the **lock-wedge** (CLAUDE.md gotcha) — the firmware is stuck
*believing* it's printing (LEDs + XML both frozen in the print state) because a
prior job released its `:9100` lock before the job truly finished. The panel
shows "printing," not "error," precisely because from the firmware's confused
view nothing errored.

Key discriminators that proved useful:
- **All-LEDs-simultaneous** would mean a real **jam** — it wasn't that, so no
  point hunting for jammed tape.
- A genuine print shows **motion**: stage transitions and/or falling `remain`.
  Frozen values + frozen panel = wedge → **power-cycle is the only fix**.

## Sources

- **On-disk authoritative copy:** [`docs/vendor/vc500w_users_guide_en.pdf`](vendor/vc500w_users_guide_en.pdf)
  — Brother VC-500W User's Guide, §10 "LED Indications" and p.18 (Swipe-to-Cut
  Panel). Version 02, dated 2021-04-12. Downloaded 2026-06-15 from Brother:
  <https://download.brother.com/welcome/docp100376/vc500w_use_uss_canfre_ug_d00r46001a.pdf>
- **Brother support FAQ — "What do the indicator lights mean?"** (richer panel
  patterns): <https://support.brother.com/g/b/faqend.aspx?c=us&lang=en&prod=vc500weus&faqid=faqp00001466_024>
- **Manual mirror (ManualsLib), LED Indications page:**
  <https://www.manualslib.com/manual/1740229/Brother-Vc-500w.html?page=13>

## Other on-disk vendor docs

Downloaded alongside the User's Guide into `docs/vendor/`:

| File | What it is |
|---|---|
| `vc500w_users_guide_en.pdf` | Full User's Guide (primary reference) |
| `vc500w_airprint_guide_en.pdf` | AirPrint setup (we don't use this — we drive :9100 directly) |
| `vc500w_network_security_notice_en.pdf` | Brother notice on changing the default web-UI login password |
| `vc500w_open_source_license.pdf` | OSS licensing remarks for the firmware (confirms the embedded Linux/CUPS stack) |
