#!/bin/bash
# Install the `print-label` Claude Code skill for the current user, so EVERY Claude
# session on this machine (any project) can print on the shared VC-500W.
#
#   tools/install-claude-skill.sh          # from a checkout
#
# Re-run after editing tools/claude-skill/print-label/SKILL.md. On a machine without
# a checkout (e.g. munchlax), copy that one file to ~/.claude/skills/print-label/.
set -euo pipefail
src="$(cd "$(dirname "$0")" && pwd)/claude-skill/print-label"
dst="$HOME/.claude/skills/print-label"
mkdir -p "$dst"
cp "$src/SKILL.md" "$dst/SKILL.md"
echo "installed: $dst/SKILL.md"
