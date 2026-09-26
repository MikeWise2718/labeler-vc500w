# VC-500W firmware, power behaviour and modding — research (2026-09-26)

**Question:** can we change the printer's behaviour — kill/lengthen auto power-off, make it
power on when mains returns (for a Shelly smart plug), stop Wi-Fi drop-offs — via settings,
a firmware update, or modified firmware?

**Answer:** not from software. There is no owner-reachable power setting, and the firmware is
encrypted and signed. Every workable improvement is outside the printer.

## Settings
- The web UI has only Wi-Fi setup, AirPrint/device info, firmware update and admin login —
  **no power settings** (checked on the device, all UI strings read 2026-09-25).
- The user's guide lists auto power-off only as a fixed spec, with no timeout selector.
- The :9100 protocol exposes config read-only; no documented write/set-config command
  ([Sunburn-Schematics protocol reference][sunburn]).
- **Power-on needs a 2-second press of the physical button.** The printer does not start
  when power is restored, and there is no Wake-on-LAN. Confirmed here 2026-09-26: an
  off printer on Shelly outlet 1 stayed off through a power cycle.
  Still untested: a printer that is *on* when power is cut.

## Firmware updates
- The printer fetches updates itself: web UI → **AirPrint Options → Select Updated Firmware**,
  or the Color Label Editor 2 app ([Brother FAQ][fwfaq]). Brother's download page lists only a
  "Firmware Update Tool" with no file ([downloads][dl]).
- Source is a ZINK CDN, not Brother's MFP update service: the manifest is
  `http://cdn.zinkapps.com/brother-secure-versions.js`. Latest seen 2026-09-26:
  **`brotherupgrade-2026031504.tgz.gpg` ("2026-03-15-04")**. Earlier builds: 2021032102, 2022061402
  ([unitof/brother-vc-500w-hacking][unitof]).
- No changelog found anywhere. An update may help Wi-Fi stability; there is no evidence it
  adds power settings.
- **To do:** read the installed version from AirPrint Options → Device Information and compare.

## Modifying the firmware — why it's blocked
- Images are **`.tgz.gpg`**: encrypted and integrity-protected with a Brother-held key. The
  only project that tried to open one gave up at the GPG layer ([unitof][unitof]).
- No public root shell, UART/JTAG pinout, or flash dump. The micro-USB port is a standard
  USB printer-class interface, not a console ([Sunburn][sunburn]).
- Brother's open-source licence document (v1.01, 2018) lists only permissive components
  (lighttpd, OpenSSL, libxml2, zlib, …). It names no GPL component and makes no source
  offer ([licence PDF][lic]).

## Security — act on this regardless
Rapid7's June 2025 Brother disclosure lists the **VC-500W as affected** (CVE-2024-51977/78/79/80/
81/83/84: auth bypass, SSRF, DoS, and a stack overflow with possible code execution). Brother's
table shows the VC-500W fix as "in planning" ([Rapid7 repo][r7], [Brother advisory][adv],
[affected-model table][table]). Brother's workarounds:
- **Change the default admin password.** Mitigates 51978/51979/51984.
- **Disable WSD and TFTP** in Web Based Management, if present. Mitigates 51980/51981/51983.
- Keep it LAN-only (already the rule — never forward 9100).

## What actually works
1. **Keep-awake polling** (`web/keepalive.py`, v0.9.15). Sunburn independently reports that a
   status query every 45 s prevents sleep. Here, 5-minute polls kept it up for about 27 h
   (2026-09-25 08:20 → 2026-09-26 11:2x, no `printer.offline`). The drop that followed was a
   manual power swap, not the timer.
2. **Mains-return power-on needs hardware:** a relay or opto across the power-button contacts,
   pulsed by the Shelly or an ESP after power returns. Or bench-test whether it starts if the
   button is held closed while power is applied. Not attempted.
3. Wi-Fi: keep-awake plus the strongest single SSID (`Dungeon`) plus a DHCP reservation.

[sunburn]: https://github.com/Sunburn-Schematics/brother-vc500w-driver
[unitof]: https://github.com/unitof/brother-vc-500w-hacking
[fwfaq]: https://support.brother.com/g/b/faqend.aspx?c=de&lang=de&prod=vc500weuk&faqid=faqp00100041_001
[dl]: https://support.brother.com/g/b/downloadlist.aspx?c=us&lang=en&prod=vc500weus&os=10071
[lic]: https://download.brother.com/welcome/docp100375/cv_vc500w_eng_license_101.pdf
[r7]: https://github.com/sfewer-r7/BrotherVulnerabilities
[adv]: https://support.brother.com/g/b/faqend.aspx?c=us&lang=en&prod=lmgroup1&faqid=faqp00100620_000
[table]: https://support.brother.com/g/s/es/security/CVE-2017-9765_label.pdf
