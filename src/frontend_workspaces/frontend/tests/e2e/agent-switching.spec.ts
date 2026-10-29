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
    else if (url.pathname === "/api/agent/context") body = { agent_id: "cuga-default", config_version: 99, knowledge_enabled: false, session_level_knowledge_enabled: false };
    else if (url.pathname === "/api/manage/config") body = { version: id === "cuga" ? 3 : 2, config: { agent: agents.find((agent) => agent.id === id) } };
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

test("configuration versions belong to the selected agent rather than the global context", async ({ page }) => {
  await stubAgents(page);
  await page.goto("/chat/weatherbot");
  await expect(page.getByText("Config v2", { exact: true })).toBeVisible();
  await selectAgent(page, "Supervisor Supervisor");
  await expect(page.getByText("Config v3", { exact: true })).toBeVisible();
  await selectAgent(page, "Price specialist");
  await expect(page.getByText("Config v2", { exact: true })).toBeVisible();
  await expect(page.getByText("Config v99", { exact: true })).toHaveCount(0);
});

test("new conversations skip knowledge cleanup when knowledge is disabled", async ({ page }) => {
  await stubAgents(page);
  const cleanupRequests: string[] = [];
  await page.route("**/api/knowledge/session*", async (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    cleanupRequests.push(route.request().url());
    await route.fulfill({ status: 503, contentType: "application/json", body: '{}' });
  });
  await page.goto("/chat/weatherbot");
  await expect(page.getByText("Config v2", { exact: true })).toBeVisible();
  const priorThread = await page.locator("code").first().textContent();
  await page.getByRole("button", { name: "New conversation", exact: true }).click();
  await expect(page.locator("code").first()).not.toHaveText(priorThread!);
  await expect(page.getByText("Draft cleanup incomplete", { exact: true })).toHaveCount(0);
  expect(cleanupRequests).toEqual([]);
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

test("a newly answered YKT weather conversation reopens from the recent list", async ({ page }) => {
  await stubAgents(page);
  let savedThread = "";
  const savedEvents = new Map<string, object[]>();
  await page.route("**/api/conversation-threads?*", (route) => route.fulfill({
    contentType: "application/json", body: JSON.stringify({ threads: [
      ...(savedThread ? [{ thread_id: savedThread, first_message: "YKT weather", latest_version: 1, updated_at: "2026-10-22T00:00:00Z" }] : []),
      { thread_id: "weatherbot-other", first_message: "Another conversation", latest_version: 1, updated_at: "2026-10-22T00:00:00Z" },
    ] }),
  }));
  await page.route("**/api/conversation-stream-events/**", (route) => {
    const thread = new URL(route.request().url()).pathname.split("/").pop()!;
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({ events: savedEvents.get(thread) || (thread === "weatherbot-other" ? [{
      event_name: "Answer", event_data: "Another saved answer.", timestamp: "2026-10-22T00:00:01Z", sequence: 0,
    }] : []) }) });
  });
  await page.route("**/stream", async (route) => {
    savedThread = route.request().headers()["x-thread-id"];
    savedEvents.set(savedThread, [
      { event_name: "UserMessage", event_data: "YKT weather", timestamp: "2026-10-22T00:00:02Z", sequence: 0 },
      { event_name: "Answer", event_data: "YKT weather answer.", timestamp: "2026-10-22T00:00:03Z", sequence: 1 },
    ]);
    await route.fulfill({ contentType: "text/event-stream", body: "event: Answer\ndata: YKT weather answer.\n\n" });
  });
  await page.goto("/chat/weatherbot");
  await expect(page.getByTestId("header_name").last()).toHaveText("Weather specialist");
  const composer = page.getByRole("textbox").first();
  await composer.fill("YKT weather");
  await composer.press("Enter");
  await expect(page.getByText("YKT weather answer.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /YKT weather/ })).toBeVisible();
  for (let visit = 0; visit < 2; visit += 1) {
    await page.getByRole("button", { name: /Another conversation/ }).click();
    await expect(page.getByText("Another saved answer.", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: /YKT weather/ }).click();
    await expect(page.getByText("YKT weather answer.", { exact: true })).toBeVisible();
    await expect(page.getByText("YKT weather", { exact: true }).first()).toBeVisible();
  }
});

test("saved conversations can be revisited without starting a new conversation", async ({ page }) => {
  await stubAgents(page);
  await page.goto("/chat/weatherbot");
  await page.getByRole("button", { name: /weatherbot recent thread/ }).click();
  await expect(page.getByText("History answer for weatherbot-thread.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /weatherbot recent other/ }).click();
  await expect(page.getByText("History answer for weatherbot-other.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /weatherbot recent thread/ }).click();
  await expect(page.getByText("History answer for weatherbot-thread.", { exact: true })).toBeVisible();
});

test("a slow previous conversation does not block or replace the selected history", async ({ page }) => {
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await stubAgents(page, { historyDelay: delayed });
  try {
    await page.goto("/chat/weatherbot");
    const requested = page.waitForRequest("**/conversation-stream-events/weatherbot-thread?*");
    await page.getByRole("button", { name: /weatherbot recent thread/ }).click();
    await requested;
    await page.getByRole("button", { name: /weatherbot recent other/ }).click();
    await expect(page.getByText("History answer for weatherbot-other.", { exact: true })).toBeVisible();
    const response = page.waitForResponse("**/conversation-stream-events/weatherbot-thread?*");
    release();
    await (await response).finished();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page.getByText("History answer for weatherbot-thread.", { exact: true })).toHaveCount(0);
    await expect(page.getByText("History answer for weatherbot-other.", { exact: true })).toBeVisible();
  } finally { release(); }
});

test("rapid conversation selection during hydration keeps the last selected transcript", async ({ page }) => {
  await stubAgents(page);
  let releaseDraft!: () => void;
  const draftDelay = new Promise<void>((resolve) => { releaseDraft = resolve; });
  await page.route("**/api/conversation-stream-events/**", async (route) => {
    const thread = new URL(route.request().url()).pathname.split("/").pop()!;
    const saved = ["weatherbot-thread", "weatherbot-other"].includes(thread);
    if (!saved) await draftDelay;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ events: saved ? [{
      event_name: "Answer", event_data: `Saved answer for ${thread}.`, timestamp: "2026-10-22T00:00:00Z", sequence: 0,
    }] : [] }) });
  });
  try {
    const initialHistory = page.waitForRequest((request) => new URL(request.url()).pathname.startsWith("/api/conversation-stream-events/")
      && !new URL(request.url()).pathname.endsWith("/weatherbot-thread"));
    await page.goto("/chat/weatherbot");
    await initialHistory;
    await page.getByRole("button", { name: /weatherbot recent thread/ }).click();
    await page.getByRole("button", { name: /weatherbot recent other/ }).click();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    releaseDraft();
    await expect(page.getByText("Saved answer for weatherbot-other.", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Hello, how can I help you today?" })).toHaveCount(0);
    await expect(page.getByText("Saved answer for weatherbot-thread.", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "New conversation", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Hello, how can I help you today?" })).toBeVisible();
    await expect(page.getByText("Saved answer for weatherbot-other.", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: /weatherbot recent other/ }).click();
    await expect(page.getByText("Saved answer for weatherbot-other.", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Close panel", exact: true }).last().click();
    await page.getByTestId("main_panel").getByRole("button", { name: "Restart conversation", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Hello, how can I help you today?" })).toBeVisible();
  } finally { releaseDraft(); }
});

test("restarting while saved history is pending keeps the conversation empty", async ({ page }) => {
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await stubAgents(page, { historyDelay: delayed });
  let sentThread = "";
  await page.route("**/stream", async (route) => {
    sentThread = route.request().headers()["x-thread-id"];
    await route.fulfill({ contentType: "text/event-stream", body: "event: Answer\ndata: Fresh conversation answer.\n\n" });
  });
  try {
    await page.goto("/chat/weatherbot");
    const requested = page.waitForRequest("**/conversation-stream-events/weatherbot-thread?*");
    await page.getByRole("button", { name: /weatherbot recent thread/ }).click();
    await requested;
    await page.getByRole("button", { name: "Close panel", exact: true }).last().click();
    await page.getByTestId("main_panel").getByRole("button", { name: "Restart conversation", exact: true }).click();
    const response = page.waitForResponse("**/conversation-stream-events/weatherbot-thread?*");
    release();
    await (await response).finished();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page.getByText("History answer for weatherbot-thread.", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Hello, how can I help you today?" })).toBeVisible();
    await expect(page.locator("code").first()).not.toHaveText("weatherbot-thread");
    const newDraft = await page.locator("code").first().textContent();
    const composer = page.getByTestId("home_screen_panel").getByRole("textbox");
    await composer.fill("Weather after restart");
    await composer.press("Enter");
    await expect(page.getByText("Fresh conversation answer.", { exact: true })).toBeVisible();
    expect(sentThread).toBe(newDraft);
  } finally { release(); }
});
