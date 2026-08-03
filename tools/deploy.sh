#!/usr/bin/env bash
# Deploy labler-web to munchlax (the shared printer server).
#
# WHY MUNCHLAX: the VC-500W accepts ONE :9100 connection at a time, so exactly one
# process may own the printer. That process lives on munchlax, which sits next to
# the printer in the basement and is already the production Flask target.
#
# Usage:
#   tools/deploy.sh              # rsync + sync deps + restart
#   tools/deploy.sh --no-restart # push code only
#
# Runtime data (~/.labler/) is NEVER touched — settings, stats and the event log
# survive every deploy. Designs and history live in each person's browser and are
# not on the server at all (specs/central-deployment.md).
set -euo pipefail

HOST="${LABLER_HOST:-munchlax}"
REMOTE_DIR="${LABLER_REMOTE_DIR:-~/projects/labler-vc5002}"
SERVICE="com.labler.web"
RESTART=1

for arg in "$@"; do
  case "$arg" in
    --no-restart) RESTART=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."

echo "== deploying to ${HOST}:${REMOTE_DIR} =="

# --exclude list mirrors .gitignore: never ship venvs, caches, or node_modules.
rsync -az --delete \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache/' \
  --exclude 'node_modules/' \
  --exclude '.git/' \
  --exclude 'misc/' \
  ./ "${HOST}:${REMOTE_DIR}/"

echo "== syncing dependencies =="
ssh "$HOST" "cd ${REMOTE_DIR} && uv sync --extra web"

if [ "$RESTART" -eq 1 ]; then
  echo "== restarting ${SERVICE} =="
  # kickstart -k restarts if loaded; if it was never loaded, bootstrap it first.
  ssh "$HOST" "launchctl kickstart -k gui/\$(id -u)/${SERVICE} 2>/dev/null || \
               launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/${SERVICE}.plist"
  echo "== waiting for the app to come up =="
  sleep 3
  # Verify against the version we just shipped, not just 'something answered'.
  LOCAL_VER="$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' src/labler/__init__.py | head -1)"
  REMOTE_VER="$(ssh "$HOST" "curl -sf --max-time 5 http://localhost:5000/api/ping" \
                | sed -n 's/.*\"version\":\"\([^\"]*\)\".*/\1/p')"
  if [ "$LOCAL_VER" = "$REMOTE_VER" ]; then
    echo "OK — ${HOST} is serving v${REMOTE_VER}"
  else
    echo "MISMATCH — shipped v${LOCAL_VER}, server reports '${REMOTE_VER:-no answer}'" >&2
    echo "  check: ssh ${HOST} 'tail ~/.labler/logs/stderr.log'" >&2
    exit 1
  fi
fi

echo "done. http://${HOST}:5000"
