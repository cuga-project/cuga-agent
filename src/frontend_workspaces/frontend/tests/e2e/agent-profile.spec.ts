import { test, expect, type Page } from "@playwright/test";

const agents = [
  { id: "cuga-default", name: "Digital Sales Agent", kind: "single" },
  { id: "weatherbot", name: "Weather specialist", kind: "single" },
  { id: "pricebot", name: "Price specialist", kind: "single" },
];

async function stubBackend(page: Page, delayWeather?: Promise<void>) {
  const streamAgents: string[] = [];
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    let body: object = {};
    if (url.pathname === "/api/auth/config") body = { enabled: false, authorization_enabled: false };
    else if (url.pathname === "/api/ui/config") body = { agent_registry: true, hide_cuga_logo: true };
    else if (url.pathname === "/api/agents") body = { agents };
    else if (url.pathname === "/api/commands") body = [];
    else if (url.pathname === "/api/agent/context") body = { agent_id: "cuga-default" };
    else if (url.pathname === "/api/manage/config") {
      const id = url.searchParams.get("agent_id") || "cuga-default";
      if (id === "weatherbot" && delayWeather) await delayWeather;
      body = { config: { agent: agents.find((agent) => agent.id === id) } };
    } else if (url.pathname.includes("conversation-stream-events")) body = { events: [] };
    else if (url.pathname.includes("conversation-messages")) body = { messages: [] };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.route("**/stream", async (route) => {
    const agentId = route.request().headers()["x-agent-id"];
    streamAgents.push(agentId);
    await route.fulfill({ contentType: "text/event-stream", body: `event: Answer\ndata: Reply from ${agentId}.\n\n` });
  });
  return streamAgents;
}

function switcher(page: Page) {
  return page.locator(".chat-agent-switcher button[role='combobox']");
}

function assistantTitle(page: Page) {
  return page.getByTestId("header_name").last();
}

test("one navigation click from a dirty field survives draft-save rerenders", async ({ page }) => {
  await stubBackend(page);
  await page.goto("/manage/weatherbot");
  await expect(page.getByPlaceholder("Enter agent name")).toHaveValue("Weather specialist");
  const saved = page.waitForRequest((request) => request.method() === "PATCH"
    && new URL(request.url()).pathname === "/api/manage/config/draft/agent");
  await page.locator("#agent-description").fill("Weather draft saved on navigation");
  await page.getByRole("link", { name: "Agents", exact: true }).click();
  expect(new URL((await saved).url()).searchParams.get("agent_id")).toBe("weatherbot");
  await expect(page).toHaveURL(/\/manage$/);
  await expect(page.getByRole("heading", { name: "Agent dashboard", exact: true })).toBeVisible();
});

test("weatherbot displays its own name and sends its own execution identity", async ({ page }) => {
  const streamAgents = await stubBackend(page);
  await page.goto("/chat/weatherbot");
  await expect(assistantTitle(page)).toHaveText("Weather specialist");
  const composer = page.getByRole("textbox").first();
  await composer.fill("What is the weather?");
  await composer.press("Enter");
  await expect(page.getByText("Reply from weatherbot.", { exact: true })).toBeVisible();
  await expect(assistantTitle(page)).toHaveText("Weather specialist");
  expect(streamAgents).toEqual(["weatherbot"]);
});

test("switching agents replaces the default name without reusing a cached profile", async ({ page }) => {
  await stubBackend(page);
  await page.goto("/chat");
  await expect(assistantTitle(page)).toHaveText("Digital Sales Agent");
  for (const agent of [agents[1], agents[2], agents[0]]) {
    await switcher(page).click();
    await page.getByRole("option", { name: agent.name, exact: true }).click();
    await expect(assistantTitle(page)).toHaveText(agent.name);
  }
});

test("a late profile response cannot rename the newly selected agent", async ({ page }) => {
  let releaseWeather!: () => void;
  const delayWeather = new Promise<void>((resolve) => { releaseWeather = resolve; });
  await stubBackend(page, delayWeather);
  await page.goto("/chat/weatherbot");
  await expect(switcher(page)).toContainText("Weather specialist");
  await switcher(page).click();
  await page.getByRole("option", { name: "Price specialist", exact: true }).click();
  await expect(assistantTitle(page)).toHaveText("Price specialist");
  releaseWeather();
  const composer = page.getByRole("textbox").first();
  await composer.fill("Check price");
  await composer.press("Enter");
  await expect(page.getByText("Reply from pricebot.", { exact: true })).toBeVisible();
  await expect(assistantTitle(page)).toHaveText("Price specialist");
});

test("a pending profile lookup does not block sending or cancellation", async ({ page }) => {
  let releaseProfile!: () => void;
  const delayProfile = new Promise<void>((resolve) => { releaseProfile = resolve; });
  await stubBackend(page, delayProfile);
  await page.route("**/stop", (route) => route.fulfill({ contentType: "application/json", body: "{}" }));
  await page.addInitScript(() => {
    const originalFetch = window.fetch;
    window.fetch = (input, options) => {
      const url = new URL(input instanceof Request ? input.url : String(input), location.href);
      if (url.pathname !== "/stream") return originalFetch(input, options);
      (window as any).streamAgentHeader = new Headers(options?.headers).get("X-Agent-ID");
      return Promise.resolve(new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode("event: CodeAgent\ndata: Waiting for weather lookup\n\n"));
          options?.signal?.addEventListener("abort", () => {
            controller.error(new DOMException("Aborted", "AbortError"));
          });
        },
      }), { headers: { "Content-Type": "text/event-stream" } }));
    };
  });
  try {
    await page.goto("/chat/weatherbot");
    await page.getByRole("button", { name: "Close panel", exact: true }).last().click();
    const composer = page.getByRole("textbox").first();
    await composer.fill("Check the weather");
    await composer.press("Enter");
    await page.waitForFunction(() => (window as any).streamAgentHeader === "weatherbot");
    await page.getByRole("button", { name: /stop/i }).first().click();
    await expect(page.getByText("Request was cancelled.", { exact: true })).toBeVisible();
  } finally {
    releaseProfile();
  }
});

test("approval responses use the selected agent after switching", async ({ page }) => {
  await stubBackend(page);
  const approvalAgents: string[] = [];
  let savedThread = "";
  await page.route("**/api/conversation-threads?*", (route) => route.fulfill({
    contentType: "application/json", body: JSON.stringify({ threads: savedThread ? [{
      thread_id: savedThread, first_message: "Check weather with approval", latest_version: 1,
      updated_at: new Date().toISOString(),
    }] : [] }),
  }));
  await page.route("**/stream", async (route) => {
    const body = route.request().postDataJSON();
    if (body.action_id) {
      approvalAgents.push(route.request().headers()["x-agent-id"]);
      savedThread = route.request().headers()["x-thread-id"];
      await route.fulfill({ contentType: "text/event-stream", body: "event: Answer\ndata: Approved weather action.\n\n" });
    } else {
      await route.fulfill({
        contentType: "text/event-stream",
        body: `event: SuggestHumanActions\ndata: ${JSON.stringify({
          action_id: "weather-approval",
          action_name: "Check weather",
          type: "confirmation",
          button_text: "Approve weather lookup",
        })}\n\n`,
      });
    }
  });
  await page.goto("/chat");
  await expect(assistantTitle(page)).toHaveText("Digital Sales Agent");
  await switcher(page).click();
  await page.getByRole("option", { name: "Weather specialist", exact: true }).click();
  await expect(assistantTitle(page)).toHaveText("Weather specialist");
  const composer = page.getByRole("textbox").first();
  await composer.fill("Check weather with approval");
  const pendingThreads = page.waitForResponse("**/api/conversation-threads?*");
  await composer.press("Enter");
  await pendingThreads;
  await page.getByRole("button", { name: "Approve weather lookup", exact: true }).click();
  await expect(page.getByText("Approved weather action.", { exact: true })).toBeVisible();
  expect(approvalAgents).toEqual(["weatherbot"]);
  await expect(page.getByRole("button", { name: /Check weather with approval.*active/ })).toBeVisible();
});
