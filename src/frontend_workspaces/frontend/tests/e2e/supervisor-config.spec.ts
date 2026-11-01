import { test, expect } from "@playwright/test";

for (const model of ["", "imported-supervisor-model"]) {
  for (const instructions of ["", "Updated supervisor instructions"]) {
    test(`supervisor publish preserves model=${model || "environment"} and instructions=${instructions || "cleared"}`, async ({ page }) => {
      const config = {
        agent: { name: "Roster supervisor", kind: "supervisor" },
        supervisor: { subAgents: [{ kind: "internal", ref: "weatherbot" }], planApproval: false },
        llm: { provider: "openai", model, base_url: "https://example.invalid/v1" },
        special_instructions: "Original instructions",
        knowledge: { enabled: false },
      };
      let published: typeof config | undefined;
      await page.route("**/api/**", async (route) => {
        const url = new URL(route.request().url());
        let body: object = {};
        if (url.pathname === "/api/auth/config") body = { enabled: false, authorization_enabled: false };
        else if (url.pathname === "/api/ui/config") body = { agent_registry: true, hide_cuga_logo: true };
        else if (url.pathname === "/api/secrets/config") body = { force_env: true, mode: "local" };
        else if (url.pathname === "/api/agents") body = { agents: [{ id: "cuga", name: "Roster supervisor", kind: "supervisor" }] };
        else if (url.pathname === "/api/manage/config" && route.request().method() === "POST") {
          published = route.request().postDataJSON().config;
          body = { status: "success", version: 2 };
        } else if (url.pathname === "/api/manage/config") body = { config, version: 1 };
        else if (url.pathname === "/api/commands") body = [];
        else if (url.pathname === "/api/skills") body = { skills: [] };
        else if (url.pathname === "/api/tools/list") body = { apps: [], tools: [] };
        else if (url.pathname === "/api/manage/config/history") body = { versions: [] };
        else if (url.pathname === "/api/manage/config/draft") body = { status: "success", version: "draft" };
        await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
      });

      await page.goto("/manage/cuga");
      await expect(page.locator("#agent-name")).toHaveValue("Roster supervisor");
      await page.getByRole("button", { name: "Special Instructions", exact: true }).click();
      await page.locator("#special-instructions").fill(instructions);
      await page.getByRole("button", { name: "Publish", exact: true }).click();
      await expect.poll(() => published).toBeDefined();

      expect(published!.special_instructions).toBe(instructions);
      expect(published!.supervisor.subAgents).toEqual(config.supervisor.subAgents);
      if (model) expect(published!.llm).toMatchObject(config.llm);
      else expect(published!.llm).toBeUndefined();
    });
  }
}
