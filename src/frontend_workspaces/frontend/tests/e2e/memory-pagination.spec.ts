import { test, expect } from "@playwright/test";

test("personal inventory replaces pages instead of accumulating 1000 memories", async ({ page }) => {
  const requests: Array<{ offset: number; limit: number }> = [];
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let body: unknown = {};
    if (path === "/api/auth/config") body = { enabled: false, authorization_enabled: false };
    else if (path === "/api/ui/config") body = { evolve_memory_enabled: true, agent_registry: false };
    else if (path === "/api/commands") body = [];
    else if (path === "/api/conversation-threads") body = { threads: [] };
    else if (path.endsWith("/settings")) body = { instance_enabled: true, user_enabled: true, effective_enabled: true, episodic_enabled: true };
    else if (path === "/api/memory/entities") {
      const offset = Number(url.searchParams.get("cursor") ?? 0);
      const limit = Number(url.searchParams.get("limit"));
      requests.push({ offset, limit });
      body = { total: 1000, next_cursor: offset + limit < 1000 ? String(offset + limit) : null,
        items: Array.from({ length: Math.min(limit, 1000 - offset) }, (_, index) => ({
          id: String(offset + index + 1), type: "fact", content: "A saved preference",
          metadata: { title: `Memory number ${offset + index + 1}` },
        })),
      };
    } else if (path.endsWith("/entities") || path.includes("/retention/")) body = { items: [], total: 0 };
    await route.fulfill({ json: body });
  });
  await page.goto("/chat");
  await page.getByText("Memory", { exact: true }).first().click();
  const list = page.locator(".memory-workspace__record-list .memory-workspace__list");
  await expect(list.locator("li")).toHaveCount(20);
  await expect(page.getByText("20 on this page · 1000 total")).toBeVisible();
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(list).toContainText("Memory number 21");
  await expect(list.locator("li")).toHaveCount(20);
  await expect(list.getByText("Memory number 1", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Previous page", exact: true }).click();
  await expect(list.getByText("Memory number 1", { exact: true })).toBeVisible();
  await page.setViewportSize({width: 1920, height: 1080});
  await page.getByRole("combobox", { name: "Memories per page:", exact: true }).selectOption("50");
  await expect(list.locator("li")).toHaveCount(50);
  expect(requests.slice(0, 4)).toEqual([
    { offset: 0, limit: 20 }, { offset: 20, limit: 20 }, { offset: 0, limit: 20 }, { offset: 0, limit: 50 },
  ]);
});
