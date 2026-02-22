export type ToolType = "mcp" | "openapi";

export type AuthType =
  | "none"
  | "header"
  | "bearer"
  | "api-key"
  | "basic"
  | "query";

export interface ToolAuth {
  type: AuthType;
  key?: string;
  value?: string;
}

export type McpTransport = "sse" | "stdio" | "http";

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
  transport?: McpTransport;
}

export const AUTH_TYPE_OPTIONS: { value: AuthType; label: string; needsKey: boolean }[] = [
  { value: "none", label: "No auth", needsKey: false },
  { value: "header", label: "Header", needsKey: true },
  { value: "bearer", label: "Bearer token", needsKey: false },
  { value: "api-key", label: "API key (query)", needsKey: true },
  { value: "basic", label: "Basic (user:pass)", needsKey: false },
  { value: "query", label: "Query parameter", needsKey: true },
];
