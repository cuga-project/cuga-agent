import type { Page } from "@playwright/test";

export const agents = [
  { id: "cuga-default", name: "CUGA supervisor", kind: "supervisor", description: "Coordinate specialist agents to complete complex tasks.", tools_count: 4, latest_version: 3 },
  { id: "weatherbot", name: "Weather specialist", kind: "single", description: "Look up current conditions and plan around the forecast.", tools_count: 2, latest_version: 2 },
  { id: "pricebot", name: "Price specialist", kind: "single", description: "Research product prices and compare available options.", tools_count: 3, latest_version: 1 },
];
export async function stub(page: Page) {
  const deletions: string[] = [];
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const id = url.searchParams.get("agent_id") || "cuga-default";
    let body: unknown = {};
    if (route.request().method() === "DELETE") deletions.push(url.pathname);
    if (url.pathname === "/api/auth/config") body = { enabled: false, authorization_enabled: false };
    else if (url.pathname === "/api/ui/config") body = { agent_registry: true, hide_cuga_logo: true, evolve_memory_enabled: true };
    else if (url.pathname === "/api/agents") body = { agents };
    else if (url.pathname === "/api/commands") body = [];
    else if (url.pathname === "/api/skills") body = { skills: [] };
    else if (url.pathname === "/api/agent/context") body = { agent_id: id, config_version: 3, knowledge_enabled: true, session_level_knowledge_enabled: true };
    else if (url.pathname === "/api/manage/config") body = { version: 3, config: { agent: agents.find((a) => a.id === id), knowledge: { enabled: true, agent_level_enabled: true, session_level_enabled: true, citations_enabled: true }, supervisor: { subAgents: [{ kind: "internal", ref: "weatherbot" }] } } };
    else if (url.pathname === "/api/tools/list") body = { tools: [{ name: "weather", app: "Weather service", description: "Current weather conditions" }] };
    else if (url.pathname === "/api/conversations") body = [{ id: "recent", title: "Plan next week's customer visits", timestamp: Date.parse("2026-10-31T08:00:00Z"), preview: "", status: "active" }];
    else if (url.pathname.includes("conversation-stream-events")) body = { events: [] };
    else if (url.pathname.includes("conversation-messages")) body = { messages: [] };
    else if (url.pathname === "/api/workspace/tree") body = { tree: [] };
    else if (url.pathname.endsWith("/settings")) body = { instance_enabled: true, user_enabled: true, effective_enabled: true, episodic_enabled: true };
    await route.fulfill({ json: body });
  });
  return deletions;
}
