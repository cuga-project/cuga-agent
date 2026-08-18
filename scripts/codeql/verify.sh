#!/usr/bin/env bash
#
# Prove locally that a branch closes the CodeQL alerts it claims to.
#
# GitHub only rescans after a merge to main, so without this you find out whether
# a fix worked *after* it has landed. This builds a database from the working
# tree, runs the same query suite default setup runs, and checks two things:
#
#   1. every row in the manifest produces zero results   (the fix worked)
#   2. no result appears that the baseline did not have  (the fix broke nothing)
#
# Check 2 is the one that catches a "fix" that just moved the leak somewhere else.
#
# Usage:
#   scripts/codeql/verify.sh --manifest <file> [--baseline <git-ref>] [--keep]
#
# Manifests live in scripts/codeql/expected-closed/. A manifest only passes on a
# branch that contains the matching fix.
#
# Requires the codeql CLI on PATH. Note the pack is pinned: CLI 2.26.2 cannot
# parse a manifest produced by 2.26.3, so "latest" breaks on an older CLI.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
MANIFEST=""
BASELINE_REF=""
KEEP=0
QUERY_PACK="codeql/python-queries@1.8.7"
SUITE="codeql-suites/python-code-scanning.qls"

while [ $# -gt 0 ]; do
  case "$1" in
    --baseline) BASELINE_REF="$2"; shift 2 ;;
    --manifest) MANIFEST="$2"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$MANIFEST" ]; then
  echo "--manifest is required; pick one from scripts/codeql/expected-closed/" >&2
  ls "$REPO_ROOT/scripts/codeql/expected-closed/"*.txt >&2
  exit 2
fi

WORKDIR="$(mktemp -d)"
BASE_WORKTREE=""

# Registered up-front, not after the analysis: `set -e` means a failing baseline
# analysis aborts the script, and deleting the directory without deregistering
# the worktree leaves a stale entry in .git/worktrees pointing at nothing.
cleanup() {
  if [ -n "$BASE_WORKTREE" ]; then
    git -C "$REPO_ROOT" worktree remove --force "$BASE_WORKTREE" 2>/dev/null || true
  fi
  [ "$KEEP" -eq 1 ] || rm -rf "$WORKDIR"
}
trap cleanup EXIT
echo "scratch: $WORKDIR"

# Keep the extractor out of vendored and virtualenv trees. Passing
# --codescanning-config instead would make `database create` resolve the newest
# query pack, which is exactly the version skew this script pins around.
export LGTM_INDEX_FILTERS=$'exclude:**/node_modules/**\nexclude:.venv*/**\nexclude:src/frontend_workspaces/**\nexclude:src/cuga/frontend/dist/**'

analyze() {  # <source-root> <db-path> <sarif-out>
  codeql database create "$2" --language=python --source-root="$1" --overwrite --quiet
  codeql database analyze "$2" "${QUERY_PACK}:${SUITE}" \
    --additional-packs="$REPO_ROOT/.github/codeql/extensions" \
    --model-packs=cuga-project/workspace-path-sanitizers \
    --format=sarif-latest --output="$3" --quiet
}

echo "==> analyzing working tree"
analyze "$REPO_ROOT" "$WORKDIR/db-head" "$WORKDIR/head.sarif"

BASELINE_SARIF=""
if [ -n "$BASELINE_REF" ]; then
  echo "==> analyzing baseline $BASELINE_REF"
  git -C "$REPO_ROOT" worktree add --detach "$WORKDIR/base" "$BASELINE_REF" >/dev/null
  BASE_WORKTREE="$WORKDIR/base"
  analyze "$WORKDIR/base" "$WORKDIR/db-base" "$WORKDIR/base.sarif"
  BASELINE_SARIF="$WORKDIR/base.sarif"
fi

python3 "$REPO_ROOT/scripts/codeql/check_sarif.py" \
  --manifest "$MANIFEST" \
  --sarif "$WORKDIR/head.sarif" \
  ${BASELINE_SARIF:+--baseline-sarif "$BASELINE_SARIF"}
