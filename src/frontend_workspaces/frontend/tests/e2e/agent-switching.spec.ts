import { test, expect, type Page } from "@playwright/test";

const agents = [
  { id: "weatherbot", name: "Weather specialist", kind: "single" },
  { id: "pricebot", name: "Price specialist", kind: "single" },
  { id: "cuga", name: "Supervisor", kind: "supervisor" },
];

async function stubAgents(page: Page, options: {
  registry?: boolean;
  historyDelay?: Promise<void>;
  workspaceDelay?: Promise<void>;
  failToolsFor?: string;
} = {}) {
  const historyRequests: Array<{ thread: string; agent: string | null }> = [];
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const id = url.searchParams.get("agent_id") || "cuga-default";
    if (url.pathname === "/api/tools/list" && id === options.failToolsFor) {
      await route.abort("failed");
      return;
    }
    let body: object = {};
    if (url.pathname === "/api/auth/config") body = { enabled: false, authorization_enabled: false };
    else if (url.pathname === "/api/ui/config") body = { agent_registry: options.registry ?? true, hide_cuga_logo: true };
    else if (url.pathname === "/api/agents") body = { agents };
    else if (url.pathname === "/api/commands") body = [];
    else if (url.pathname === "/api/skills") body = { skills: [] };
    else if (url.pathname === "/api/agent/context") body = { agent_id: "cuga-default", config_version: 1 };
    else if (url.pathname === "/api/manage/config") body = { config: { agent: agents.find((agent) => agent.id === id) } };
    else if (url.pathname === "/api/tools/list") body = {
      tools: [{ name: `${id}_tool`, app: `${id}_app`, description: `Tools for ${id}` }],
    };
    else if (url.pathname === "/api/conversation-threads") body = { threads: ["thread", "other"].map((suffix) => ({
      thread_id: `${id}-${suffix}`,
      latest_version: 1,
      first_message: `${id} recent ${suffix}`,
      updated_at: "2026-10-22T00:00:00Z",
    })) };
    else if (url.pathname.startsWith("/api/conversation-stream-events/")) {
      const thread = url.pathname.split("/").pop()!;
      historyRequests.push({ thread, agent: url.searchParams.get("agent_id") });
      if (thread === "weatherbot-thread" && options.historyDelay) await options.historyDelay;
      body = { events: thread.endsWith("-thread") || thread.endsWith("-other") ? [{
        event_name: "Answer", event_data: `History answer for ${thread}.`,
        timestamp: "2026-10-22T00:00:00Z", sequence: 1,
      }] : [] };
    } else if (url.pathname.includes("conversation-messages")) body = { messages: [] };
    else if (url.pathname === "/api/workspace/tree") {
      const thread = url.searchParams.get("thread_id") || "";
      if (thread === "weatherbot-thread" && options.workspaceDelay) await options.workspaceDelay;
      body = { tree: thread.endsWith("-thread") || thread.endsWith("-other")
        ? [{ name: `${thread}.json`, path: `${thread}.json`, type: "file" }] : [] };
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  return historyRequests;
}

async function selectAgent(page: Page, name: string) {
  await page.locator(".chat-agent-switcher button[role='combobox']").click();
  await page.getByRole("option", { name, exact: true }).click();
}

test("recent chats and history switch with the selected agent", async ({ page }) => {
  const requests = await stubAgents(page);
  await page.goto("/chat/weatherbot");
  await page.getByRole("button", { name: /weatherbot recent thread/ }).click();
  await expect(page.getByText("History answer for weatherbot-thread.", { exact: true })).toBeVisible();
  await selectAgent(page, "Price specialist");
  await expect(page.getByRole("button", { name: /weatherbot recent/ })).toHaveCount(0);
  await expect(page.getByText("History answer for weatherbot-thread.", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /pricebot recent thread/ }).click();
  await expect(page.getByText("History answer for pricebot-thread.", { exact: true })).toBeVisible();
  await selectAgent(page, "Weather specialist");
  await expect(page.getByRole("button", { name: /weatherbot recent thread/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /pricebot recent/ })).toHaveCount(0);
  expect(requests).toContainEqual({ thread: "weatherbot-thread", agent: "weatherbot" });
  expect(requests).toContainEqual({ thread: "pricebot-thread", agent: "pricebot" });
});

test("configuration tools switch with the selected agent", async ({ page }) => {
  await stubAgents(page);
  await page.goto("/chat/weatherbot");
  await page.getByRole("button", { name: "Configuration section", exact: true }).click();
  await expect(page.getByText("weatherbot_app", { exact: true })).toBeVisible();
  await selectAgent(page, "Price specialist");
  await page.getByRole("button", { name: "Configuration section", exact: true }).click();
  await expect(page.getByText("pricebot_app", { exact: true })).toBeVisible();
  await expect(page.getByText("weatherbot_app", { exact: true })).toHaveCount(0);
});

test("late history from the previous agent does not appear after switching", async ({ page }) => {
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await stubAgents(page, { historyDelay: delayed });
  try {
    await page.goto("/chat/weatherbot");
    const requested = page.waitForRequest("**/conversation-stream-events/weatherbot-thread?*");
    await page.getByRole("button", { name: /weatherbot recent thread/ }).click();
    await requested;
    await selectAgent(page, "Price specialist");
    await page.getByRole("button", { name: /pricebot recent thread/ }).click();
    await expect(page.getByText("History answer for pricebot-thread.", { exact: true })).toBeVisible();
    const response = page.waitForResponse("**/conversation-stream-events/weatherbot-thread?*");
    release();
    await (await response).finished();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page.getByText("History answer for weatherbot-thread.", { exact: true })).toHaveCount(0);
  } finally { release(); }
});

test("a failed tool lookup after switching does not show another agent's configuration", async ({ page }) => {
  await stubAgents(page, { failToolsFor: "pricebot" });
  await page.goto("/chat/weatherbot");
  await page.getByRole("button", { name: "Configuration section", exact: true }).click();
  await expect(page.getByText("weatherbot_app", { exact: true })).toBeVisible();
  await selectAgent(page, "Price specialist");
  await page.getByRole("button", { name: "Configuration section", exact: true }).click();
  await expect(page.getByText("Configuration Load Error", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Configuration section", exact: true })).toHaveText("0");
  await expect(page.getByText("weatherbot_app", { exact: true })).toHaveCount(0);
  await expect(page.getByText("CUGA Default Agent", { exact: true })).toHaveCount(0);
});

test("late workspace responses do not replace the selected conversation files", async ({ page }) => {
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await stubAgents(page, { workspaceDelay: delayed });
  try {
    await page.goto("/chat/weatherbot");
    const requested = page.waitForRequest((request) => new URL(request.url()).searchParams.get("thread_id") === "weatherbot-thread"
      && new URL(request.url()).pathname === "/api/workspace/tree");
    await page.getByRole("button", { name: /weatherbot recent thread/ }).click();
    await requested;
    await page.getByRole("button", { name: /weatherbot recent other/ }).click();
    await expect(page.getByText("weatherbot-other.json", { exact: true })).toBeVisible();
    const response = page.waitForResponse((response) => new URL(response.url()).searchParams.get("thread_id") === "weatherbot-thread"
      && new URL(response.url()).pathname === "/api/workspace/tree");
    release();
    await (await response).finished();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page.getByText("weatherbot-thread.json", { exact: true })).toHaveCount(0);
    await expect(page.getByText("weatherbot-other.json", { exact: true })).toBeVisible();
  } finally { release(); }
});

for (const registry of [true, false]) {
  test(`chat begins below its header bars (registry ${registry})`, async ({ page }) => {
    await stubAgents(page, { registry });
    await page.goto(registry ? "/chat/weatherbot" : "/chat");
    if (registry) await expect(page.locator(".chat-agent-switcher")).toBeVisible();
    const chat = await page.locator(".chat-content-area").boundingBox();
    expect(chat!.y).toBe(registry ? 88 : 48);
    expect(chat!.y + chat!.height).toBeLessThanOrEqual(page.viewportSize()!.height);
  });
}
