import { test, expect, type Page } from "@playwright/test";

async function openChat(page: Page) {
  await page.route("**/api/**", (route) =>
    route.fulfill({ contentType: "application/json", body: "{}" }),
  );
  await page.route("**/api/auth/config", (route) =>
    route.fulfill({ contentType: "application/json", body: '{"enabled":false,"authorization_enabled":false}' }),
  );
  await page.route("**/api/commands", (route) =>
    route.fulfill({ contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/conversation-stream-events/**", (route) =>
    route.fulfill({ contentType: "application/json", body: '{"events":[]}' }),
  );
  await page.route("**/api/conversation-messages/**", (route) =>
    route.fulfill({ contentType: "application/json", body: '{"messages":[]}' }),
  );
  await page.goto("/chat?agent=geobot");
}

for (const failure of ["404", "500", "network", "SSE Error"]) {
  test(`main chat finalizes ${failure} and accepts the next turn`, async ({ page }) => {
    const detail = "Agent 'geobot' has no draft configuration";
    let requests = 0;
    await page.route("**/stream", async (route) => {
      requests += 1;
      if (requests > 1) {
        await route.fulfill({
          contentType: "text/event-stream",
          body: "event: Answer\ndata: Retry succeeded.\n\n",
        });
      } else if (failure === "network") {
        await route.abort("failed");
      } else if (failure === "SSE Error") {
        await route.fulfill({
          contentType: "text/event-stream",
          body: `event: Error\ndata: ${detail}\n\n`,
        });
      } else {
        await route.fulfill({
          status: Number(failure),
          contentType: "application/json",
          body: JSON.stringify({ detail }),
        });
      }
    });
    await openChat(page);
    const composer = page.getByRole("textbox").first();
    await composer.fill("Where am I?");
    await composer.press("Enter");

    await expect(page.getByText("Error:", { exact: false }).first()).toBeVisible();
    if (failure !== "network") {
      await expect(page.getByText(detail, { exact: false }).first()).toBeVisible();
    }
    if (failure === "404" || failure === "500") {
      await expect(page.getByText(`HTTP error! status: ${failure}`, { exact: false }).first()).toBeVisible();
    }
    // A visible error alone is insufficient: Carbon must finish the active response so
    // cancellation is no longer offered and another turn can receive its own answer.
    await expect(page.getByRole("button", { name: /stop/i })).toHaveCount(0);
    await composer.fill("Try again");
    await composer.press("Enter");
    await expect(page.getByText("Retry succeeded.", { exact: true })).toBeVisible();
    expect(requests).toBe(2);
  });
}
