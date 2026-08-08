# Session log — munchlax deployment, editor features, and the macOS Local Network Privacy saga

**Date:** 2026-08-07 → 2026-08-08
**Versions shipped:** v0.9.8 → v0.9.13
**Outcome:** labeler runs as an always-on **root** system LaunchDaemon on munchlax:5001,
reaches the printer, is registered on pokeflute's Services tab, survives headless reboots,
and deploys+restarts passwordless. Plus a batch of editor fixes and a new feature.

This log exists because the deployment took a long, winding debug (a macOS 26 platform
quirk that masquerades as a network fault). It's written so nobody re-pays for it.

---

## 1. What shipped (commit by commit)

| Version | Change |
|---|---|
| v0.9.8 | Munchlax deploy scaffolding: `deploy/` (waitress + LaunchDaemon + pokeflute registry), retired the old `tools/munchlax/*` |
| v0.9.9 | **Fix: default-font text rendered ~8px tall on macOS** (null font fell through to PIL's fixed bitmap `load_default()`; the fallback list only had lowercase Windows/Linux font names, missing macOS `Arial.ttf`). Fixed with macOS names + `load_default(size)`. |
| v0.9.10 | Switched launcher to dev server + `--reload` (later reverted — see below) |
| v0.9.11 | **New feature: "Remove background"** on image elements — corner flood-fill to transparent, entirely client-side (privacy preserved) |
| v0.9.12 | `_connect` retries transient route errors (EHOSTUNREACH etc.) — legit resilience, but NOT the fix for the real bug |
| v0.9.13 | **The real fix: run the munchlax daemon as ROOT** (macOS 26 Local Network Privacy). Dropped `--reload`. |

Earlier the same span also produced (already committed): undo/redo (v0.9.6), the
import-visibility + id-less-design fixes (v0.9.2/0.9.3), the port-5001/chgeo-collision fix
(v0.9.4), the history-write-resilience fix (v0.9.5), and the `labler → labeler` rename
(v0.9.7).

---

## 2. Deployment: the sanctioned munchlax convention

Following `D:\hw\pokeflute\docs\deploying-a-new-munchlax-service.md` (template: Recall /
cca_quiz). Scaffolding in `deploy/`: `run-labeler-web.sh`, `labeler.conf`,
`com.labeler.web.plist`, `pokeflute-service.json`, `register-with-pokeflute.sh`.

Port **5001** (macOS AirPlay owns 5000 & 7000 — see `D:\hw\docs\munchlax-ports.md`, the
service port registry written this session). Registered on pokeflute's Services tab as
`Labeler → http://munchlax:5001`. Runtime data in `~/.labeler/` (settings, stats, log);
label content stays in each browser (IndexedDB) — the privacy model is unchanged.

---

## 3. The macOS Local Network Privacy saga (the expensive part)

### Symptom
The service was installed and healthy (HTTP 200 on `/api/ping`), but **every printer call
failed `[Errno 65] No route to host`**. Meanwhile, from the SAME machine at the SAME
moment: `ping 192.168.25.190` worked, `nc -z … 9100` worked, and a fresh-shell
`python -c "socket.create_connection((printer,9100))"` connected 10/10.

### The wrong turns (all disproven, in order)
1. **Stale long-lived process** — restarting the service didn't help.
2. **Connect retries** (v0.9.12) — retrying inside the process never re-resolved; still failed.
3. **Route table** — the `!` flags on the subnet route looked like reject routes but ping
   worked through them; not it.
4. **IPv4 / source-address bind to en0** — tested in the failing context: still failed.
5. **The Werkzeug `--reload` worker** — a red herring; no-reload in a shell worked, but that
   was the shell, not the reload flag.
6. **LaunchDaemon → GUI LaunchAgent** — converted it (wrong direction!); still failed.
7. **Tailscale network extension** — brought Tailscale fully DOWN (routes removed) and the
   daemon still failed. Exonerated.

The tell that unlocked it: **a shell/SSH process always worked; a launchd process never
did — with Tailscale up or down, agent or daemon, reload or not.** Pure launchd-context.

### Root cause (researched — Apple TN3179 + forums)
**macOS 15+/26 Local Network Privacy.** A process needs permission to reach LAN hosts; the
permission is evaluated against the top-level "responsible" process. Key facts:

- A **user-level (uid 501) launchd process is BLOCKED** — and the block is mis-reported as
  **`EHOSTUNREACH` ("No route to host")** instead of a permission error. This mislabeling is
  what sent the debug down every network rabbit-hole.
- **root is EXEMPT** ("local network privacy does not apply to code running as root").
- A **shell/SSH process is exempt** too — Terminal is the "responsible" app and, as a system
  app, is not subject to the restriction. **This is the trap: every manual test works.**
- There is **no `tccutil` / System Settings** way to grant it to a CLI/launchd tool.

Refs: [Apple TN3179](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy),
developer.apple.com/forums/thread/776552 and /778457,
[Michael Tsai — Local Network Privacy on Sequoia](https://mjtsai.com/blog/2024/10/02/local-network-privacy-on-sequoia/).

### Proof
A one-shot launchd job running `socket.create_connection((printer,9100))`:
- as **uid=0** (root system daemon) → **`OK`**
- as **uid=501** (mike) → **`Errno 65`**

### The fix (v0.9.13, in `deploy/labeler.conf`)
```
USER_NAME=root
GROUP_NAME=wheel
HOME_ENV=/Users/mike
```
Run the daemon as **root** (network-exempt); `HOME=/Users/mike` keeps runtime data in
mike's home (root writes anywhere). After reinstalling as root: `state=IDLE, ok=true` — the
service reaches the printer.

### Lesson
**If a munchlax (or any macOS 15+) launchd service can't reach a LAN device but a shell on
the same box can, it's Local Network Privacy — run the service as root. Do NOT debug it as
a network problem.** The `EHOSTUNREACH` error text is a lie.

---

## 4. Passwordless deploy (so a Claude session can do it end-to-end)

A munchlax `sudoers.d` rule (pre-existing) permits `/bin/launchctl kickstart -k
system/com.*.web` with `NOPASSWD`. So the full deploy needs no TTY and no copy-pasted
command:

```
ssh munchlax 'cd ~/projects/labeler-vc500w && git pull --ff-only && uv sync --extra web'
ssh munchlax 'sudo -n /bin/launchctl kickstart -k system/com.labeler.web'
```

(Motivated by ytsum being taken down the day before by a mis-pasted terminal command —
hand-copying long commands is the failure mode this removes.)

**Gotcha hit this session:** a `git pull` on munchlax silently failed to fast-forward
because a prior `scp` had left local modifications to `deploy/run-labeler-web.sh` +
`uv.lock`. `git reset --hard origin/main` fixed it. Don't `scp` files into a git checkout
you'll later `git pull`.

---

## 5. Other findings worth keeping

- **Printer Wi-Fi is flaky (known):** the VC-500W drops its association and falls to
  link-local; it now lives in the basement on the **`Dungeon`** SSID (broadcast by `.2`
  DungeonFritz3272, −57 dBm, the strongest/steadiest per the Wi-Fi survey) at reserved IP
  **`.190`** (MAC `04:FE:A1:53:BF:2B`). The `.190` reservation follows the printer across APs.
- **Cassette must be LOCKED** (cassette button → solid white LED), not just inserted, or
  every print EJECT-JAMs / reports NO MEDIA. `present` in status.xml is a red herring;
  `remain` dropping is the truth.
- **Multi-user serialization verified on hardware:** concurrent prints queue and run one at
  a time; back-to-back prints jam only if the prior label isn't physically removed.
- **Default-font size bug (v0.9.9):** PIL bare-name font lookup is case-sensitive; macOS
  ships `Arial.ttf` (capital), not `arial.ttf`. A missing macOS name dropped every
  default-font label to the ~10px bitmap `load_default()` that ignores size.

---

## 6. Final state

- labeler v0.9.13, **root** system LaunchDaemon on munchlax:5001
- Printer reachable (`state=IDLE, ok=true`), runs as `uid=0 root`, data in `~/.labeler/`
- On pokeflute's Services tab; survives headless reboots; deploy+restart passwordless
- 143 Python + 17 JS tests pass
- Full detail + task tracker in `specs/munchlax-deployment.md`; the root requirement is in
  `CLAUDE.md` under Deployment.
