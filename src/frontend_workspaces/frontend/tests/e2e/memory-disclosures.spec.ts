import { test, expect } from "@playwright/test";

test("conversation history keeps used and saved memory disclosures distinct", async ({ page }) => {
  const threadId = "67a5846f-3e2f-5f69-a79a-7232c58cd77a";
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/auth/config") body = { enabled: false, authorization_enabled: false };
    else if (path === "/api/ui/config") body = { evolve_memory_enabled: true, agent_registry: false };
    else if (path === "/api/commands") body = [];
    else if (path === "/api/conversation-threads") body = { threads: [{ thread_id: threadId, latest_version: 1, first_message: "Remember the handoff preference", updated_at: "2026-09-30T12:00:00" }] };
    else if (path.startsWith("/api/conversation-stream-events/")) body = { events: [
      { event_name: "UserMessage", event_data: "Remember the handoff preference", sequence: 0, timestamp: "2026-09-30T12:00:00" },
      { event_name: "Answer", event_data: JSON.stringify({ data: "I will start with customer impact.", memory_usage: { count: 2, entity_ids: ["used-a", "used-b"] }, memory_saved: { count: 1, entity_ids: ["saved-c"] } }), sequence: 1, timestamp: "2026-09-30T12:00:01" },
    ] };
    else if (path.startsWith("/api/conversation-messages/")) body = { messages: [] };
    else if (path.endsWith("/settings")) body = { instance_enabled: true, user_enabled: true, effective_enabled: true, episodic_enabled: true };
    else if (path.endsWith("/entities")) body = { items: [], total: 0 };
    await route.fulfill({ json: body });
  });
  await page.goto("/chat");
  await page.getByText("Remember the handoff preference", { exact: true }).first().click();
  await expect(page.getByRole("button", { name: "2 memories used", exact: true })).toBeVisible();
  const saved = page.getByRole("button", { name: "1 memory saved", exact: true });
  await expect(saved).toBeVisible();
  const chat = page.locator(".carbon-chat-contained");
  await expect(chat).toBeVisible();
  await chat.evaluate((element) => { (window as any).__originalChat = element; });
  await saved.click();
  await expect(page.getByText("Showing 1 saved in the response. Show all")).toBeVisible();
  await expect(chat).toBeAttached();
  await expect(chat).toBeHidden();
  expect(await chat.evaluate((element) => element === (window as any).__originalChat)).toBe(true);
  await page.getByRole("button", { name: /Back to chat/i }).click();
  await expect(chat).toBeVisible();
  expect(await chat.evaluate((element) => element === (window as any).__originalChat)).toBe(true);
  await expect(saved).toBeVisible();
});
