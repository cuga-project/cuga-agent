import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("../api", () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));

const action = {
  action_id: "mcp_app_interaction",
  callback_url: "/stream",
  additional_data: {
    tool: { mcp_app: { interaction_id: "i-1", server: "table_designer", tool: "propose_table" } },
  },
};

async function load(location: string, sandboxOrigin: string | null = null) {
  vi.resetModules();
  vi.stubGlobal("window", { location: { href: location } });
  apiFetch.mockResolvedValue({ ok: true, json: async () => ({ sandbox_origin: sandboxOrigin }) });
  return import("./mcpApps");
}

afterEach(() => {
  vi.unstubAllGlobals();
  apiFetch.mockReset();
});

describe("MCP Apps actions", () => {
  it("reads the app info only from an mcp_app_interaction action", async () => {
    const { mcpAppInfoFromAction } = await load("http://localhost:7860/chat");
    expect(mcpAppInfoFromAction(action)?.interaction_id).toBe("i-1");
    expect(mcpAppInfoFromAction({ ...action, action_id: "tool_approval" })).toBeNull();
    expect(mcpAppInfoFromAction({ action_id: "mcp_app_interaction", additional_data: {} })).toBeNull();
    expect(mcpAppInfoFromAction(null)).toBeNull();
  });

  it("recognises only the replayed action events of a resumed form", async () => {
    const { isMcpAppActionEcho } = await load("http://localhost:7860/chat");
    const echo = JSON.stringify(action);
    expect(isMcpAppActionEcho("WaitForResponse", echo)).toBe(true);
    expect(isMcpAppActionEcho("CodeAgent", echo)).toBe(true);
    expect(isMcpAppActionEcho("Answer", echo)).toBe(false);
    expect(isMcpAppActionEcho("CodeAgent", "print('mcp_app_interaction')")).toBe(false);
    expect(isMcpAppActionEcho("CodeAgent", JSON.stringify({ action_id: "mcp_app_interaction" }))).toBe(false);
  });
});

describe("sandbox proxy origin", () => {
  it("swaps localhost and 127.0.0.1 on the chat's own port", async () => {
    expect(String(await (await load("http://localhost:7860/chat")).sandboxProxyUrl())).toBe(
      "http://127.0.0.1:7860/mcp-apps/sandbox_proxy.html",
    );
    expect(String(await (await load("http://127.0.0.1:3002/chat")).sandboxProxyUrl())).toBe(
      "http://localhost:3002/mcp-apps/sandbox_proxy.html",
    );
  });

  it("uses the configured origin when the server sets one", async () => {
    const { sandboxProxyUrl } = await load("https://cuga.example.com/chat", "https://sandbox.example.com");
    expect(String(await sandboxProxyUrl())).toBe("https://sandbox.example.com/mcp-apps/sandbox_proxy.html");
  });

  it("refuses to run the app on the chat's own origin", async () => {
    const { sandboxProxyUrl } = await load("https://cuga.example.com/chat");
    await expect(sandboxProxyUrl()).rejects.toThrow("CUGA_MCP_APPS_SANDBOX_ORIGIN");
  });
});
