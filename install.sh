#!/usr/bin/env bash
# Install this skill for Claude Code.
#
#   bash install.sh              # -> ~/.claude/skills/geminidataanalytics
#   bash install.sh /some/dir    # -> /some/dir/geminidataanalytics
#
# Copies only what the skill needs at runtime: SKILL.md, REFERENCE.md and the
# CLI. Tests, evals and the README stay in the repo.
set -uo pipefail
cd "$(dirname "$0")"

DEST_ROOT="${1:-$HOME/.claude/skills}"
DEST="$DEST_ROOT/geminidataanalytics"

command -v python3 >/dev/null 2>&1 || {
  echo "error: python3 not found. Install Python 3 (macOS: xcode-select --install)." >&2
  exit 2
}

mkdir -p "$DEST/scripts" || exit 1
cp SKILL.md REFERENCE.md "$DEST/" || exit 1
cp scripts/gda.py "$DEST/scripts/" || exit 1
chmod +x "$DEST/scripts/gda.py"
# Never ship stale bytecode from the source tree.
rm -rf "$DEST/scripts/__pycache__"

echo "installed to $DEST"
for f in SKILL.md REFERENCE.md scripts/gda.py; do
  [ -f "$DEST/$f" ] || { echo "error: $f missing after copy" >&2; exit 1; }
done

if ! python3 "$DEST/scripts/gda.py" --help >/dev/null 2>&1; then
  echo "error: the installed CLI does not run" >&2
  exit 1
fi
echo "verified: the CLI runs"
echo
echo "Next: start (or restart) Claude Code so it picks up the skill, then ask it"
echo "something like \"list my data agents\". To check credentials right now:"
echo "    python3 $DEST/scripts/gda.py doctor"
