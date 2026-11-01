import { mkdir } from "node:fs/promises";
import { test, expect, type Page, type Locator } from "@playwright/test";
import { stub } from "./fixtures/carbon-backend";

const gatewayUrl = "https://gateway.example.com/?instance=7f3c9a10-5d2e-4b8a-9c61-0e4f2a7b8d93";
const servers = [
  { name: "Weather", tools: [
    { name: "get_forecast", description: "Get a weather forecast", inputSchema: { type: "object" } },
    { name: "get_alerts", description: "Get current weather alerts", inputSchema: { type: "object" } },
  ] },
  { name: "CRM", tools: [{ name: "list_contacts", description: "List customer contacts", inputSchema: { type: "object" } }] },
];

async function openGateway(page: Page) {
  await stub(page);
  await page.goto("/manage/weatherbot");
  const tools = page.getByRole("button", { name: "Tools", exact: true });
  if (await tools.getAttribute("aria-expanded") === "false") await tools.click();
  await page.getByRole("button", { name: "MCP Gateway", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: /^(Add|Edit) MCP Gateway$/ });
  await dialog.getByLabel("Name", { exact: true }).fill("workspace_gateway");
  await dialog.getByLabel("Gateway instance URL", { exact: true }).fill(gatewayUrl);
  return dialog;
}

async function clickVisibleAction(button: Locator) {
  // Keep the target clear of Carbon's scroll fade before a pointer click.
  await button.evaluate((element) => element.scrollIntoView({ block: "center" }));
  await button.focus();
  await expect(button).toBeInViewport();
  await button.click();
}

async function capture(page: Page, name: string) {
  const directory = process.env.CUGA_DESIGN_SCREENSHOTS;
  if (!directory) return;
  await mkdir(directory, { recursive: true });
  await page.screenshot({ path: `${directory}/${name}-${test.info().project.name}.png`, animations: "disabled" });
}

test.describe("MCP Gateway Carbon workflows", { tag: "@e2e" }, () => {
  test("signed-in user discovery has pending feedback, explicit selection, and an editable saved tile", async ({ page }) => {
    const dialog = await openGateway(page);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const requests: Record<string, unknown>[] = [];
    await page.route("**/api/manage/gateway/discover", async (route) => {
      requests.push(route.request().postDataJSON());
      await gate;
      await route.fulfill({ json: { servers } });
    });
    await expect(dialog.getByLabel("Tool calls run as")).toHaveValue("user");
    await expect(dialog.getByLabel("Secret / token / value")).toHaveCount(0);
    await clickVisibleAction(dialog.getByRole("button", { name: "Connect", exact: true }));
    try {
      await expect(dialog.getByText("Connecting…", { exact: true })).toBeVisible();
      await expect(dialog.getByLabel("Gateway instance URL")).toBeDisabled();
      await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeDisabled();
      await page.keyboard.press("Escape");
      await expect(dialog).toBeVisible();
      await capture(page, "gateway-connecting");
    } finally { release(); }
    await expect(dialog.getByRole("button", { name: "Refresh tools", exact: true })).toBeVisible();
    await expect(dialog.getByRole("checkbox", { checked: true })).toHaveCount(0);
    await expect(dialog.getByRole("button", { name: "Add gateway", exact: true })).toBeDisabled();
    await dialog.getByRole("checkbox", { name: "Weather (2)", exact: true }).press("Space");
    await expect(dialog.getByText("Select the tools this agent may use (2 selected)")).toBeVisible();
    await capture(page, "gateway-selection");
    await dialog.getByRole("button", { name: "Add gateway", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({ url: gatewayUrl, run_as: "user", agent_id: "weatherbot" });
    expect(requests[0]).not.toHaveProperty("api_key");
    const tile = page.locator(".tools-config-tile").filter({ hasText: "workspace_gateway" });
    await expect(tile).toContainText("as user");
    await expect(tile).toContainText("2 selected");
    await expect(tile.getByRole("link", { name: "Open gateway console" })).toHaveAttribute("href", gatewayUrl);
    await tile.getByRole("button", { name: "Select tools", exact: true }).click();
    await expect(page.getByRole("dialog", { name: /^(Add|Edit) MCP Gateway$/ })).toContainText("2 tools selected. Connect to change the selection.");
    await expect(page.getByRole("dialog", { name: /^(Add|Edit) MCP Gateway$/ }).getByRole("button", { name: "Save", exact: true })).toBeEnabled();
  });

  test("refresh preserves selected tools on failure and leaves newly discovered tools unselected", async ({ page }) => {
    const dialog = await openGateway(page);
    let attempt = 0;
    await page.route("**/api/manage/gateway/discover", (route) => {
      attempt++;
      return attempt === 2
        ? route.fulfill({ status: 503, json: { detail: "Gateway is temporarily unavailable" } })
        : route.fulfill({ json: { servers: attempt > 2 ? [...servers, { name: "Reports", tools: [{ name: "daily_report", description: "Daily summary" }] }] : servers } });
    });
    await clickVisibleAction(dialog.getByRole("button", { name: "Connect", exact: true }));
    await dialog.getByRole("checkbox", { name: /^get_forecast/ }).press("Space");
    await clickVisibleAction(dialog.getByRole("button", { name: "Refresh tools", exact: true }));
    await expect(dialog).toContainText("Gateway is temporarily unavailable");
    await expect(dialog.getByRole("checkbox", { name: /^get_forecast/ })).toBeChecked();
    await capture(page, "gateway-refresh-error");
    await clickVisibleAction(dialog.getByRole("button", { name: "Refresh tools", exact: true }));
    await expect(dialog.getByRole("checkbox", { name: /^daily_report/ })).not.toBeChecked();
    await expect(dialog.getByRole("checkbox", { name: /^get_forecast/ })).toBeChecked();
    await expect(dialog.getByRole("checkbox", { name: "Weather (2)", exact: true })).toHaveJSProperty("indeterminate", true);
    await expect(dialog.getByText("Gateway is temporarily unavailable")).toHaveCount(0);
  });

  test("empty results and invalid URLs remain distinct from loading", async ({ page }) => {
    const dialog = await openGateway(page);
    await dialog.getByLabel("Gateway instance URL").fill("https://gateway.example.com");
    await expect(dialog.getByRole("button", { name: "Connect", exact: true })).toBeDisabled();
    await dialog.getByLabel("Gateway instance URL").fill(gatewayUrl);
    await page.route("**/api/manage/gateway/discover", (route) => route.fulfill({ json: { servers: [] } }));
    await clickVisibleAction(dialog.getByRole("button", { name: "Connect", exact: true }));
    await expect(dialog).toContainText("This instance has no tools yet.");
    await expect(dialog.getByRole("button", { name: "Add gateway", exact: true })).toBeDisabled();
  });

  test("changing the connection or identity invalidates the previous discovery", async ({ page }) => {
    const dialog = await openGateway(page);
    await page.route("**/api/manage/gateway/discover", (route) => route.fulfill({ json: { servers } }));
    await clickVisibleAction(dialog.getByRole("button", { name: "Connect", exact: true }));
    await dialog.getByRole("checkbox", { name: /^get_forecast/ }).press("Space");
    await dialog.getByLabel("Gateway instance URL").fill(gatewayUrl.replace("7f3c9a10", "8f3c9a10"));
    await expect(dialog.getByRole("checkbox", { name: /^get_forecast/ })).toHaveCount(0);
    await expect(dialog.getByRole("button", { name: "Add gateway", exact: true })).toBeDisabled();
    await clickVisibleAction(dialog.getByRole("button", { name: "Connect", exact: true }));
    await dialog.getByRole("checkbox", { name: /^get_forecast/ }).press("Space");
    await dialog.getByLabel("Tool calls run as").selectOption("service");
    await expect(dialog.getByRole("checkbox", { name: /^get_forecast/ })).toHaveCount(0);
    await expect(dialog.getByRole("button", { name: "Connect", exact: true })).toBeDisabled();
  });

  test("service ID secret failure preserves input and pending saves cannot be submitted twice", async ({ page }) => {
    const dialog = await openGateway(page);
    await dialog.getByLabel("Tool calls run as").selectOption("service");
    const value = dialog.getByLabel("Secret / token / value", { exact: true });
    await value.fill("sample-gateway-key");
    await expect(value).toHaveAttribute("type", "password");
    await page.route("**/api/manage/gateway/discover", (route) => route.fulfill({ json: { servers } }));
    await clickVisibleAction(dialog.getByRole("button", { name: "Connect", exact: true }));
    await dialog.getByRole("checkbox", { name: /^get_forecast/ }).press("Space");
    let saves = 0;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/secrets", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      saves++;
      await gate;
      await route.fulfill({ status: 503, json: { detail: "Unavailable" } });
    });
    await dialog.getByRole("button", { name: "Add gateway", exact: true }).click();
    try {
      await expect(dialog.getByText("Saving gateway…", { exact: true })).toBeVisible();
      await page.keyboard.press("Enter");
      await page.keyboard.press("Escape");
      await expect(dialog).toBeVisible();
    } finally { release(); }
    await expect(dialog).toContainText("Nothing was saved.");
    await expect(value).toHaveValue("sample-gateway-key");
    expect(saves).toBe(1);
    await expect(dialog.getByRole("button", { name: "Add gateway", exact: true })).toBeEnabled();
  });

  test("saved Vault secret references survive gateway editing", async ({ page }) => {
    const dialog = await openGateway(page);
    await dialog.getByLabel("Tool calls run as").selectOption("service");
    await page.route("**/api/secrets?*", (route) => route.fulfill({ json: { secrets: [{ id: "gateway-key", source: "vault" }] } }));
    await page.route("**/api/secrets/config", (route) => route.fulfill({ json: { mode: "vault" } }));
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.getByRole("button", { name: "MCP Gateway", exact: true }).click();
    const edit = page.getByRole("dialog", { name: /^(Add|Edit) MCP Gateway$/ });
    await edit.getByLabel("Name", { exact: true }).fill("vault_gateway");
    await edit.getByLabel("Gateway instance URL").fill(gatewayUrl);
    await edit.getByLabel("Tool calls run as").selectOption("service");
    await edit.getByRole("checkbox", { name: "Use saved secret", exact: true }).press("Space");
    await edit.getByLabel("Secret", { exact: true }).selectOption("vault://secret/gateway-key#value");
    const payloads: Record<string, unknown>[] = [];
    await page.route("**/api/manage/gateway/discover", (route) => {
      payloads.push(route.request().postDataJSON());
      return route.fulfill({ json: { servers } });
    });
    await clickVisibleAction(edit.getByRole("button", { name: "Connect", exact: true }));
    await edit.getByRole("checkbox", { name: /^get_forecast/ }).press("Space");
    await edit.getByRole("button", { name: "Add gateway", exact: true }).click();
    const tile = page.locator(".tools-config-tile").filter({ hasText: "vault_gateway" });
    await tile.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByRole("dialog", { name: /^(Add|Edit) MCP Gateway$/ }).getByLabel("Secret", { exact: true })).toHaveValue("vault://secret/gateway-key#value");
    expect(payloads[0]).toMatchObject({ api_key_ref: "vault://secret/gateway-key#value" });
    expect(payloads[0]).not.toHaveProperty("api_key");
  });

  test("creating a Vault credential invalidates tools discovered with the previous saved credential", async ({ page }) => {
    const dialog = await openGateway(page);
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    const saved = [{ id: "old-key", source: "vault" }];
    await page.route("**/api/secrets?*", (route) => route.fulfill({ json: { secrets: saved } }));
    await page.route("**/api/secrets/config", (route) => route.fulfill({ json: { mode: "vault" } }));
    await page.route("**/api/secrets", (route) => route.fulfill({ json: { ref: "vault://secret/new-key#value" } }));
    await page.getByRole("button", { name: "MCP Gateway", exact: true }).click();
    const form = page.getByRole("dialog", { name: "Add MCP Gateway", exact: true });
    await form.getByLabel("Name", { exact: true }).fill("new_credential_gateway");
    await form.getByLabel("Gateway instance URL").fill(gatewayUrl);
    await form.getByLabel("Tool calls run as").selectOption("service");
    await form.getByRole("checkbox", { name: "Use saved secret", exact: true }).press("Space");
    await form.getByLabel("Secret", { exact: true }).selectOption("vault://secret/old-key#value");
    await page.route("**/api/manage/gateway/discover", (route) => route.fulfill({ json: { servers } }));
    await clickVisibleAction(form.getByRole("button", { name: "Connect", exact: true }));
    await form.getByRole("checkbox", { name: /^get_forecast/ }).press("Space");
    await clickVisibleAction(form.getByRole("button", { name: "Create new secret", exact: true }));
    await form.getByLabel("Key name", { exact: true }).fill("new-key");
    await form.getByLabel("New secret value", { exact: true }).fill("sample-new-key");
    saved.push({ id: "new-key", source: "vault" });
    await form.getByRole("button", { name: "Save", exact: true }).click();
    await expect(form.getByLabel("Secret", { exact: true })).toHaveValue("vault://secret/new-key#value");
    await expect(form.getByRole("checkbox", { name: /^get_forecast/ })).toHaveCount(0);
    await expect(form.getByRole("button", { name: "Add gateway", exact: true })).toBeDisabled();
  });

  test("switching through a generic connection form cannot reuse another gateway's tool definitions", async ({ page }) => {
    const dialog = await openGateway(page);
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.getByRole("button", { name: "Add tool", exact: true }).click();
    const form = page.getByRole("dialog", { name: "Add tool", exact: true });
    await form.getByRole("button", { name: "MCP Gateway instance", exact: true }).press("Enter");
    await form.getByLabel("Name", { exact: true }).fill("generic_gateway");
    await form.getByLabel("Gateway instance URL").fill(gatewayUrl);
    await page.route("**/api/manage/gateway/discover", (route) => route.fulfill({ json: { servers } }));
    await clickVisibleAction(form.getByRole("button", { name: "Connect", exact: true }));
    await form.getByRole("checkbox", { name: /^get_forecast/ }).press("Space");
    await form.getByLabel("Connection", { exact: true }).selectOption("url-http");
    await form.getByLabel("URL", { exact: true }).fill(gatewayUrl.replace("7f3c9a10", "8f3c9a10"));
    await form.getByLabel("Connection", { exact: true }).selectOption("gateway");
    await expect(form.getByRole("checkbox", { name: /^get_forecast/ })).toHaveCount(0);
    await expect(form.getByRole("button", { name: "Add tool", exact: true })).toBeDisabled();
  });

  test("standard tool templates still save and reopen through the shared Carbon form", async ({ page }) => {
    const gateway = await openGateway(page);
    await gateway.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.getByRole("button", { name: "Add tool", exact: true }).click();
    const form = page.getByRole("dialog", { name: "Add tool", exact: true });
    await form.getByRole("button", { name: "Filesystem", exact: true }).press("Enter");
    await expect(form.getByLabel("Command", { exact: true })).toHaveValue("npx");
    await form.getByRole("button", { name: "Add tool", exact: true }).click();
    await expect(form).toHaveCount(0);
    const tile = page.locator(".tools-config-tile").filter({ hasText: "filesystem" });
    await tile.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Edit tool", exact: true }).getByLabel("Command", { exact: true })).toHaveValue("npx");
  });

  for (const width of [320, 390, 1280]) {
    test(`gateway selection and modal footer remain usable at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: width >= 1000 ? 1000 : 740 });
      const dialog = await openGateway(page);
      await page.route("**/api/manage/gateway/discover", (route) => route.fulfill({ json: { servers } }));
      await clickVisibleAction(dialog.getByRole("button", { name: "Connect", exact: true }));
      await dialog.getByRole("checkbox", { name: /^get_forecast/ }).press("Space");
      await expect(dialog.getByRole("button", { name: "Add gateway", exact: true })).toBeInViewport();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      const bounds = await dialog.boundingBox();
      expect(bounds!.width).toBeLessThanOrEqual(width);
      await capture(page, `gateway-${width}`);
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      await expect(dialog).toHaveCount(0);
      await expect(page.getByRole("button", { name: "MCP Gateway", exact: true })).toBeFocused();
    });
  }
});
