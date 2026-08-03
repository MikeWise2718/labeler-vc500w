#!/usr/bin/env bash
# Run the browser-JS test suite.
#
# The Python suite never exercises app.js / store.js (CLAUDE.md lesson #6), so the
# client-side storage layer — where a bug silently eats saved designs — gets its own
# tests against a real IndexedDB via fake-indexeddb.
#
# Usage:  tools/run-js-tests.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d node_modules ]; then
  echo "installing dev dependencies (fake-indexeddb)…"
  npm install
fi

echo "== syntax =="
node --check src/labler/web/static/app.js
node --check src/labler/web/static/store.js
echo "  ok"

echo "== store.js (IndexedDB) =="
node --test tests/test_store.mjs
