rm -rf ./dist
NODE_ENV=production pnpm run build

# Copy build output into the cuga Python package so it gets included in the PyPI wheel
CUGA_PKG_DIR="../../cuga"

rm -rf "$CUGA_PKG_DIR/frontend/dist"
mkdir -p "$CUGA_PKG_DIR/frontend"
cp -r ./dist "$CUGA_PKG_DIR/frontend/dist"
