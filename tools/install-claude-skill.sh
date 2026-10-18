#!/bin/bash
# Retired 2026-10-18. The print-label skill is published to every machine through
# CCFleet, which versions it (metadata.version in SKILL.md) and rolls it out:
#
#   ccfleet publish D:/hw/labeler-vc500w/tools/claude-skill/print-label -m "<what changed>"
#
# (run on spearow; Claude sessions do this through the ccfleet-skill skill).
# Copying it into ~/.claude/skills by hand bypasses the versioning, and the
# daily CCFleet audit reports the copy as a rule violation.
echo "retired: publish with 'ccfleet publish D:/hw/labeler-vc500w/tools/claude-skill/print-label' on spearow" >&2
exit 1
