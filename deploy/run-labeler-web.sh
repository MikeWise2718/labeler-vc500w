#!/bin/bash
# Launcher for the labeler web app on munchlax (macOS + launchd).
#
# Invoked by the LaunchDaemon (installed from deploy/com.labeler.web.plist via
# pokeflute's install-launchd-daemon.sh). Loads env from ~/.labeler/env, then
# starts waitress — NOT the Flask dev server, which is not for production.
#
# The VC-500W accepts one :9100 connection at a time, so exactly one process may
# own the printer; that process is this one, on munchlax next to the printer.

set -a
source ~/.labeler/env
set +a

cd ~/projects/labeler-vc500w

# --host 0.0.0.0 so the rest of the house (and pokeflute's probe from togepi) can
# reach it; bound to 127.0.0.1 it would work locally but read as DOWN.
# --call: labeler.wsgi:app is a factory that returns the WSGI application.
exec /opt/homebrew/bin/uv run waitress-serve \
    --host 0.0.0.0 \
    --port "${LABELER_PORT:-5001}" \
    --call labeler.wsgi:app
