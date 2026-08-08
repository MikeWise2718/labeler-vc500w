#!/bin/bash
# Launcher for the labeler web app on munchlax (macOS + launchd GUI agent).
#
# Invoked by ~/Library/LaunchAgents/com.labeler.web.plist (a GUI LaunchAgent, run
# in the auto-login session — NOT a system LaunchDaemon). Loads env from
# ~/.labeler/env, then runs the app.
#
# DO NOT add --reload. The Werkzeug reloader re-exec's a worker child that, on this
# multi-interface Mac, inherits a BROKEN network context and cannot route to the LAN
# printer — every request failed "[Errno 65] No route to host" while a plain sibling
# process connected fine. Cost a long session 2026-08-08. Without --reload it reaches
# the printer. To pick up a code deploy, restart the agent (NO sudo, it's a GUI
# agent):  launchctl kickstart -k gui/$(id -u)/com.labeler.web
#
# --bind 0.0.0.0 so the rest of the house (and pokeflute's probe from togepi) can
# reach it; bound to 127.0.0.1 it would read as DOWN.

set -a
source ~/.labeler/env
set +a

cd ~/projects/labeler-vc500w

exec /opt/homebrew/bin/uv run labeler-web \
    --bind 0.0.0.0 \
    --port "${LABELER_PORT:-5001}"
