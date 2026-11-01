#!/usr/bin/env bash
# The documentation check of a public repository: no reference to the team's private
# repository and no private data in any tracked file. Prints the matches and exits 1 when
# there is one. Run it from anywhere inside the repository.
#
# Exceptions (a word that only looks like a match), each one named here with why:
#   - this script itself (it holds the pattern); no other: the result must be empty.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
pattern='PRY-[0-9]|BUG-[0-9]|\bRun [0-9]{2}\b|\bT[0-9]{3}\b|ROADMAP|docs/projects|test\.md|monorepo|apps/(hub|pitwall|voice)|the driver|C:\Users|\bthe plan\b'
if out=$(git grep -nIE "$pattern" -- . ':!*.lock' ':!scripts/check-docs.sh'); then
  echo "$out" >&2
  echo "check-docs: the lines above reference the team's private repository or private data" >&2
  exit 1
fi
echo "check-docs: ok"
