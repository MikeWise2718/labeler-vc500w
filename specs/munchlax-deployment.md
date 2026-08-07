# Munchlax deployment — labeler-web as a registered shared service

## Goal
Run `labeler-web` as an always-on service on **munchlax** (the basement Mac mini next
to the printer), surviving headless reboots, and appearing on **pokeflute's Services
tab**. Today the app runs on spearow's dev server (`localhost:5001`) — fine for
testing, but it dies when spearow is off and is invisible to the rest of the house.

This follows the **pokeflute munchlax-service convention** (see
`D:\hw\pokeflute\docs\deploying-a-new-munchlax-service.md` and `service-registration.md`).
The template is **Recall / cca_quiz** (`D:\python\cca_quiz\deploy\`), the closest
match: a Flask app on waitress + launchd + registry.

## Why the existing `tools/munchlax/` tooling is wrong
The repo already has `tools/munchlax/com.labeler.web.plist` + `install.sh` and
`tools/deploy.sh`, but they predate knowledge of the convention and are the wrong
pattern:

| Problem | Convention requires |
|---|---|
| Flask **dev server** (`labeler-web` → `app.run()`) | **waitress** WSGI (dev server "not for production") |
| **LaunchAgent** (`bootstrap gui/…`) | **LaunchDaemon** via `install-launchd-daemon.sh` — survives *headless* reboot |
| Port baked in plist | Port from `~/.labeler/env` |
| **No pokeflute registry entry** | `deploy/pokeflute-service.json` in `~/services-registry/` |
| Hand-rolled plist | `deploy/labeler.conf` for the generic installer |

These get **retired/replaced** by the `deploy/` scaffolding below. Keep `tools/deploy.sh`'s
rsync idea only if useful; the sanctioned path is clone + daemon-installer + register.

## Port
**5001** — confirmed free on munchlax (registry: `D:\hw\docs\munchlax-ports.md`; macOS
AirPlay owns 5000 & 7000). Matches labeler's spearow port, so "labeler = 5001" everywhere.

## Privacy note (unchanged by this)
Label content still never reaches the server. Designs/history stay in each browser's
IndexedDB; only stats + settings live server-side under `~/.labeler/`. Moving the host
from spearow to munchlax does not change that — it just makes the server always-on and
shared. No login layer (deliberate; the documented fallback if per-person privacy is
ever needed).

## Deliverables (`deploy/` in this repo, modeled on cca_quiz)
1. `waitress` dep + `src/labeler/wsgi.py` (`create_app` factory → `--call labeler.wsgi:app`)
2. `deploy/run-labeler-web.sh` (755; sources `~/.labeler/env`; execs waitress on `0.0.0.0:${LABELER_PORT:-5001}`)
3. `deploy/labeler.conf` (LABEL/RUN_SCRIPT/WORKING_DIR/LOG_FILE/PORT for the daemon installer)
4. `deploy/com.labeler.web.plist` (the LaunchAgent the daemon-installer converts — bash → run script)
5. `deploy/pokeflute-service.json` (`id: labeler`, `url: http://munchlax:5001`, `health_url: …/api/ping`)
6. `deploy/register-with-pokeflute.sh` (writes the JSON to `~/services-registry/labeler.json`)
7. Retire `tools/munchlax/*` and the old `tools/deploy.sh` (or fold their good parts in)
8. Update `CLAUDE.md` Deployment section to the new path

## On-munchlax steps (require sudo → user runs the `ssh -t` ones)
- Clone repo to `~/projects/labeler-vc500w` (deploy key if needed)
- `mkdir -p ~/.labeler ~/.labeler/logs`; write `~/.labeler/env` (chmod 600: `LABELER_PORT=5001`)
- `/opt/homebrew/bin/uv sync --extra web`
- Smoke-test the launcher by hand → `curl localhost:5001/api/ping`
- Push installer + conf; `ssh -t munchlax '~/admin/install-launchd-daemon.sh labeler'`
- Run `deploy/register-with-pokeflute.sh`; Refresh pokeflute Services tab
- Point `~/.labeler/settings.json` host at the printer (`192.168.25.190`)

## Gotchas (from the pokeflute doc — pre-paid in blood)
- `.sh` staged from Windows lands mode 644 → non-exec on munchlax; `git update-index --chmod=+x deploy/*.sh`.
- `uv` not on PATH over non-interactive SSH → absolute `/opt/homebrew/bin/uv`.
- Bind `0.0.0.0`, never `127.0.0.1` (pokeflute probes from togepi).
- munchlax is **zsh**: unmatched glob errors; use `find -exec`, not `*.json`.
- `python3` is a Store stub in Windows Git Bash — validate JSON on munchlax, not locally.
- `health_url` → `/api/ping`, not `/` (index-page probe can read UP when the app is broken).

## Task tracker
| # | Task | Status |
|---|------|--------|
| 1 | Spec written | Done |
| 2 | `waitress` dep + `src/labeler/wsgi.py` | Done (waitress serves /api/ping locally, verified) |
| 3 | `deploy/run-labeler-web.sh` (755, waitress, 0.0.0.0:5001) | Done (exec bit set via update-index) |
| 4 | `deploy/labeler.conf` | Done |
| 5 | `deploy/com.labeler.web.plist` (LaunchAgent for daemon-installer) | Done |
| 6 | `deploy/pokeflute-service.json` | Done |
| 7 | `deploy/register-with-pokeflute.sh` | Done (dry-run validates) |
| 8 | Retire `tools/munchlax/*` + old `tools/deploy.sh` | Done (git rm'd) |
| 9 | Update `CLAUDE.md` Deployment section | Done |
| 10 | Commit + push scaffolding | Done |
| 11 | On munchlax: clone, env, `uv sync`, smoke-test | **Todo (user runs sudo bits)** |
| 12 | Install LaunchDaemon; verify `sudo launchctl list` | Todo (user) |
| 13 | Register with pokeflute; confirm on Services tab | Todo |
| 14 | Point app host at printer `.190`; end-to-end print via munchlax | Todo |

**Scaffolding (1–10) is complete and committed.** Remaining (11–14) are on-munchlax
steps that need sudo/SSH — walk through them together next.
