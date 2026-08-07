#!/bin/bash
# Launcher for the labeler web app on munchlax (macOS + launchd).
#
# Invoked by the LaunchDaemon (installed from deploy/com.labeler.web.plist via
# pokeflute's install-launchd-daemon.sh). Loads env from ~/.labeler/env, then
# runs the app with the stat-based auto-reloader on.
#
# WHY the dev server + --reload (not waitress): this matches how the other munchlax
# services (ytsum, etc.) deploy — a `git pull` alone makes the running process pick
# up code changes, so a routine deploy needs NO sudo restart. (Only a startup-time
# change needs `sudo launchctl kickstart -k system/com.labeler.web`.) The server
# still runs threaded=True and serializes all printer access via _print_queue, so
# it is safe for the shared home-LAN load. The reloader fires on file change (deploy
# time), never mid-print.
#
# --bind 0.0.0.0 so the rest of the house (and pokeflute's probe from togepi) can
# reach it; bound to 127.0.0.1 it would read as DOWN.

set -a
source ~/.labeler/env
set +a

cd ~/projects/labeler-vc500w

exec /opt/homebrew/bin/uv run labeler-web \
    --bind 0.0.0.0 \
    --port "${LABELER_PORT:-5001}" \
    --reload
