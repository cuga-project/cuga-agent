#!/usr/bin/env bash
#
# Build the frontend (production) and sync it into the Python package's
# served dist/ directory.
#
# WHY THIS EXISTS — webpack writes the built bundle to
#   src/frontend_workspaces/frontend/dist/
# but the FastAPI server serves
#   src/cuga/frontend/dist/   (PACKAGE_ROOT/frontend/dist; see
#   src/cuga/backend/server/main.py -> FRONTEND_DIST_DIR)
# which is a SEPARATE, git-tracked copy. A bare `pnpm build` leaves the
# served copy stale, so frontend changes silently never reach
# `cuga start` — every "I rebuilt but still see the old behavior" trace
# is this gap. Run THIS instead of a bare build whenever FE source changes.
#
# NODE_ENV=production is required: webpack.config.js gates both `mode` and
# Terser minification on it (line 48/69). Without it you get a ~4x larger,
# unminified dev bundle with console.* intact.
#
# Usage:  bash scripts/build_frontend.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WS_DIR="$REPO_ROOT/src/frontend_workspaces"
SRC_DIST="$WS_DIR/frontend/dist"
PKG_DIST="$REPO_ROOT/src/cuga/frontend/dist"

echo "==> Building frontend (NODE_ENV=production)…"
( cd "$WS_DIR" && NODE_ENV=production pnpm --filter ./frontend build )

if [ ! -f "$SRC_DIST/index.html" ]; then
  echo "ERROR: build produced no $SRC_DIST/index.html" >&2
  exit 1
fi

echo "==> Syncing $SRC_DIST -> $PKG_DIST (source maps excluded)…"
rm -rf "$PKG_DIST"
mkdir -p "$PKG_DIST"
( cd "$SRC_DIST" && find . -type f ! -name '*.map' -print0 | while IFS= read -r -d '' f; do
    mkdir -p "$PKG_DIST/$(dirname "$f")"
    cp "$f" "$PKG_DIST/$f"
  done )

echo "==> Served bundle is now:"
grep -oE '(main|vendors|tailwind)\.[a-z0-9]*\.?js' "$PKG_DIST/index.html" | sed 's/^/      /'
echo "==> Done. Restart cuga (and hard-refresh the browser) to serve it."
