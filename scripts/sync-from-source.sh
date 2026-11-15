#!/usr/bin/env bash
#
# sync-from-source.sh: refresh the delegate tool from the private working copy, then check
# that nothing personal or machine-local is in the repo.
#
# orchestrate/orchestrate.py and orchestrate/tests/ come from the working copy.
# Everything else (READMEs, skills, prompts, browser/) is maintained here.
#
# Usage: ./scripts/sync-from-source.sh [--check-only]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$(cd "$REPO_DIR/../.." && pwd)/agent_tools/orchestrate"

if [[ "${1:-}" != "--check-only" ]]; then
    [[ -d "$SRC" ]] || { echo "missing source: $SRC" >&2; exit 1; }
    cp "$SRC/orchestrate.py" "$SRC/pyproject.toml" "$SRC/uv.lock" "$REPO_DIR/orchestrate/"
    cp "$SRC"/tests/*.py "$REPO_DIR/orchestrate/tests/"
    echo "copied: orchestrate"
fi

cd "$REPO_DIR"
ALLOWED='Copyright \(c\) 2026 Dory Zidon|Built by \[Dory Zidon\]|"name": "Dory Zidon"|DoryZi/|doryzidon\.com'
if git ls-files --cached --others --exclude-standard -z | grep -zv '^uv.lock$\|/uv.lock$\|^scripts/sync-from-source.sh$' \
    | xargs -0 grep -nIiE 'dory|zidon|sara|toptal|a\.team|ateam|ashby|greenhouse|revolut|Resume_|resumes\.py|cover.letter|/home/|coreteam|pipeliner|Profile 3|ai_will_replace_you' \
    | grep -vE "$ALLOWED"; then
    echo "error: personal or machine-local text is left (lines above)" >&2
    exit 1
fi
echo "privacy check passed"
