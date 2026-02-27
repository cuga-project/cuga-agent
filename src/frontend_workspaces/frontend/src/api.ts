/*
 * Central API client. All backend requests go through this module so auth (cookies, 401 handling) is consistent.
 */

export function getApiBaseUrl(): string {
  if (typeof window === "undefined") return "http://localhost:7860";
  const { hostname, protocol, origin, port } = window.location;
  if (hostname !== "localhost" && hostname !== "127.0.0.1") return origin;
  if (port === "3002") return origin;
  return `${protocol}//${hostname}:7860`;
}

let authConfigCache: { enabled: boolean } | null = null;

export async function getAuthConfig(): Promise<{ enabled: boolean }> {
  if (authConfigCache !== null) return authConfigCache;
  const base = getApiBaseUrl();
  const res = await fetch(`${base}/api/auth/config`, { credentials: "include" });
  const data = await res.json().catch(() => ({ enabled: false }));
  authConfigCache = { enabled: !!data.enabled };
  return authConfigCache;
}

export async function apiFetch(
  url: string | URL,
  init?: RequestInit
): Promise<Response> {
  const base = getApiBaseUrl();
  const fullUrl = typeof url === "string" && !url.startsWith("http") ? `${base}${url.startsWith("/") ? "" : "/"}${url}` : url;
  const res = await fetch(fullUrl, {
    ...init,
    credentials: "include",
    headers: { ...init?.headers },
  });
  if (res.status === 401) {
    const config = await getAuthConfig();
    if (config.enabled) {
      const loginUrl = `${base}/auth/login`;
      window.location.href = loginUrl;
    }
  }
  return res;
}

export async function postAuthCallback(code: string, state: string): Promise<Response> {
  const base = getApiBaseUrl();
  return apiFetch(`${base}/auth/callback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, state }),
  });
}

export async function postAuthLogout(): Promise<Response> {
  const base = getApiBaseUrl();
  return apiFetch(`${base}/auth/logout`, { method: "POST" });
}

export async function getAgentContext(): Promise<Response> {
  return apiFetch("/api/agent/context");
}

export async function getAgentState(threadId: string): Promise<Response> {
  return apiFetch(`/api/agent/state?thread_id=${encodeURIComponent(threadId)}`, {
    headers: { "X-Thread-ID": threadId },
  });
}

export async function postStop(threadId: string): Promise<Response> {
  const base = getApiBaseUrl();
  return apiFetch(`${base}/stop`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Thread-ID": threadId },
  });
}

export async function postStream(
  body: { query: string } | object,
  options: {
    threadId: string;
    useDraft?: boolean;
    disableHistory?: boolean;
    signal?: AbortSignal;
  }
): Promise<Response> {
  const base = getApiBaseUrl();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Thread-ID": options.threadId,
  };
  if (options.useDraft) headers["X-Use-Draft"] = "true";
  if (options.disableHistory) headers["X-Disable-History"] = "true";
  return apiFetch(`${base}/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

export async function getConversationStreamEvents(threadId: string): Promise<Response> {
  return apiFetch(
    `/api/conversation-stream-events/${threadId}?agent_id=cuga-default&user_id=default_user`
  );
}

export async function getConversationMessages(threadId: string): Promise<Response> {
  return apiFetch(
    `/api/conversation-messages/${threadId}?agent_id=cuga-default&user_id=default_user`
  );
}

export async function getManageConfig(draft?: boolean): Promise<Response> {
  const q = draft ? "?draft=1" : "";
  return apiFetch(`/api/manage/config${q}`);
}

export async function getManageConfigVersion(version: string): Promise<Response> {
  return apiFetch(`/api/manage/config?version=${encodeURIComponent(version)}`);
}

export async function getManageConfigHistory(): Promise<Response> {
  return apiFetch("/api/manage/config/history");
}

export async function postManageConfigDraft(config: unknown): Promise<Response> {
  return apiFetch("/api/manage/config/draft", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config }),
  });
}

export async function postManageConfig(config: unknown): Promise<Response> {
  return apiFetch("/api/manage/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config }),
  });
}

export async function getToolsList(draft?: boolean): Promise<Response> {
  const q = draft ? "?draft=1" : "";
  return apiFetch(`/api/tools/list${q}`);
}

export async function getConversationThreads(): Promise<Response> {
  return apiFetch("/api/conversation-threads?agent_id=cuga-default");
}

export async function getConversations(): Promise<Response> {
  return apiFetch("/api/conversations");
}

export async function deleteConversation(threadId: string): Promise<Response> {
  return apiFetch(`/api/conversations/${threadId}?agent_id=cuga-default`, {
    method: "DELETE",
  });
}

export async function getWorkspaceTree(): Promise<Response> {
  return apiFetch("/api/workspace/tree");
}

export async function getWorkspaceFile(path: string): Promise<Response> {
  return apiFetch(`/api/workspace/file?path=${encodeURIComponent(path)}`);
}

export async function getWorkspaceDownload(path: string): Promise<Response> {
  return apiFetch(`/api/workspace/download?path=${encodeURIComponent(path)}`);
}

export async function getAgents(): Promise<Response> {
  return apiFetch("/api/agents");
}
