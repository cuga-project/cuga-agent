const { chromium } = require('playwright');
const fs = require('fs');

// Usage: node record_demo.spec.js [URL]
// Example: node record_demo.spec.js http://localhost:3000
(async () => {
  const url = process.argv[2] || process.env.DEMO_URL || 'http://localhost:3000';
  const outDir = 'demo-recordings';
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    recordVideo: { dir: outDir, size: { width: 1280, height: 720 } },
  });
  const page = await context.newPage();

  console.log('Opening', url);
  await page.goto(url, { waitUntil: 'networkidle' });

  // Short waits to let the app bootstrap. Adjust selectors if your app differs.
  await page.waitForTimeout(1000);

  // If your app has a chat input with a known selector, update this.
  const inputSelector = 'textarea, input[type=text], [contenteditable="true"]';
  await page.waitForSelector(inputSelector, { timeout: 5000 });

  // Focus the input and type a query that triggers the fake stream
  await page.click(inputSelector);
  await page.keyboard.type('Show processing indicator demo');

  // Submit — try Enter key; if your UI needs a button, change selector accordingly
  await page.keyboard.press('Enter');

  // Wait long enough to capture pre-token latency + streaming
  console.log('Recording demo — waiting 12s to capture pre-token phase and streaming');
  await page.waitForTimeout(12000);

  // Stop and save video
  const video = await page.video().path();
  console.log('Video saved to:', video);

  await browser.close();
  console.log('Done. Files in', outDir);
})();
