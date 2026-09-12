#!/usr/bin/env bash
# frontend_build.sh — compile the CUGA React frontend (incl. the events Studio) and publish it to
# the directory FastAPI serves. The server serves the PRE-BUILT bundle at src/cuga/frontend/dist;
# editing *.tsx does nothing until you rebuild. Run this after any frontend change.
#
#   scripts/frontend_build.sh
#
# Requires node + pnpm (the workspace is a pnpm monorepo). If pnpm is missing, we enable it via
# corepack (bundled with node).
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

echo "== build frontend (webpack, PRODUCTION) =="
# NODE_ENV=production is REQUIRED, not a nicety. webpack.config.js keys `mode`, minification,
# console-stripping and source-maps off `process.env.NODE_ENV === "production"`, and `pnpm run
# build` sets nothing — so a bare build is a DEVELOPMENT build: unminified, with source maps, ~3.5x
# larger. Committing that balloons the served bundle (main.js 1.3MB→4.6MB, vendors 7MB→24MB) and
# turns a routine rebuild into a ~450k-line git diff, because the dev bundle has real newlines
# where the production one is a single minified line.
( cd "$FE" && NODE_ENV=production pnpm run build )

echo "== publish → $SERVED =="
rm -rf "$SERVED"
cp -r "$FE/dist" "$SERVED"

BUNDLE=$(ls -1 "$SERVED"/main.*.js 2>/dev/null | head -1)
if grep -ql "StudioPage\|/studio" "$BUNDLE" 2>/dev/null; then
  echo "  ✓ Studio present in $(basename "$BUNDLE")"
else
  echo "  ⚠ built, but Studio markers not found in the bundle (may be minified)"
fi
echo ""
echo "Frontend published. Restart the CUGA server to serve it, then open /studio."
