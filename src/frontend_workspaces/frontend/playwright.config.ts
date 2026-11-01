import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.CUGA_TEST_PORT) || 3002;
const baseURL = `http://localhost:${port}`;

/**
 * Browser regression tests for the CUGA frontend.
 *
 * Boots a small Node static server (``tests/e2e/static-server.cjs``) on port
 * 3002 that serves the production build out of ``dist/`` with SPA fallback —
 * run ``pnpm run build`` first to populate ``dist/``. All backend HTTP is
 * stubbed per-test via ``page.route``, so no real backend is required.
 * ``reuseExistingServer: true`` lets iterative runs skip the static-server
 * boot when one is already listening.
 * CUGA_CROSS_BROWSER=1 adds Firefox and WebKit. CUGA_FRONTEND_DIST and
 * CUGA_TEST_PORT select a separate packaged build and server for verification.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  // Enable the extended browser matrix with CUGA_CROSS_BROWSER=1.
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    ...(process.env.CUGA_CROSS_BROWSER === "1" ? [
      { name: "firefox", use: { ...devices["Desktop Firefox"] } },
      { name: "webkit", use: { ...devices["Desktop Safari"] } },
    ] : []),
  ],
  webServer: {
    // Serve the production build statically. All backend HTTP is stubbed via
    // ``page.route`` in the spec, so we don't need the webpack dev server's
    // proxy. ``--single`` enables SPA fallback so ``/chat`` returns
    // ``index.html`` instead of 404. Run ``pnpm run build`` before invoking
    // the tests (or chain it from the npm script).
    command: "node tests/e2e/static-server.cjs",
    env: { PORT: String(port) },
    url: baseURL,
    reuseExistingServer: true,
    timeout: 60_000,
    stdout: "ignore",
    stderr: "pipe",
  },
});
