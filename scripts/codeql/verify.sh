#!/usr/bin/env bash
#
# Check, on your own machine, that a branch closes the CodeQL alerts it claims to.
#
# GitHub only re-scans after a change is merged, so without this you find out
# whether a fix worked after it has already landed. This builds a CodeQL database
# from the current working tree, runs the same set of queries GitHub runs, and
# checks two things:
#
#   1. every entry in the chosen list produces no results   (the fix worked)
#   2. no result appears that the starting point did not have (nothing else broke)
#
# The second check matters. A change that stops one report by moving the problem
# somewhere else would still pass the first check on its own.
#
# Usage:
#   scripts/codeql/verify.sh --manifest <file> [--baseline <git-ref>] [--keep]
#
# The lists live in scripts/codeql/expected-closed/. A list only passes on a
# branch that actually contains the matching fix.
#
# Needs the codeql command available. Note that the query pack version is fixed
# below: a newer pack cannot be read by an older codeql version, so asking for
# the latest one fails on an older install.
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

if [ -z "$BASELINE_REF" ]; then
  echo "--baseline is required: without a starting point to compare against, an" >&2
  echo "alert that was never reported looks identical to one that was fixed." >&2
  exit 2
fi

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

# Skip third-party and virtual environment directories when scanning. The
# --codescanning-config option would do this too, but it also makes the database
# step fetch the newest query pack, which is the version problem noted above.
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
