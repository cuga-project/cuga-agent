Demo recording instructions
==========================

This document explains how to record a quick demo showing the `turnInFlight` UI behavior (pre-token processing indicator).

Prerequisites
- Node.js (16+)
- Playwright (will be installed via npm)
- A running frontend dev server (e.g. `pnpm start` or your usual dev command) serving the chat UI at `http://localhost:3000`

Steps
1. Ensure the frontend is running locally. If you rely on the fake stream mode, enable `FAKE_STREAM` when building or set it in the environment so the UI simulates a slow LLM.
2. Install Playwright dependencies (once):

```bash
npm init -y
npm i -D playwright
npx playwright install
```

3. Run the recorder script (passes URL as first arg or use `DEMO_URL` env var):

```bash
node scripts/playwright/record_demo.spec.js http://localhost:3000
```

4. After the script completes, find the recorded video under `demo-recordings/`.

Notes
- The script uses a generic input selector; if your chat input uses a custom element, update `scripts/playwright/record_demo.spec.js` accordingly.
- If you'd like, I can run this here to produce a recording (I'll install Playwright and run it). Reply `run demo` to let me proceed.
