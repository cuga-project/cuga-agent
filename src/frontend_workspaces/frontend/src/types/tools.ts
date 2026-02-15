export type ToolType = "mcp" | "openapi";

export type AuthType =
  | "none"
  | "header"
  | "bearer"
  | "api-key"
  | "basic"
  | "query"
  | "oauth2";

export interface ToolAuth {
  type: AuthType;
  key?: string;
  value?: string;
}

export interface ToolEntry {
  name: string;
  type: ToolType;
  url: string;
  description?: string;
  auth?: ToolAuth;
}

export const AUTH_TYPE_OPTIONS: { value: AuthType; label: string; needsKey: boolean }[] = [
  { value: "none", label: "No auth", needsKey: false },
  { value: "header", label: "Header", needsKey: true },
  { value: "bearer", label: "Bearer token", needsKey: false },
  { value: "api-key", label: "API key (query)", needsKey: true },
  { value: "basic", label: "Basic (user:pass)", needsKey: false },
  { value: "query", label: "Query parameter", needsKey: true },
  { value: "oauth2", label: "OAuth 2", needsKey: false },
];
