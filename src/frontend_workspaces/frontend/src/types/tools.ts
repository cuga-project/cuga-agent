export type ToolType = "mcp" | "openapi";

export type AuthType =
  | "none"
  | "header"
  | "bearer"
  | "api-key"
  | "basic"
  | "query"
  /** MCP gateway instance API key, exchanged by the registry for an instance token. */
  | "account_iam_apikey";

export interface ToolAuth {
  type: AuthType;
  key?: string;
  value?: string;
}

export type McpTransport = "sse" | "stdio" | "http";

/** Who the gateway sees on a tool call: the instance API key's service ID. */
export type GatewayRunAs = "service";

/** MCP gateway instance entry. `url` holds the instance URL (https://<gateway>/?instance=<id>). */
export interface ToolGateway {
  instance_url: string;
  run_as: GatewayRunAs;
}

/** A tool the gateway instance exposes, grouped by the gateway's MCP server. */
export interface GatewayServer {
  name: string;
  tools: { name: string; description: string }[];
}

const GATEWAY_INSTANCE_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,127}$/;

/** The instance ID of a gateway instance URL, or null when the URL is not one. */
export function parseGatewayInstanceUrl(url: string): string | null {
  try {
    const u = new URL(url.trim());
    if (u.protocol !== "https:" && u.protocol !== "http:") return null;
    const id = (u.searchParams.get("instance") ?? "").trim();
    return GATEWAY_INSTANCE_RE.test(id) ? id : null;
  } catch {
    return null;
  }
}

export interface ToolEntry {
  name: string;
  type: ToolType;
  url?: string;
  description?: string;
  auth?: ToolAuth;
  /** When set, only these tool/operation ids are enabled (registry include list). Omit or empty = all enabled. */
  include?: string[];
  /** Command-based MCP (e.g. npx). When set, transport is stdio. */
  command?: string;
  args?: string[];
  /** Environment variables for command-based MCP servers. Values may be literal strings or secret refs. */
  env?: Record<string, string>;
  transport?: McpTransport;
  /** Set for an MCP gateway instance; `include` then lists the selected gateway tools. */
  gateway?: ToolGateway;
  /** Environment variables passed to STDIO transport subprocess. */
  env?: Record<string, string>;
}

export const AUTH_TYPE_OPTIONS: { value: AuthType; label: string; needsKey: boolean }[] = [
  { value: "none", label: "No auth", needsKey: false },
  { value: "header", label: "Header", needsKey: true },
  { value: "bearer", label: "Bearer token", needsKey: false },
  { value: "api-key", label: "API key (query)", needsKey: true },
  { value: "basic", label: "Basic (user:pass)", needsKey: false },
  { value: "query", label: "Query parameter", needsKey: true },
];
