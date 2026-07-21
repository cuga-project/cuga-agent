import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the CUGA frontend (slash-commands feature verification).
 *
 * Boots a small Node static server (``tests/e2e/static-server.cjs``) on port
 * 3002 that serves the production build out of ``dist/`` with SPA fallback —
 * run ``pnpm run build`` first to populate ``dist/``. All backend HTTP is
 * stubbed per-test via ``page.route``, so no real backend is required.
 * ``reuseExistingServer: true`` lets iterative runs skip the static-server
 * boot when one is already listening.
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
    baseURL: "http://localhost:3002",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    // Serve the production build statically. All backend HTTP is stubbed via
    // ``page.route`` in the spec, so we don't need the webpack dev server's
    // proxy. ``--single`` enables SPA fallback so ``/chat`` returns
    // ``index.html`` instead of 404. Run ``pnpm run build`` before invoking
    // the tests (or chain it from the npm script).
    command: "node tests/e2e/static-server.cjs",
    url: "http://localhost:3002",
    reuseExistingServer: true,
    timeout: 60_000,
    stdout: "ignore",
    stderr: "pipe",
  },
});
