import { test, expect } from "@playwright/test";

const agents = [
  { id: "cuga", name: "CUGA supervisor", kind: "supervisor" },
  { id: "other", name: "Other supervisor", kind: "supervisor" },
  { id: "weatherbot", name: "Weather specialist", kind: "single", description: "Looks up current weather" },
  { id: "pricebot", name: "Price specialist", kind: "single" },
];

async function stub(page, members = [{ kind: "internal", ref: "weatherbot" }, { kind: "a2a", name: "Remote analyst", endpoint: "https://example.invalid" }]) {
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    const id = url.searchParams.get("agent_id") || "cuga-default";
    let body: object = {};
    if (url.pathname === "/api/auth/config") body = { enabled: false, authorization_enabled: false };
    else if (url.pathname === "/api/ui/config") body = { agent_registry: true };
    else if (url.pathname === "/api/agents") body = { agents };
    else if (url.pathname === "/api/manage/config") body = { version: 3, config: { agent: agents.find(a => a.id === id), supervisor: { subAgents: id === "other" ? [{ kind: "internal", ref: "pricebot" }] : members } } };
    else if (url.pathname === "/api/tools/list") body = { tools: [{ name: "global_tool", app: "cuga_web", description: "Global registry tool" }] };
    else if (url.pathname === "/api/conversation-threads") body = { threads: [] };
    else if (url.pathname === "/api/skills") body = { skills: [] };
    else if (url.pathname === "/api/commands") body = [];
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
}

test("supervisor panel shows published members instead of the global tool registry", async ({ page }) => {
  await stub(page);
  await page.goto("/chat/cuga");
  await page.getByRole("button", { name: "Configuration section", exact: true }).click();
  const roster = page.getByRole("region", { name: "Supervised agents", exact: true });
  await expect(page.getByRole("heading", { name: "Supervised agents", exact: true })).toBeVisible();
  await expect(roster.getByText("Weather specialist", { exact: true })).toBeVisible();
  await expect(roster.getByText("weatherbot", { exact: true })).toBeVisible();
  await expect(roster.getByText("Remote analyst", { exact: true })).toBeVisible();
  await expect(roster.getByText("External A2A", { exact: true })).toBeVisible();
  await expect(roster.getByText("Price specialist", { exact: true })).toHaveCount(0);
  await expect(page.getByText("cuga_web", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Published agents available for delegation", { exact: true })).toBeVisible();
  await roster.getByRole("link", { name: "Chat with Weather specialist", exact: true }).click();
  await expect(page).toHaveURL(/\/chat\/weatherbot$/);
  await page.getByRole("button", { name: "Configuration section", exact: true }).click();
  await expect(page.getByText("cuga_web", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Supervised agents", exact: true })).toHaveCount(0);
});

test("switching supervisors replaces the roster", async ({ page }) => {
  await stub(page);
  await page.goto("/chat/cuga");
  await page.getByRole("button", { name: "Configuration section", exact: true }).click();
  await expect(page.getByRole("link", { name: "Chat with Weather specialist", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Agent", exact: true }).click();
  await page.getByRole("option", { name: "Other supervisor Supervisor", exact: true }).click();
  await page.getByRole("button", { name: "Configuration section", exact: true }).click();
  await expect(page.getByRole("link", { name: "Chat with Price specialist", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Chat with Weather specialist", exact: true })).toHaveCount(0);
});

test("empty supervisor roster does not imply access to all agents", async ({ page }) => {
  await stub(page, []);
  await page.goto("/chat/cuga");
  await page.getByRole("button", { name: "Configuration section", exact: true }).click();
  await expect(page.getByText("No agents are assigned to this supervisor.", { exact: true })).toBeVisible();
  await expect(page.getByText("cuga_web", { exact: true })).toHaveCount(0);
});

test("deleted member stays visible as unavailable without a chat link", async ({ page }) => {
  await stub(page, [{ kind: "internal", ref: "deleted-agent" }]);
  await page.goto("/chat/cuga");
  await page.getByRole("button", { name: "Configuration section", exact: true }).click();
  await expect(page.getByText("Agent unavailable. Update the supervisor configuration.", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Chat with deleted-agent/ })).toHaveCount(0);
});
