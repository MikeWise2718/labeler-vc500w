#!/bin/bash
# Register labeler with pokeflute's Services tab.
#
# Pokeflute discovers services from ~/services-registry/ on munchlax — one JSON
# file per service, owned by the service's own project. This copies our
# deploy/pokeflute-service.json there as labeler.json.
#
# Idempotent: re-running overwrites, so a changed port/URL/description propagates
# on the next deploy. Safe to run every deploy.
#
# Contract: pokeflute repo, docs/service-registration.md.
#
# Usage:  deploy/register-with-pokeflute.sh [-n]
#           -n   dry run — show what would be written, change nothing

set -euo pipefail

REMOTE="munchlax"     # SSH config resolves this to the Mac mini
REGISTRY_DIR="~/services-registry"
SERVICE_ID="labeler"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/pokeflute-service.json"

DRY_RUN=0
if [ "${1:-}" = "-n" ] || [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=1
fi

if [ ! -f "$SRC" ]; then
    echo "error: $SRC not found" >&2
    exit 1
fi

# Validate without python3 — it is a Microsoft Store stub in Windows Git Bash and
# would hang/fail. Grep for the required fields instead.
for field in schema id title url; do
    if ! grep -q "\"$field\"" "$SRC"; then
        echo "error: $SRC is missing required field \"$field\"" >&2
        exit 1
    fi
done

if ! grep -q "\"id\"[[:space:]]*:[[:space:]]*\"$SERVICE_ID\"" "$SRC"; then
    echo "error: $SRC does not declare id \"$SERVICE_ID\";" >&2
    echo "       the registry filename must match the id field." >&2
    exit 1
fi

DEST="$REGISTRY_DIR/$SERVICE_ID.json"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry run] would write $REMOTE:$DEST with:"
    cat "$SRC"
    exit 0
fi

echo "Registering $SERVICE_ID with pokeflute on $REMOTE ..."

# mkdir -p makes this safe on a host that has never had a registry.
ssh -o BatchMode=yes "$REMOTE" \
    "mkdir -p $REGISTRY_DIR && cat > $DEST" < "$SRC"

# Read it back so a silent partial write does not pass as success.
echo "Registered. Remote file now reads:"
ssh -o BatchMode=yes "$REMOTE" "cat $DEST"

echo
echo "Note: pokeflute caches a successful registry read for ~60s."
echo "Hit Refresh on the Services tab to see this immediately."
