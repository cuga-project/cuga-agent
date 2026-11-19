import { mkdir } from "node:fs/promises";
import { test, expect, type Page, type Locator } from "@playwright/test";
import { stub } from "./fixtures/carbon-backend";

async function settleScroll(page: Page) {
  // Native wheel and PageDown animations can outlive two animation frames,
  // especially in Firefox. Wait for every nested scroll owner to become quiet
  // before testing a subsequent focus/reveal operation.
  await page.evaluate(() => new Promise<void>((resolve, reject) => {
    let quietTimer: number;
    const cleanup = () => {
      window.clearTimeout(quietTimer);
      window.clearTimeout(deadline);
      window.removeEventListener("scroll", onScroll, true);
    };
    const onScroll = () => {
      window.clearTimeout(quietTimer);
      quietTimer = window.setTimeout(() => { cleanup(); resolve(); }, 150);
    };
    const deadline = window.setTimeout(() => { cleanup(); reject(new Error("Scrolling did not settle")); }, 5000);
    window.addEventListener("scroll", onScroll, true);
    onScroll();
  }));
}
async function screenshot(page: Page, name: string) {
  const directory = process.env.CUGA_DESIGN_SCREENSHOTS;
  if (!directory) return;
  await mkdir(directory, { recursive: true });
  await page.screenshot({ path: `${directory}/${name}-${page.context().browser()!.browserType().name()}.png`, animations: "disabled" });
}
async function exerciseBoundaries(page: Page, owner: Locator, mustScroll = true) {
  await owner.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  if (mustScroll) await expect.poll(() => owner.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await owner.hover();
  await page.mouse.wheel(0, 2000);
  await settleScroll(page);
  await page.mouse.wheel(0, 2000);
  await page.evaluate(() => window.scrollTo(0, document.scrollingElement!.scrollHeight));
  await settleScroll(page);
  await expectViewportContained(page);
}
async function expectViewportContained(page: Page) {
  const metrics = await page.evaluate(() => ({
    scrollY: window.scrollY,
    documentHeight: document.scrollingElement!.scrollHeight,
    viewportHeight: document.documentElement.clientHeight,
    rootTop: document.getElementById("root")!.getBoundingClientRect().top,
  }));
  expect(metrics.scrollY, JSON.stringify(metrics)).toBe(0);
  expect(metrics.documentHeight, JSON.stringify(metrics)).toBeLessThanOrEqual(metrics.viewportHeight + 1);
  expect(metrics.rootTop, JSON.stringify(metrics)).toBe(0);
}

test.describe("Scroll ownership and viewport containment", { tag: "@e2e" }, () => {
  test.describe.configure({ mode: "parallel" });
  test("supervisor scrolling stays inside its workspace at a short mobile height", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 640 });
    await stub(page);
    await page.goto("/manage/cuga-default");
    await expect(page.getByPlaceholder("Enter agent name")).toHaveValue("CUGA supervisor");
    const workspace = page.locator(".manage-layout");
    await workspace.evaluate((element) => { element.scrollTop = element.scrollHeight; });
    await workspace.hover({ position: { x: 150, y: 300 } });
    await page.mouse.wheel(0, 2000);
    await settleScroll(page);
    await page.mouse.wheel(0, 2000);
    await page.evaluate(() => window.scrollTo(0, document.scrollingElement!.scrollHeight));
    await settleScroll(page);
    await screenshot(page, "scroll-supervisor-mobile");
    await expectViewportContained(page);
  });
  const viewports = [
    { width: 1920, height: 1080 }, { width: 1440, height: 900 },
    { width: 1280, height: 480 }, { width: 1056, height: 640 },
    { width: 1055, height: 640 }, { width: 768, height: 640 },
    { width: 390, height: 640 }, { width: 320, height: 568 },
  ];
  for (const viewport of viewports) for (const id of ["weatherbot", "cuga-default"]) {
    if (viewport.width === 390 && id === "cuga-default") continue; // regression case above
    test(`${id} configuration scrolls at ${viewport.width}×${viewport.height}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await stub(page);
      await page.goto(`/manage/${id}`);
      await expect(page.getByPlaceholder("Enter agent name")).toHaveValue(id === "weatherbot" ? "Weather specialist" : "CUGA supervisor");
      const owner = page.locator(viewport.width >= 1056 ? ".manage-config-scroll" : ".manage-layout");
      await exerciseBoundaries(page, owner);
      if (id === "cuga-default") {
        await page.getByRole("switch", { name: "Plan approval", exact: true }).focus();
        await settleScroll(page);
        await expectViewportContained(page);
      }
      await page.getByRole("button", { name: "Agent", exact: true }).focus();
      await page.keyboard.press("End");
      await page.keyboard.press("PageDown");
      await settleScroll(page);
      await expectViewportContained(page);
      const publish = page.getByRole("button", { name: "Publish", exact: true });
      await publish.scrollIntoViewIfNeeded();
      await settleScroll(page);
      await expect(publish).toBeInViewport();
      await expect(publish).toBeEnabled();
      await expectViewportContained(page);
    });
  }

  for (const viewport of [{ width: 1440, height: 720 }, { width: 390, height: 640 }]) {
    test(`large dashboard stays bounded at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await stub(page);
      await page.route("**/api/agents", (route) => route.fulfill({ json: { agents: Array.from({ length: 45 }, (_, i) => ({ id: `agent-${i}`, name: `Agent ${i}`, description: "A long agent description. ".repeat(8), kind: "single" })) } }));
      await page.goto("/manage");
      await expect(page.locator(".manage-dashboard-card")).toHaveCount(45);
      await exerciseBoundaries(page, page.locator(".manage-dashboard-content"));
      await page.getByRole("searchbox", { name: "Search agents", exact: true }).fill("Agent 44");
      await expect(page.locator(".manage-dashboard-card")).toHaveCount(1);
      await expectViewportContained(page);
    });

    test(`long chat panels and history stay bounded at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await stub(page);
      await page.route("**/api/conversations?*", (route) => route.fulfill({ json: Array.from({ length: 60 }, (_, i) => ({ id: `thread-${i}`, title: `Conversation ${i} — ${"Long subject ".repeat(8)}`, timestamp: Date.parse("2026-10-31T08:00:00Z"), preview: "", status: "active" })) }));
      await page.route("**/api/conversation-stream-events/*", (route) => route.fulfill({ json: { events: Array.from({ length: 40 }, (_, i) => [{ event_name: "UserMessage", event_data: `Question ${i}`, sequence: i * 2, timestamp: "2026-10-31T08:00:00Z" }, { event_name: "Answer", event_data: `Answer ${i}\n\n${"Long answer paragraph. ".repeat(30)}`, sequence: i * 2 + 1, timestamp: "2026-10-31T08:00:00Z" }]).flat() } }));
      await page.route("**/api/workspace/tree*", (route) => route.fulfill({ json: { tree: Array.from({ length: 80 }, (_, i) => ({ type: "file", name: `Report-${i}-${"long-name-".repeat(8)}.json`, path: `report-${i}.json` })) } }));
      await page.goto("/chat/weatherbot");
      const conversations = page.getByRole("complementary", { name: "Conversations", exact: true });
      if (!await conversations.isVisible()) await page.getByRole("button", { name: "Open conversations", exact: true }).click();
      await expect(conversations.getByRole("button", { name: /Conversation 0/ })).toBeVisible();
      await exerciseBoundaries(page, conversations.locator(".chat-panel-scroll"));
      await conversations.getByRole("button", { name: /Conversation 0 —/ }).click();
      await expect(page.getByText("Question 39", { exact: true })).toBeAttached();
      if (await conversations.isVisible()) await conversations.getByRole("button", { name: "Close panel", exact: true }).click();
      const details = page.getByRole("complementary", { name: "Agent details", exact: true });
      if (!await details.isVisible()) await page.getByRole("button", { name: "Open agent panel", exact: true }).click();
      await exerciseBoundaries(page, details.locator(".chat-panel-scroll"));
      await details.getByRole("button", { name: "Close panel", exact: true }).click();
      const transcript = page.getByRole("region", { name: "Chat messages", exact: true });
      await transcript.hover();
      await page.mouse.wheel(0, -3000);
      await page.mouse.wheel(0, 8000);
      await settleScroll(page);
      await expectViewportContained(page);
      await expect(page.getByRole("region", { name: "Chat", exact: true }).getByRole("textbox")).toBeVisible();
    });

    test(`Memory user and administration views stay bounded at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await stub(page);
      await page.route("**/api/memory/entities?*", (route) => route.fulfill({ json: { total: 40, items: Array.from({ length: 40 }, (_, i) => ({ id: String(i), type: "fact", content: "A saved preference. ".repeat(30), metadata: { title: `Memory ${i}` } })) } }));
      await page.goto("/chat/weatherbot?view=memory");
      await expect(page.getByText("Your view of this agent's memory", { exact: true })).toBeVisible();
      await exerciseBoundaries(page, page.locator(".memory-workspace"));
      await page.getByRole("button", { name: "Administration", exact: true }).click();
      for (const name of ["Settings", "Memory", "Activity"]) {
        await page.getByRole("tab", { name, exact: true }).click();
        await exerciseBoundaries(page, page.locator(".memory-workspace"), false);
      }
      await page.getByRole("button", { name: "Back to chat", exact: true }).click();
      await expect(page.getByRole("region", { name: "Chat", exact: true })).toBeVisible();
      await expectViewportContained(page);
    });

    test(`all 12 Studio sections stay bounded at ${viewport.width}px`, async ({ page }) => {
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.setViewportSize(viewport);
      await stub(page);
      await page.route("**/api/events/status", (route) => route.fulfill({ json: { enabled: true, ok: true, scope: "test", backends: [], features: {}, ap_configured: false } }));
      await page.route("**/docs", (route) => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>API reference fixture</title><h1>API reference</h1>" }));
      await page.goto("/studio");
      const sections = ["Dashboard", "Concierge", "Sub-agents", "Channels", "Integrations", "Setup", "Flows", "Runs", "Examples", "API", "Profile", "Admin"];
      for (const section of sections) {
        if (viewport.width < 1056) await page.getByLabel("Studio section", { exact: true }).selectOption({ label: section });
        else await page.getByRole("navigation", { name: "Studio sections", exact: true }).getByRole("link", { name: section, exact: true }).click();
        await expect(page.getByRole("heading", { name: section, exact: true })).toBeVisible();
        await exerciseBoundaries(page, page.locator(".studio-content"), false);
      }
      expect(errors).toEqual([]);
    });

    test(`configuration modal tabs and close restore scroll at ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await stub(page);
      await page.route("**/api/knowledge/documents*", (route) => route.fulfill({ json: { documents: Array.from({ length: 30 }, (_, i) => ({ filename: `Document-${i}.pdf`, chunk_count: 20, ingested_at: "2026-10-31T08:00:00Z" })) } }));
      await page.goto("/manage/weatherbot");
      await expect(page.getByPlaceholder("Enter agent name")).toHaveValue("Weather specialist");
      await page.getByRole("button", { name: "Knowledge", exact: true }).click();
      await page.getByRole("button", { name: "Configure knowledge base", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Knowledge Base", exact: true });
      await expect(dialog.getByText("Document-29.pdf", { exact: true })).toBeAttached();
      await exerciseBoundaries(page, dialog.locator(".cds--modal-scroll-content"));
      for (const tab of ["Search Test", "Knowledge harness", "Settings"]) {
        await dialog.getByRole("tab", { name: tab, exact: true }).click();
        await exerciseBoundaries(page, dialog.locator(".cds--modal-scroll-content"), false);
      }
      await dialog.getByRole("button", { name: "Close", exact: true }).first().click();
      await expect(dialog).toBeHidden();
      await expectViewportContained(page);
      await expect(page.getByRole("button", { name: "Publish", exact: true })).toBeVisible();
    });
  }

  for (const role of ["ServiceOwner", "ServiceAdmin", "Viewer"]) {
    test(`${role} navigation and role guard`, async ({ page }) => {
      await stub(page);
      const canManage = role !== "Viewer";
      await page.route("**/api/auth/config", (route) => route.fulfill({ json: { enabled: true, authorization_enabled: true } }));
      await page.route("**/auth/userinfo", (route) => route.fulfill({ json: { name: "UI review", roles: [role], can_manage: canManage } }));
      await page.goto("/chat/weatherbot");
      const nav = page.getByRole("navigation", { name: "CUGA", exact: true });
      await expect(nav.getByRole("link", { name: "Chat", exact: true })).toBeVisible();
      if (canManage) await expect(nav.getByRole("link", { name: "Agents", exact: true })).toBeVisible();
      else await expect(nav.getByRole("link", { name: "Agents", exact: true })).toHaveCount(0);
      await page.goto("/manage/weatherbot");
      if (canManage) await expect(page.getByPlaceholder("Enter agent name")).toHaveValue("Weather specialist");
      else await expect(page.getByRole("heading", { name: "Access Denied", exact: true })).toBeVisible();
      await expectViewportContained(page);
    });
  }

});
