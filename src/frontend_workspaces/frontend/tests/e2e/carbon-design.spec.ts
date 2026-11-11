import { mkdir } from "node:fs/promises";
import { test, expect, type Page } from "@playwright/test";
import { stub } from "./fixtures/carbon-backend";

async function capture(page: Page, name: string) {
  const directory = process.env.CUGA_DESIGN_SCREENSHOTS;
  if (!directory) return;
  await mkdir(directory, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${directory}/${name}.png`, fullPage: true, animations: "disabled" });
}


test.describe("Carbon design and responsive interactions", { tag: "@e2e" }, () => {
  test("dashboard search and card actions work without nested interactive controls", async ({ page }) => {
    await stub(page);
    await page.goto("/manage");
    await expect(page.getByRole("heading", { name: "Agent dashboard" })).toBeVisible();
    await expect(page.locator(".manage-dashboard-card")).toHaveCount(3);
    await capture(page, "dashboard-desktop");
    await expect(page.locator(".manage-dashboard-card a a, .manage-dashboard-card button button")).toHaveCount(0);
    await page.getByRole("searchbox", { name: "Search agents" }).fill("weather");
    await expect(page.locator(".manage-dashboard-card")).toHaveCount(1);
    await page.getByRole("link", { name: "Configure & try it out" }).click();
    await expect(page).toHaveURL(/\/manage\/weatherbot$/);
    await expect(page.getByPlaceholder("Enter agent name")).toHaveValue("Weather specialist");
    await capture(page, "agent-configuration");
  });
  test("deleting requires a Carbon modal confirmation and cancellation preserves the agent", async ({ page }) => {
    const deletions = await stub(page);
    await page.goto("/manage");
    await page.getByRole("button", { name: "Delete agent Weather specialist", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText('Delete agent "Weather specialist"?');
    await expect(dialog.getByRole("button", { name: "Delete agent", exact: true })).toBeVisible();
    expect(deletions).toEqual([]);
    await capture(page, "delete-confirmation");
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(deletions).toEqual([]);
  });
  test("profile toggles closed and Escape restores focus", async ({ page }) => {
    await stub(page);
    await page.goto("/manage");
    const profile = page.getByRole("button", { name: "User profile", exact: true });
    await profile.click();
    await expect(page.getByText("Guest", { exact: true })).toBeVisible();
    await profile.click();
    await expect(page.getByText("Guest", { exact: true })).toBeHidden();
    await profile.click();
    await page.keyboard.press("Escape");
    await expect(profile).toBeFocused();
    await expect(page.getByText("Guest", { exact: true })).toBeHidden();
  });
  test("desktop panels reserve space for the chat and the detail tabs support arrow keys", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await stub(page);
    await page.goto("/chat/weatherbot");
    const left = page.getByRole("complementary", { name: "Conversations", exact: true });
    const right = page.getByRole("complementary", { name: "Agent details", exact: true });
    await expect(left).toBeVisible();
    await expect(right).toBeVisible();
    await expect(page.getByRole("heading", { name: "Hello, how can I help you today?", exact: true })).toBeVisible();
    await capture(page, "chat-desktop");
    await expect(page.getByRole("button", { name: "Toggle Debug Panel", exact: true })).toHaveCount(0);
    const chatBox = await page.locator(".chat-content-area").boundingBox();
    const leftBox = await left.boundingBox();
    const rightBox = await right.boundingBox();
    expect(chatBox!.x).toBeGreaterThanOrEqual(leftBox!.x + leftBox!.width);
    expect(chatBox!.x + chatBox!.width).toBeLessThanOrEqual(rightBox!.x);
    const files = page.getByRole("tab", { name: "Files", exact: true });
    await files.focus();
    await files.press("ArrowRight");
    await expect(page.getByRole("tab", { name: "Knowledge", exact: true })).toHaveAttribute("aria-selected", "true");
  });
  test("mobile navigation and both side panels remain available without horizontal overflow", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await stub(page);
    await page.goto("/chat/weatherbot");
    await page.getByRole("button", { name: "Open navigation", exact: true }).click();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Open navigation", exact: true })).toBeFocused();
    await expect(page.getByRole("navigation", { name: "CUGA navigation", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Open navigation", exact: true }).click();
    await page.getByRole("navigation", { name: "CUGA navigation", exact: true }).getByRole("link", { name: "Chat", exact: true }).click();
    await page.getByRole("button", { name: "Open conversations", exact: true }).click();
    await expect(page.getByRole("complementary", { name: "Conversations", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Close side panel", exact: true }).click({ position: { x: 370, y: 400 } });
    await page.getByRole("button", { name: "Open agent panel", exact: true }).click();
    await expect(page.getByRole("complementary", { name: "Agent details", exact: true })).toBeVisible();
    await capture(page, "chat-mobile");
    await page.getByRole("button", { name: "Close panel", exact: true }).last().click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
  test("dashboard fits a narrow viewport and reports an empty search", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 740 });
    await stub(page);
    await page.goto("/manage");
    await page.getByRole("searchbox", { name: "Search agents" }).fill("no match");
    await expect(page.getByRole("heading", { name: "No matching agents" })).toBeVisible();
    await capture(page, "dashboard-mobile");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByRole("button", { name: "Clear search", exact: true }).click();
    await expect(page.locator(".manage-dashboard-card")).toHaveCount(3);
  });
  test("knowledge upload and citation settings retain the active conversation scope", async ({ page }) => {
    await stub(page);
    const documents: Array<{ filename: string; status: string; chunk_count: number }> = [];
    const citationChanges: boolean[] = [];
    let uploadRequest: { body: string; thread: string | undefined } | undefined;
    await page.route("**/api/knowledge/documents*", async (route) => {
      if (route.request().method() === "POST") {
        uploadRequest = { body: route.request().postData() || "", thread: route.request().headers()["x-thread-id"] };
        documents.push({ filename: "visit-notes.txt", status: "completed", chunk_count: 1 });
        await route.fulfill({ json: { tasks: [{ status: "completed", filename: "visit-notes.txt" }] } });
      } else await route.fulfill({ json: { documents: new URL(route.request().url()).searchParams.get("scope") === "agent" ? [] : documents } });
    });
    await page.route("**/api/knowledge/session/settings*", async (route) => {
      if (route.request().method() === "PATCH") citationChanges.push(route.request().postDataJSON().citations_enabled);
      await route.fulfill({ json: { citations_enabled: citationChanges.at(-1) ?? true } });
    });
    await page.goto("/chat/weatherbot");
    // Per-conversation settings need a conversation: a new chat has no ID yet (#774).
    await page.getByRole("button", { name: /Plan next week's customer visits/ }).click();
    await page.getByRole("tab", { name: "Knowledge", exact: true }).click();
    const panel = page.locator(".knowledge-panel");
    const toggle = panel.getByRole("switch", { name: "Citations for this chat", exact: true });
    await expect(toggle).toBeChecked();
    await toggle.focus();
    await toggle.press("Space");
    await expect.poll(() => citationChanges).toEqual([false]);
    await panel.locator('input[type="file"]').setInputFiles({ name: "visit-notes.txt", mimeType: "text/plain", buffer: Buffer.from("Customer visit notes") });
    await expect(panel.getByText("visit-notes.txt", { exact: true })).toBeVisible();
    expect(uploadRequest?.body).toContain('name="scope"');
    expect(uploadRequest?.body).toContain("session");
    expect(uploadRequest?.thread).toBe("recent");
    await capture(page, "knowledge");
  });
  test("skip link keeps the current route and reaches visible memory content", async ({ page }) => {
    await stub(page);
    await page.goto("/chat/weatherbot");
    await page.getByRole("link", { name: "Memory", exact: true }).click();
    const link = page.getByRole("link", { name: "Skip to main content", exact: true });
    await link.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/chat\/weatherbot\?view=memory#main-content$/);
    await expect(page.locator("#main-content")).toBeVisible();
    await expect(page.locator("#main-content")).toBeFocused();
    await capture(page, "memory");
  });

  test("conversation removal uses a Carbon confirmation before deleting", async ({ page }) => {
    const deletions = await stub(page);
    await page.goto("/chat/weatherbot");
    await page.getByRole("button", { name: "Remove all", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Confirm action", exact: true });
    await expect(dialog).toContainText("Remove all conversations? This cannot be undone.");
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    expect(deletions).toEqual([]);
    await expect(page.getByRole("button", { name: /Plan next week's customer visits/ })).toBeVisible();
    await page.getByRole("button", { name: "Remove all", exact: true }).click();
    await dialog.getByRole("button", { name: "Remove all conversations", exact: true }).click();
    await expect.poll(() => deletions.some((path) => path.includes("conversation"))).toBe(true);
  });
  test("secret deletion confirmation restores its parent dialog after cancellation", async ({ page }) => {
    const deletions = await stub(page);
    await page.route("**/api/secrets*", (route) => route.fulfill({ json: { secrets: [{ id: "weather-key", source: "db", agent_id: "weatherbot" }] } }));
    await page.route("**/api/secrets/config", (route) => route.fulfill({ json: { mode: "local", force_env: false } }));
    await page.goto("/manage/weatherbot");
    await page.getByRole("button", { name: "Manage Secrets", exact: true }).click();
    const parent = page.getByRole("dialog", { name: "Secrets Manager", exact: true });
    await parent.getByRole("button", { name: "Delete", exact: true }).click();
    const confirmation = page.getByRole("dialog", { name: "Confirm action", exact: true });
    await expect(confirmation).toContainText('Delete secret "weather-key"?');
    await expect(parent).toBeHidden();
    await confirmation.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(parent).toBeVisible();
    await expect(parent.getByText("weather-key", { exact: true })).toBeVisible();
    expect(deletions).toEqual([]);
  });
  test("Studio token entry uses a masked Carbon input and a notification", async ({ page }) => {
    await stub(page);
    let connected = false;
    await page.route("**/api/events/status", (route) => route.fulfill({ json: { enabled: true, ok: true, scope: "test", backends: [], features: {}, ap_configured: false } }));
    await page.route("**/api/events/integrations", (route) => route.fulfill({ json: { integrations: [{ name: "github", label: "GitHub", auth: "token", status: "not_connected", backend: "direct", needs_connection: true }] } })); // pragma: allowlist secret -- Mock authentication-method enum, not a token value.
    await page.route("**/api/events/connect/github/token", (route) => {
      connected = true;
      return route.fulfill({ json: { ok: true } });
    });
    await page.goto("/studio");
    await page.getByRole("link", { name: "Integrations", exact: true }).click();
    await page.getByRole("button", { name: "Connect", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Enter value", exact: true });
    const input = dialog.getByLabel("Paste your GitHub token:", { exact: true });
    await expect(input).toHaveAttribute("type", "password");
    await input.fill("sample-token");
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    expect(connected).toBe(false);
    await page.getByRole("button", { name: "Connect", exact: true }).click();
    await input.fill("sample-token");
    await capture(page, "studio-token-entry");
    await dialog.getByRole("button", { name: "Continue", exact: true }).click();
    await expect(page.getByText("GitHub connected.", { exact: true })).toBeVisible();
    expect(connected).toBe(true);
  });

  test("configuration chat fills its preview before and after sending a message", async ({ page }) => {
    await page.setViewportSize({ width: 1728, height: 1000 });
    await stub(page);
    await page.route("**/stream", (route) => route.fulfill({
      contentType: "text/event-stream", body: "event: Answer\ndata: Response completed.\n\n",
    }));
    await page.goto("/manage/weatherbot");
    const preview = page.locator(".manage-chat-wrap");
    await expect(preview.getByRole("textbox")).toBeVisible();
    const expectFullPreview = async () => {
      await expect(preview.locator(":scope > [hidden]")).toBeHidden();
      await expect(preview.getByRole("button", { name: "Toggle Debug Panel", exact: true })).toHaveCount(0);
      const container = await preview.boundingBox();
      const chat = await preview.locator(".cuga-carbon-chat-wrapper").boundingBox();
      expect(Math.abs(chat!.y - container!.y)).toBeLessThanOrEqual(1);
      expect(Math.abs(chat!.height - container!.height)).toBeLessThanOrEqual(1);
    };
    await expectFullPreview();
    await preview.getByRole("textbox").fill("Hi");
    await preview.getByRole("textbox").press("Enter");
    await expect(preview.getByText("Response completed.", { exact: true })).toBeVisible();
    await expectFullPreview();
    await capture(page, "configuration-chat-filled");
  });
  test("agent selection has one toolbar divider and keeps the supervisor label on one line", async ({ page }) => {
    await stub(page);
    await page.goto("/chat/weatherbot");
    const switcher = page.locator(".chat-agent-switcher");
    const dropdown = switcher.locator(".cds--dropdown");
    await expect(dropdown).toHaveCSS("border-bottom-color", "rgba(0, 0, 0, 0)");
    await switcher.getByRole("combobox", { name: "Agent", exact: true }).click();
    await page.getByRole("option", { name: "CUGA supervisor Supervisor", exact: true }).click();
    await expect(page).toHaveURL(/\/chat$/);
    const option = page.locator(".chat-agent-switcher .chat-agent-option");
    const name = await option.locator(".chat-agent-option-name").boundingBox();
    const badge = await option.locator(".cds--tag").boundingBox();
    expect(badge!.y).toBeGreaterThanOrEqual(name!.y - 2);
    expect(badge!.y + badge!.height).toBeLessThanOrEqual(name!.y + name!.height + 2);
    await capture(page, "supervisor-selector");
  });

  test("initial agent load reserves card geometry and never shows an empty dashboard", async ({ page }) => {
    await stub(page);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/agents", async (route) => { await gate; await route.fallback(); });
    await page.goto("/manage");
    const loading = page.getByRole("status", { name: "Loading agents", exact: true });
    await expect(loading).toBeVisible();
    await expect(loading.locator(".cds--tile")).toHaveCount(3);
    await expect(page.getByText("No agents yet", { exact: true })).toHaveCount(0);
    await capture(page, "loading-dashboard");
    release();
    await expect(loading).toHaveCount(0);
    await expect(page.locator(".manage-dashboard-card")).toHaveCount(3);
  });
  test("configuration waits for data before exposing editable defaults or publishing", async ({ page }) => {
    await stub(page);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/manage/config?*", async (route) => { await gate; await route.fallback(); });
    await page.goto("/manage/weatherbot");
    const loading = page.getByRole("status", { name: "Loading agent configuration…", exact: true });
    await expect(loading).toBeVisible();
    await expect(page.getByPlaceholder("Enter agent name")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Publish", exact: true })).toBeDisabled();
    await capture(page, "loading-configuration");
    release();
    await expect(page.getByPlaceholder("Enter agent name")).toHaveValue("Weather specialist");
    await expect(loading).toHaveCount(0);
  });
  test("create uses inline loading, blocks duplicate submissions, and recovers from a failed request", async ({ page }) => {
    await stub(page);
    let release!: () => void;
    let requests = 0;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/agents", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      requests++;
      await gate;
      await route.fulfill({ status: 500, json: { detail: "Create failed" } });
    });
    await page.goto("/manage");
    await page.getByRole("button", { name: "Create agent", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Create agent", exact: true });
    await dialog.getByLabel("Name", { exact: true }).fill("Test agent");
    await dialog.getByRole("button", { name: "Create", exact: true }).click();
    await expect(dialog.locator(".cds--inline-loading__text")).toHaveText("Creating…");
    await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeDisabled();
    expect(requests).toBe(1);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeVisible();
    await capture(page, "loading-create");
    release();
    await expect(dialog.getByText("Create failed", { exact: true })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Create", exact: true })).toBeEnabled();
    await expect(dialog.locator(".cds--inline-loading")).toHaveCount(0);
  });
  test("conversation selection shows a Carbon skeleton until history is restored", async ({ page }) => {
    await stub(page);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/conversation-stream-events/recent?*", async (route) => { await gate; await route.fallback(); });
    await page.goto("/chat/weatherbot");
    await expect(page.getByRole("region", { name: "Chat", exact: true }).getByRole("textbox")).toBeVisible();
    await page.getByRole("button", { name: /Plan next week's customer visits/ }).click();
    const loading = page.getByRole("status", { name: "Loading conversation history…", exact: true });
    await expect(loading).toBeVisible();
    await capture(page, "loading-conversation");
    release();
    await expect(loading).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Chat", exact: true })).toBeVisible();
  });
  test("knowledge distinguishes loading, errors, and an empty document list", async ({ page }) => {
    await stub(page);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/knowledge/documents?*", async (route) => {
      await gate;
      await route.fulfill({ status: 503, json: { detail: "Unavailable" } });
    });
    await page.goto("/chat/weatherbot");
    // Session documents load only for a conversation: a new chat has no ID yet (#774).
    await page.getByRole("button", { name: /Plan next week's customer visits/ }).click();
    await page.getByRole("tab", { name: "Knowledge", exact: true }).click();
    const loading = page.getByRole("status", { name: "Loading conversation documents…", exact: true });
    await expect(loading).toBeVisible();
    await expect(page.getByText("No session documents", { exact: true })).toHaveCount(0);
    await capture(page, "loading-knowledge");
    release();
    await expect(loading).toHaveCount(0);
    await expect(page.locator(".knowledge-section").filter({ hasText: "This Conversation" }).getByText("Failed to load documents (503)", { exact: true })).toBeVisible();
    await expect(page.getByText("No session documents", { exact: true })).toHaveCount(0);
  });
  test("memory lists use skeleton rows until the inventory resolves", async ({ page }) => {
    await stub(page);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/memory/entities?*", async (route) => {
      await gate;
      await route.fulfill({ json: { entities: [], total: 0 } });
    });
    await page.goto("/chat/weatherbot");
    await page.getByRole("link", { name: "Memory", exact: true }).click();
    const loading = page.getByRole("status", { name: "Loading memories…", exact: true });
    await expect(loading).toBeVisible();
    await expect(page.getByText("No memories match these filters.", { exact: true })).toHaveCount(0);
    await capture(page, "loading-memory");
    release();
    await expect(loading).toHaveCount(0);
    await expect(page.getByText("No memories match these filters.", { exact: true })).toBeVisible();
  });
  test("Studio keeps content visible while refreshing and marks token writes pending", async ({ page }) => {
    await stub(page);
    let release!: () => void;
    let releaseSave!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const saveGate = new Promise<void>((resolve) => { releaseSave = resolve; });
    let calls = 0;
    await page.route("**/api/events/status", (route) => route.fulfill({ json: { enabled: true, ok: true, scope: "test", backends: [], features: {}, ap_configured: false } }));
    await page.route("**/api/events/integrations", async (route) => {
      calls++;
      if (calls > 1) await gate;
      await route.fulfill({ json: { integrations: [{ name: "github", label: "GitHub", auth: "token", status: "not_connected", backend: "direct", needs_connection: true }] } }); // pragma: allowlist secret -- Mock authentication-method enum, not a token value.
    });
    await page.route("**/api/events/connect/github/token", async (route) => { await saveGate; await route.fulfill({ json: { ok: true } }); });
    await page.goto("/studio");
    await page.getByRole("link", { name: "Integrations", exact: true }).click();
    await expect(page.getByText("GitHub", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(page.getByText("Refreshing Studio content…", { exact: true })).toBeVisible();
    await expect(page.getByText("GitHub", { exact: true })).toBeVisible();
    release();
    await expect(page.getByText("Refreshing Studio content…", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Connect", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Enter value", exact: true });
    await dialog.getByLabel("Paste your GitHub token:", { exact: true }).fill("sample-token");
    await dialog.getByRole("button", { name: "Continue", exact: true }).click();
    await expect(page.getByText("Connecting…", { exact: true })).toBeVisible();
    await capture(page, "loading-studio-action");
    releaseSave();
    await expect(page.getByText("GitHub connected.", { exact: true })).toBeVisible();
    await expect(page.getByText("Connecting…", { exact: true })).toHaveCount(0);
  });

  test("authentication uses a page skeleton until the access check completes", async ({ page }) => {
    await stub(page);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/auth/config", async (route) => { await gate; await route.fallback(); });
    await page.goto("/manage");
    await expect(page.getByRole("status", { name: "Loading CUGA…", exact: true })).toBeVisible();
    await capture(page, "loading-authentication");
    release();
    await expect(page.getByRole("heading", { name: "Agent dashboard", exact: true })).toBeVisible();
    await expect(page.getByRole("status", { name: "Loading CUGA…", exact: true })).toHaveCount(0);
  });
  test("secrets modal keeps its title and uses content skeletons during retrieval", async ({ page }) => {
    await stub(page);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/secrets?*", async (route) => { await gate; await route.fulfill({ json: { secrets: [] } }); });
    await page.route("**/api/secrets/config", (route) => route.fulfill({ json: { mode: "local", force_env: false } }));
    await page.goto("/manage/weatherbot");
    await page.getByRole("button", { name: "Manage Secrets", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Secrets Manager", exact: true });
    await expect(dialog.getByRole("heading", { name: "Secrets Manager", exact: true })).toBeVisible();
    await expect(dialog.getByRole("status", { name: "Loading secrets…", exact: true })).toBeVisible();
    await expect(dialog.getByText("No secrets stored yet.", { exact: true })).toHaveCount(0);
    await capture(page, "loading-secrets");
    release();
    await expect(dialog.getByRole("status", { name: "Loading secrets…", exact: true })).toHaveCount(0);
    await expect(dialog.getByRole("tab", { name: "Environment", exact: true })).toBeVisible();
  });

  test("a failed first secret save preserves the form and permits retry", async ({ page }) => {
    await stub(page);
    await page.route("**/api/secrets*", (route) => route.request().method() === "POST"
      ? route.fulfill({ status: 400, json: { detail: "Secret save failed" } })
      : route.fulfill({ json: { secrets: [] } }));
    await page.route("**/api/secrets/config", (route) => route.fulfill({ json: { mode: "local", force_env: false } }));
    await page.goto("/manage/weatherbot");
    await page.getByRole("button", { name: "Manage Secrets", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Secrets Manager", exact: true });
    await dialog.getByRole("tab", { name: "Overrides", exact: true }).click();
    await dialog.getByLabel("Name (id)", { exact: true }).fill("new-secret");
    await dialog.getByLabel("Value", { exact: true }).fill("sample-secret");
    await dialog.getByRole("button", { name: "Add", exact: true }).click();
    await expect(dialog.getByText("Secret save failed", { exact: true })).toBeVisible();
    await expect(dialog.getByLabel("Name (id)", { exact: true })).toHaveValue("new-secret");
    await expect(dialog.getByLabel("Value", { exact: true })).toHaveValue("sample-secret");
    await expect(dialog.getByRole("button", { name: "Add", exact: true })).toBeEnabled();
  });

  test("a pending citation save does not block or overwrite another conversation", async ({ page }) => {
    await stub(page);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/knowledge/session/settings*", async (route) => {
      if (route.request().method() === "PATCH") {
        await gate;
        return route.fulfill({ status: 503, json: { detail: "Unavailable" } });
      }
      await route.fulfill({ json: { overrides: { citations_enabled: route.request().headers()["x-thread-id"] !== "recent" } } });
    });
    // Two saved conversations: a new chat has no ID, so it has no citation setting yet (#774).
    await page.route("**/api/conversations?*", (route) => route.fulfill({ json: [
      { id: "earlier", title: "Review the quarterly forecast", timestamp: Date.parse("2026-10-30T08:00:00Z"), preview: "", status: "active" },
      { id: "recent", title: "Plan next week's customer visits", timestamp: Date.parse("2026-10-31T08:00:00Z"), preview: "", status: "active" },
    ] }));
    await page.goto("/chat/weatherbot");
    await page.getByRole("button", { name: /Review the quarterly forecast/ }).click();
    await page.getByRole("tab", { name: "Knowledge", exact: true }).click();
    const toggle = page.getByRole("switch", { name: "Citations for this chat", exact: true });
    await expect(toggle).toBeChecked();
    await toggle.press("Space");
    await expect(page.getByText("Saving citation settings…", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: /Plan next week's customer visits/ }).click();
    await expect(toggle).toBeEnabled();
    await expect(toggle).not.toBeChecked();
    await expect(page.getByText("Saving citation settings…", { exact: true })).toHaveCount(0);
    const response = page.waitForResponse((res) => res.url().includes("/knowledge/session/settings") && res.request().method() === "PATCH");
    release();
    await response;
    await expect(toggle).not.toBeChecked();
  });

  test("global navigation stays consistent and separates destinations from panel controls", async ({ page }) => {
    await stub(page);
    await page.goto("/chat/weatherbot");
    const navigation = page.getByRole("navigation", { name: "CUGA", exact: true });
    await expect(navigation.getByRole("link")).toHaveText(["Chat", "Agents", "Memory"]);
    await expect(navigation.getByRole("link", { name: "Chat", exact: true })).toHaveAttribute("aria-current", "true");
    await expect(page.getByRole("button", { name: "Close conversations", exact: true })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Skills", exact: true })).toHaveCount(0);
    await expect(page.getByRole("tablist", { name: "Agent details sections", exact: true }).getByRole("tab")).toHaveText(["Tools", "Files", "Knowledge"]);
    await capture(page, "navigation-chat");
    await navigation.getByRole("link", { name: "Agents", exact: true }).click();
    await expect(page).toHaveURL(/\/manage$/);
    await expect(navigation.getByRole("link")).toHaveText(["Chat", "Agents", "Memory"]);
    await expect(navigation.getByRole("link", { name: "Agents", exact: true })).toHaveAttribute("aria-current", "true");
    await capture(page, "navigation-agents");
  });
  test("Memory is a deep-linked destination and returning to Chat preserves the conversation", async ({ page }) => {
    await stub(page);
    await page.route("**/stream", (route) => route.fulfill({ contentType: "text/event-stream", body: "event: Answer\ndata: Conversation kept.\n\n" }));
    await page.goto("/chat/weatherbot");
    const composer = page.getByRole("region", { name: "Chat", exact: true }).getByRole("textbox");
    await composer.fill("Keep this conversation");
    await composer.press("Enter");
    await expect(page.getByText("Conversation kept.", { exact: true })).toBeVisible();
    const navigation = page.getByRole("navigation", { name: "CUGA", exact: true });
    await navigation.getByRole("link", { name: "Memory", exact: true }).click();
    await expect(page).toHaveURL(/\/chat\/weatherbot\?view=memory$/);
    await expect(navigation.getByRole("link", { name: "Memory", exact: true })).toHaveAttribute("aria-current", "true");
    await navigation.getByRole("link", { name: "Chat", exact: true }).click();
    await expect(page).toHaveURL(/\/chat\/weatherbot$/);
    await expect(page.getByText("Conversation kept.", { exact: true })).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(/\?view=memory$/);
    await expect(page.getByText("Your view of this agent's memory", { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText("Your view of this agent's memory", { exact: true })).toBeVisible();
  });
  test("detail tabs have native tab panels and keyboard navigation", async ({ page }) => {
    await stub(page);
    await page.goto("/chat/weatherbot");
    const tabs = page.getByRole("tablist", { name: "Agent details sections", exact: true });
    const tools = tabs.getByRole("tab", { name: "Tools", exact: true });
    await tools.click();
    await expect(page.getByRole("tabpanel", { name: "Tools", exact: true })).toBeVisible();
    await expect(page.getByRole("tabpanel", { name: "Files", exact: true })).toBeHidden();
    await tools.press("ArrowRight");
    await expect(tabs.getByRole("tab", { name: "Files", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("tabpanel", { name: "Files", exact: true })).toBeVisible();
  });
  test("Studio uses grouped navigation with deep links, back navigation, and a mobile selector", async ({ page }) => {
    await stub(page);
    await page.route("**/api/events/status", (route) => route.fulfill({ json: { enabled: true, ok: true, scope: "test", backends: [], features: {}, ap_configured: false } }));
    await page.goto("/studio?section=integrations");
    const navigation = page.getByRole("navigation", { name: "Studio sections", exact: true });
    await expect(navigation.getByRole("link")).toHaveCount(12);
    await expect(page.getByRole("tablist", { name: "Studio sections", exact: true })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Integrations", exact: true })).toBeVisible();
    await navigation.getByRole("link", { name: "Runs", exact: true }).click();
    await expect(page).toHaveURL(/\/studio\?section=runs$/);
    await expect(page.getByRole("heading", { name: "Runs", exact: true })).toBeVisible();
    await page.goBack();
    await expect(page.getByRole("heading", { name: "Integrations", exact: true })).toBeVisible();
    await capture(page, "navigation-studio");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(navigation).toBeHidden();
    await page.getByLabel("Studio section", { exact: true }).selectOption("runs");
    await expect(page.getByRole("heading", { name: "Runs", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await capture(page, "navigation-studio-mobile");
  });
  test("trying a Studio example opens Concierge and keeps its draft when switching sections", async ({ page }) => {
    await stub(page);
    await page.route("**/api/events/status", (route) => route.fulfill({ json: { enabled: true, ok: true, scope: "test", backends: [], features: {}, ap_configured: false } }));
    await page.route("**/api/events/examples", (route) => route.fulfill({ json: { examples: [{ id: "weather", agent: "Weather specialist", utterance: "Watch for rain tomorrow", outcome: "flow-poll" }] } }));
    await page.goto("/studio?section=examples");
    await page.getByRole("button", { name: "Try it", exact: true }).click();
    await expect(page).toHaveURL(/\/studio\?section=concierge$/);
    const input = page.getByPlaceholder("Ask the concierge…  (or /automate <what> to arm a flow)");
    await expect(input).toHaveValue("Watch for rain tomorrow");
    await page.getByRole("navigation", { name: "Studio sections", exact: true }).getByRole("link", { name: "Dashboard", exact: true }).click();
    await page.goBack();
    await expect(input).toHaveValue("Watch for rain tomorrow");
  });

  test("knowledge navigation keeps distinct tasks without interactive controls inside tab labels", async ({ page }) => {
    await stub(page);
    await page.goto("/manage/weatherbot");
    await page.getByRole("button", { name: "Knowledge", exact: true }).click();
    await page.getByRole("button", { name: "Configure knowledge base", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Knowledge Base", exact: true });
    const tabs = dialog.getByRole("tablist", { name: "Knowledge sections", exact: true });
    await expect(tabs.getByRole("tab")).toHaveCount(4);
    const harness = tabs.getByRole("tab", { name: "Knowledge harness", exact: true });
    await expect(harness.getByRole("button")).toHaveCount(0);
    await harness.click();
    await expect(dialog.getByRole("region", { name: "Knowledge harness editor", exact: true })).toBeVisible();
    await capture(page, "navigation-knowledge");
  });

  test("single-agent deployments keep panel controls without an unnecessary agent toolbar", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 740 });
    await stub(page);
    await page.route("**/api/ui/config", (route) => route.fulfill({ json: { agent_registry: false, hide_cuga_logo: false, brand_name: "CUGA Agent", evolve_memory_enabled: true } }));
    await page.goto("/chat");
    await expect(page.locator(".chat-agent-toolbar")).toHaveCount(0);
    await expect(page.locator(".cuga-header-logo")).toBeVisible();
    const headerWidth = await page.evaluate(() => document.documentElement.clientWidth);
    for (const name of ["Open navigation", "Open conversations", "Open agent panel", "User profile"]) {
      const bounds = await page.getByRole("button", { name, exact: true }).boundingBox();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(headerWidth);
    }
    await page.getByRole("button", { name: "Open conversations", exact: true }).click();
    await expect(page.getByRole("complementary", { name: "Conversations", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Close panel", exact: true }).click();
    await page.getByRole("button", { name: "Open agent panel", exact: true }).click();
    await expect(page.getByRole("complementary", { name: "Agent details", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await capture(page, "navigation-single-agent-mobile");
  });

});
