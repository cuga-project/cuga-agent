import { test, expect } from "@playwright/test";

for (const count of [0, 3, 12]) {
  test(`pending memory table handles ${count} records independently of inventory`, async ({ page }) => {
    const candidates = Array.from({ length: count }, (_, index) => ({ entity_id: String(index + 1), policy_id: "standard", status: "review", rule: "review", reason: "Review required", updated_at: "2026-10-01T12:00:00Z" }));
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = {};
      if (path === "/api/auth/config") body = { enabled: false, authorization_enabled: false };
      else if (path === "/api/ui/config") body = { evolve_memory_enabled: true, agent_registry: false };
      else if (path === "/api/commands") body = [];
      else if (path === "/api/conversation-threads") body = { threads: [] };
      else if (path.endsWith("/settings")) body = { instance_enabled: true, user_enabled: true, effective_enabled: true, episodic_enabled: true };
      else if (path.endsWith("/retention/candidates")) body = { items: candidates };
      else if (/\/manage\/memory\/entities\/\d+$/.test(path)) {
        const id = path.split("/").pop();
        body = { id, type: "fact", content: `Saved content ${id}`, metadata: { title: `Pending memory ${id}`, user_id: "alice", agent_id: "cuga-default" } };
      } else if (path.endsWith("/entities") || path.includes("/retention/")) body = { items: [], total: path.endsWith("/entities") ? count : 0 };
      await route.fulfill({ json: body });
    });
    await page.goto("/chat");
    await page.getByText("Memory", { exact: true }).first().click();
    await page.getByRole("button", { name: "Administration", exact: true }).click();
    await page.getByRole("tab", { name: "Activity", exact: true }).click();
    const table = page.getByRole("table", { name: "Memories awaiting action" });
    if (!count) {
      await expect(table.getByText("No memories are awaiting action.")).toBeVisible();
      return;
    }
    await expect(table.getByRole("button", { name: "Pending memory 1", exact: true })).toBeVisible();
    await expect(table.locator("tbody tr")).toHaveCount(Math.min(5, count));
    if (count > 5) {
      await page.getByRole("button", { name: "Next page", exact: true }).click();
      await expect(table.getByRole("button", { name: "Pending memory 6", exact: true })).toBeVisible();
    }
    await page.getByRole("searchbox", { name: "Search pending memories", exact: true }).fill(`Pending memory ${count}`);
    await expect(table.locator("tbody tr")).toHaveCount(1);
    await table.getByRole("button", { name: `Pending memory ${count}`, exact: true }).click();
    await expect(page.getByRole("tab", { name: "Memory", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("heading", { name: `Pending memory ${count}`, exact: true })).toBeVisible();
  });
}
