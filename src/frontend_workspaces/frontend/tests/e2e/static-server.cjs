/**
 * Zero-dependency static file server for Playwright e2e tests.
 *
 * Serves the webpack ``dist/`` build on port 3002 with SPA fallback (any
 * non-asset path returns ``index.html`` so React Router's ``/chat`` route
 * resolves on a fresh page load).
 *
 * Used instead of ``serve`` / webpack-dev-server because both pull in
 * path-to-regexp v6, which the workspace pins via transitive deps but which
 * is broken under Node 24+ (default export was removed). This file has no
 * dependencies beyond the Node standard library.
 */
const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = Number(process.env.PORT) || 3002;
const ROOT = path.resolve(__dirname, "..", "..", "dist");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".map": "application/json; charset=utf-8",
};

function resolveFile(urlPath) {
  let p = decodeURIComponent(urlPath.split("?")[0]);
  if (p.endsWith("/")) p = p + "index.html";
  const candidate = path.resolve(ROOT, "." + (p.startsWith("/") ? p : "/" + p));
  const rel = path.relative(ROOT, candidate);
  if (rel.startsWith("..") || path.isAbsolute(rel)) return path.join(ROOT, "index.html");
  if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  return path.join(ROOT, "index.html");
}

http
  .createServer((req, res) => {
    const file = resolveFile(req.url || "/");
    const ext = path.extname(file).toLowerCase();
    res.writeHead(200, {
      "Content-Type": MIME[ext] || "application/octet-stream",
      "Cache-Control": "no-store",
    });
    fs.createReadStream(file).pipe(res);
  })
  .listen(PORT, () => {
    console.log(`Static server on http://localhost:${PORT} serving ${ROOT}`);
  });
