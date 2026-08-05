#!/usr/bin/env bash
# Install the labeler-web launchd agent on munchlax. Run ON munchlax:
#
#   ssh munchlax
#   cd ~/projects/labeler-vc5002 && tools/munchlax/install.sh
#
# Idempotent: re-running reinstalls the plist and restarts the service.
set -euo pipefail

PLIST_SRC="$(dirname "$0")/com.labeler.web.plist"
LABEL="com.labeler.web"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [ "$(uname)" != "Darwin" ]; then
  echo "This installs a launchd agent — macOS only. On Linux use a systemd unit." >&2
  exit 1
fi

# The app writes runtime data here; launchd needs the log dir to exist up front or
# the service fails to start with a confusing permissions error.
mkdir -p "$HOME/.labeler/logs"
mkdir -p "$HOME/Library/LaunchAgents"

# launchd does not expand ~ or $HOME inside a plist — bake in the real path.
sed "s|__HOME__|${HOME}|g" "$PLIST_SRC" > "$DEST"
echo "installed $DEST"

# Unload an older copy first so bootstrap doesn't fail with "service already loaded".
launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "bootstrapped ${LABEL}"

sleep 3
if curl -sf --max-time 5 http://localhost:5001/api/ping >/dev/null; then
  echo "OK — $(curl -s http://localhost:5001/api/ping)"
  echo
  echo "Reachable from other machines at: http://$(hostname -s):5001"
else
  echo "service did not answer on :5001 — check the log:" >&2
  echo "  tail -30 ~/.labeler/logs/stderr.log" >&2
  exit 1
fi
