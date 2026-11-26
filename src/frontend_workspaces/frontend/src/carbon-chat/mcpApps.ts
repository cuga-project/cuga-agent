/**
 * MCP Apps (SEP-1865) host support for the CUGA chat.
 *
 * When the agent calls a tool that declares `_meta.ui.resourceUri`, the backend
 * pauses the turn and streams a `SuggestHumanActions` event whose action id is
 * `mcp_app_interaction`. The chat renders the tool's UI resource in a sandboxed
 * iframe (see McpAppWidget), proxies the app's `tools/call` requests to the app's
 * own server, and resumes the agent when the app sends `ui/message`.
 */
import { apiFetch } from "../api";

export const MCP_APP_ACTION_ID = "mcp_app_interaction";
export const MCP_APP_RESPONSE_TYPE = "mcp_app";
export const MCP_APP_USER_DEFINED_TYPE = "cuga_mcp_app";

/** What the backend puts in `additional_data.tool.mcp_app` of the action. */
export interface McpAppActionInfo {
  interaction_id: string;
  thread_id?: string | null;
  server: string;
  tool: string;
  agent_tool: string;
  tool_title?: string | null;
  resource_uri: string;
  tool_input: Record<string, unknown>;
  tool_result: Record<string, unknown>;
}

export interface McpAppResource {
  interaction_id: string;
  status: "pending" | "completed";
  html: string;
  csp: Record<string, string[]>;
  permissions: Record<string, object>;
  prefers_border?: boolean | null;
  tool_input: Record<string, unknown>;
  tool_result: Record<string, unknown>;
}

export class McpAppRequestError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export function mcpAppInfoFromAction(action: any): McpAppActionInfo | null {
  const info = action?.additional_data?.tool?.mcp_app;
  if (action?.action_id !== MCP_APP_ACTION_ID || !info || typeof info.interaction_id !== "string") return null;
  return info as McpAppActionInfo;
}

/** A stream event that only repeats a paused MCP App action (sent when the turn resumes). */
export function isMcpAppActionEcho(name: string | undefined, data: unknown): boolean {
  if (name !== "WaitForResponse" && name !== "CodeAgent") return false;
  if (typeof data !== "string" || !data.includes(MCP_APP_ACTION_ID)) return false;
  try {
    const parsed = JSON.parse(data);
    return parsed?.action_id === MCP_APP_ACTION_ID && typeof parsed?.callback_url === "string";
  } catch {
    return false;
  }
}

async function post<T>(path: string, body: unknown, threadId: string | null | undefined, signal?: AbortSignal): Promise<T> {
  const response = await apiFetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(threadId ? { "X-Thread-ID": threadId } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      if (data?.detail) detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* keep the status text */
    }
    throw new McpAppRequestError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export function fetchMcpAppResource(interactionId: string, threadId?: string | null, signal?: AbortSignal) {
  return post<McpAppResource>("/api/mcp-apps/resource", { interaction_id: interactionId }, threadId, signal);
}

export function callMcpAppTool(
  interactionId: string,
  name: string,
  args: Record<string, unknown> | undefined,
  threadId?: string | null,
) {
  return post<Record<string, unknown>>(
    "/api/mcp-apps/tools/call",
    { interaction_id: interactionId, name, arguments: args ?? {} },
    threadId,
  );
}

export function updateMcpAppModelContext(
  interactionId: string,
  params: { content?: unknown[]; structuredContent?: Record<string, unknown> },
  threadId?: string | null,
) {
  return post<Record<string, never>>(
    "/api/mcp-apps/model-context",
    { interaction_id: interactionId, ...params },
    threadId,
  );
}

let sandboxOriginPromise: Promise<string | null> | null = null;

async function configuredSandboxOrigin(): Promise<string | null> {
  if (!sandboxOriginPromise) {
    sandboxOriginPromise = apiFetch("/api/mcp-apps/config")
      .then((r) => (r.ok ? r.json() : {}))
      .then((data: { sandbox_origin?: string | null }) => data?.sandbox_origin || null)
      .catch(() => null);
  }
  return sandboxOriginPromise;
}

/**
 * URL of the sandbox proxy. MCP Apps requires it to be on a different origin from
 * the chat; locally we swap localhost <-> 127.0.0.1 on the same port, otherwise
 * CUGA_MCP_APPS_SANDBOX_ORIGIN must name a separate origin that serves the proxy.
 */
export async function sandboxProxyUrl(): Promise<URL> {
  const configured = await configuredSandboxOrigin();
  const here = new URL(window.location.href);
  let origin = configured;
  if (!origin) {
    const swap: Record<string, string> = { localhost: "127.0.0.1", "127.0.0.1": "localhost" };
    const host = swap[here.hostname];
    if (!host) {
      throw new Error(
        "Interactive tool forms need a separate sandbox origin. Set CUGA_MCP_APPS_SANDBOX_ORIGIN on the server.",
      );
    }
    origin = `${here.protocol}//${host}${here.port ? `:${here.port}` : ""}`;
  }
  return new URL("/mcp-apps/sandbox_proxy.html", origin);
}
