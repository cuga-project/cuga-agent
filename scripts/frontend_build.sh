#!/usr/bin/env bash
# frontend_build.sh — compile the CUGA React frontend (incl. the events Studio) and publish it to
# the directory FastAPI serves. The server serves the PRE-BUILT bundle at src/cuga/frontend/dist;
# editing *.tsx does nothing until you rebuild. Run this after any frontend change.
#
#   scripts/frontend_build.sh
#
# This is a THIN WRAPPER around src/frontend_workspaces/frontend/build.sh, which owns the actual
# build + copy-into-the-package (and, crucially, sets NODE_ENV=production so the bundle is minified
# — a bare `pnpm run build` defaults to a DEVELOPMENT build: unminified, ~3.5x larger, and a
# ~450k-line git diff). Keeping the build logic in one place means the two can't drift; this script
# only adds the toolchain setup (corepack/pnpm, workspace install) and a post-build sanity check.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
WS="$ROOT/src/frontend_workspaces"
FE="$WS/frontend"
SERVED="$ROOT/src/cuga/frontend/dist"

command -v node >/dev/null || { echo "need node"; exit 1; }
if ! command -v pnpm >/dev/null; then
  echo "pnpm not found — enabling via corepack"
  corepack enable 2>/dev/null || true
  corepack prepare pnpm@10.28.0 --activate
fi

echo "== install workspace deps (first run downloads Carbon/React — a few min) =="
( cd "$WS" && pnpm install )

echo "== build + publish (delegates to frontend/build.sh — production, copies into the package) =="
( cd "$FE" && bash build.sh )

BUNDLE=$(ls -1 "$SERVED"/main.*.js 2>/dev/null | head -1)
if grep -ql "StudioPage\|/studio" "$BUNDLE" 2>/dev/null; then
  echo "  ✓ Studio present in $(basename "$BUNDLE")"
else
  echo "  ⚠ built, but Studio markers not found in the bundle (may be minified)"
fi
echo ""
echo "Frontend published. Restart the CUGA server to serve it, then open /studio."
