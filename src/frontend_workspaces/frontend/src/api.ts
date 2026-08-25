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

let authConfigCache: { enabled: boolean; authorization_enabled: boolean } | null = null;

export async function getAuthConfig(): Promise<{ enabled: boolean; authorization_enabled: boolean }> {
  if (authConfigCache !== null) return authConfigCache;
  const base = getApiBaseUrl();
  const res = await fetch(`${base}/api/auth/config`, { credentials: "include" });
  const data = await res.json().catch(() => ({ enabled: false, authorization_enabled: false }));
  authConfigCache = {
    enabled: !!data.enabled,
    authorization_enabled: !!data.authorization_enabled
  };
  return authConfigCache;
}

export type UiConfig = {
  hide_cuga_logo: boolean;
  brand_name: string;
  agent_registry: boolean;
};

let uiConfigCache: UiConfig | null = null;

export async function getUiConfig(): Promise<UiConfig> {
  if (uiConfigCache !== null) return uiConfigCache;
  const base = getApiBaseUrl();
  const res = await fetch(`${base}/api/ui/config`, { credentials: "include" });
  const data = await res.json().catch(() => ({ hide_cuga_logo: false, brand_name: "CUGA Agent" }));
  uiConfigCache = {
    hide_cuga_logo: !!data.hide_cuga_logo,
    brand_name: data.brand_name && String(data.brand_name).trim() ? String(data.brand_name).trim() : "CUGA Agent",
    agent_registry: !!data.agent_registry,
  };
  return uiConfigCache;
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
      const { isLoginInProgress, markLoginInProgress } = await import("./auth");
      if (!isLoginInProgress()) {
        markLoginInProgress();
        window.location.href = `${base}/auth/login`;
      }
    }
  }
  if (res.status === 403) {
    console.warn(`Access denied (403) for ${String(url)}. User may lack required role.`);
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
    body: JSON.stringify({ thread_id: threadId }),
  });
}

export async function postStream(
  body: { query: string } | object,
  options: {
    threadId: string;
    useDraft?: boolean;
    disableHistory?: boolean;
    signal?: AbortSignal;
    agentId?: string;
  }
): Promise<Response> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Thread-ID": options.threadId,
    "X-Agent-ID": options.agentId || getKnowledgeAgentId(),
  };
  if (options.useDraft) headers["X-Use-Draft"] = "true";
  if (options.disableHistory) headers["X-Disable-History"] = "true";
  return apiFetch(`/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

export async function getConversationStreamEvents(threadId: string, agentId?: string): Promise<Response> {
  return apiFetch(
    `/api/conversation-stream-events/${threadId}?agent_id=${encodeURIComponent(agentId || getKnowledgeAgentId())}&user_id=default_user`
  );
}

export async function getConversationMessages(threadId: string, agentId?: string): Promise<Response> {
  return apiFetch(
    `/api/conversation-messages/${threadId}?agent_id=${encodeURIComponent(agentId || getKnowledgeAgentId())}&user_id=default_user`
  );
}

export async function getManageConfig(draft?: boolean, agentId?: string): Promise<Response> {
  const params = new URLSearchParams();
  if (draft) params.set("draft", "1");
  if (agentId) params.set("agent_id", agentId);
  const q = params.toString() ? `?${params.toString()}` : "";
  return apiFetch(`/api/manage/config${q}`);
}

export async function getManageConfigVersion(version: string, agentId?: string): Promise<Response> {
  const params = new URLSearchParams({ version });
  if (agentId) params.set("agent_id", agentId);
  return apiFetch(`/api/manage/config?${params.toString()}`);
}

export async function getLlmModels(
  apiKey: string,
  disableSsl?: boolean,
  provider?: string
): Promise<Response> {
  const params = new URLSearchParams();
  if (disableSsl) params.set("disable_ssl", "true");
  if (provider) params.set("provider", provider);
  const q = params.toString() ? `?${params.toString()}` : "";
  const headers: Record<string, string> = {};
  if (apiKey) headers["X-LLM-API-Key"] = apiKey;
  return apiFetch(`/api/manage/llm/models${q}`, { headers });
}

export async function getManageConfigHistory(agentId?: string): Promise<Response> {
  const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return apiFetch(`/api/manage/config/history${q}`);
}

export async function postManageConfigDraft(
  config: unknown,
  agentId?: string,
  signal?: AbortSignal,
): Promise<Response> {
  const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return apiFetch(`/api/manage/config/draft${q}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config }),
    signal,
  });
}

// Autosave PATCH helpers — each accepts an optional ``signal`` so the
// caller can cancel an in-flight request when a newer config arrives
// (the rapid-profile-pick race). ``apiFetch`` spreads ``init`` into
// ``fetch``'s second arg, so passing ``signal`` here propagates
// natively. See CLIENT_CANCELLATION_CONTRACT.md for the contract.
export async function patchManageConfigDraftAgent(
  agent: { name?: string; description?: string; kind?: "single" | "supervisor" },
  agentId?: string,
  signal?: AbortSignal,
): Promise<Response> {
  const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return apiFetch(`/api/manage/config/draft/agent${q}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent }),
    signal,
  });
}

export async function patchManageConfigDraftLlm(
  llm: unknown,
  agentId?: string,
  signal?: AbortSignal,
): Promise<Response> {
  const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return apiFetch(`/api/manage/config/draft/llm${q}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ llm }),
    signal,
  });
}

export async function patchManageConfigDraftTools(
  tools: unknown,
  agentId?: string,
  signal?: AbortSignal,
): Promise<Response> {
  const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return apiFetch(`/api/manage/config/draft/tools${q}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tools }),
    signal,
  });
}

export async function patchManageConfigDraftPolicies(
  policies: unknown,
  agentId?: string,
  signal?: AbortSignal,
): Promise<Response> {
  const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return apiFetch(`/api/manage/config/draft/policies${q}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ policies }),
    signal,
  });
}

export async function patchManageConfigDraftSupervisor(
  supervisor: unknown,
  agentId?: string,
  signal?: AbortSignal,
  saveSeq?: number,
): Promise<Response> {
  const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return apiFetch(`/api/manage/config/draft/supervisor${q}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ supervisor, ...(saveSeq != null ? { saveSeq } : {}) }),
    signal,
  });
}

export async function postManageConfig(
  config: unknown,
  agentId?: string,
  signal?: AbortSignal,
): Promise<Response> {
  const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return apiFetch(`/api/manage/config${q}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config }),
    signal,
  });
}

export async function patchManageConfigDraftSpecialInstructions(
  specialInstructions: string,
  agentId?: string,
  signal?: AbortSignal,
): Promise<Response> {
  const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return apiFetch(`/api/manage/config/draft/special_instructions${q}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ special_instructions: specialInstructions }),
    signal,
  });
}

export async function patchManageConfigDraftKnowledge(
  knowledge: unknown,
  agentId?: string,
  signal?: AbortSignal,
): Promise<Response> {
  const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return apiFetch(`/api/manage/config/draft/knowledge${q}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ knowledge }),
    signal,
  });
}

export function triggerKnowledgeReindex(): Promise<Response> {
  return knowledgeApiFetch("/api/knowledge/reindex", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope: "agent" }),
  });
}

// User-triggered migration + reindex after a vector-config change. Routes
// to a separate manage-side endpoint that finds the source collection (old
// hash dir), copies files to the engine's CURRENT vector_config_hash dir,
// reindexes the target with the new embedder, and updates
// app_state.knowledge_config_hash so subsequent searches + ingests point
// at the migrated data. Returns the same shape ``triggerKnowledgeReindex``
// does so callers can treat them interchangeably.
export function triggerKnowledgeReindexForConfig(agentId?: string): Promise<Response> {
  const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return apiFetch(`/api/manage/knowledge/reindex_for_config${q}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
}

export async function getToolsList(draft?: boolean): Promise<Response> {
  const q = draft ? "?draft=1" : "";
  return apiFetch(`/api/tools/list${q}`);
}

export async function getSkills(): Promise<Response> {
  return apiFetch("/api/skills");
}

export async function getConversationThreads(agentId?: string): Promise<Response> {
  return apiFetch(`/api/conversation-threads?agent_id=${encodeURIComponent(agentId || getKnowledgeAgentId())}`);
}

export async function getConversations(): Promise<Response> {
  return apiFetch("/api/conversations");
}

export async function deleteConversation(threadId: string, agentId?: string): Promise<Response> {
  return apiFetch(`/api/conversations/${threadId}?agent_id=${encodeURIComponent(agentId || getKnowledgeAgentId())}`, {
    method: "DELETE",
  });
}

export interface SlashCommandInfo {
  name: string;
  kind: "skill";
  description: string;
  argument_hint: string | null;
}

export async function getCommands(): Promise<SlashCommandInfo[]> {
  const response = await apiFetch("/api/commands");
  if (!response.ok) {
    throw new Error(`Failed to load slash commands: HTTP ${response.status}`);
  }
  const data = await response.json().catch(() => ({ commands: [] }));
  const commands = Array.isArray(data?.commands) ? data.commands : [];
  return commands.map((c: any) => ({
    name: String(c?.name ?? ""),
    kind: "skill" as const,
    description: typeof c?.description === "string" ? c.description : "",
    argument_hint: typeof c?.argument_hint === "string" ? c.argument_hint : null,
  }));
}

export async function getWorkspaceTree(threadId?: string, forceRefresh = false): Promise<Response> {
  const params = new URLSearchParams();
  if (threadId) params.set("thread_id", threadId);
  if (forceRefresh) params.set("_", String(Date.now()));
  const q = params.toString();
  return apiFetch(`/api/workspace/tree${q ? `?${q}` : ""}`);
}

export async function getWorkspaceFile(path: string, threadId?: string): Promise<Response> {
  const params = new URLSearchParams({ path });
  if (threadId) params.set("thread_id", threadId);
  return apiFetch(`/api/workspace/file?${params.toString()}`);
}

export async function getWorkspaceDownload(path: string, threadId?: string): Promise<Response> {
  const params = new URLSearchParams({ path });
  if (threadId) params.set("thread_id", threadId);
  return apiFetch(`/api/workspace/download?${params.toString()}`);
}

export async function uploadWorkspaceFile(file: File, threadId: string): Promise<Response> {
  const formData = new FormData();
  formData.append("file", file);
  const params = new URLSearchParams({ thread_id: threadId });
  return apiFetch(`/api/workspace/upload?${params.toString()}`, {
    method: "POST",
    headers: { "X-Thread-ID": threadId },
    body: formData,
  });
}

export async function getAgents(): Promise<Response> {
  return apiFetch("/api/agents");
}

export async function createAgent(
  name: string,
  description: string,
  kind: "single" | "supervisor"
): Promise<Response> {
  return apiFetch("/api/agents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description, kind }),
  });
}

export async function deleteAgent(agentId: string): Promise<Response> {
  return apiFetch(`/api/agents/${encodeURIComponent(agentId)}`, { method: "DELETE" });
}

export async function getSecrets(agentId?: string): Promise<Response> {
  const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return apiFetch(`/api/secrets${q}`);
}

export async function getSecretsConfig(): Promise<Response> {
  return apiFetch("/api/secrets/config");
}

export async function createSecret(
  id: string,
  value: string,
  description?: string,
  tags?: Record<string, string>,
  agentId?: string
): Promise<Response> {
  return apiFetch("/api/secrets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, value, description, tags, agent_id: agentId }),
  });
}

export async function updateSecret(
  id: string,
  value: string,
  description?: string,
  tags?: Record<string, string>
): Promise<Response> {
  return apiFetch(`/api/secrets/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value, description, tags }),
  });
}

export async function deleteSecret(id: string): Promise<Response> {
  return apiFetch(`/api/secrets/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

// ---------------------------------------------------------------------------
// Knowledge API (unified — LangChain + Milvus Lite engine)
// ---------------------------------------------------------------------------

// Current agent context — set by the app when agent is selected. Also used by postStream /
// conversation endpoints as the X-Agent-ID / agent_id fallback (issue #101) so calls made
// before the async agent-context fetch resolves still target the real default agent.
let _knowledgeAgentId = "cuga-default";
export function setKnowledgeAgentId(agentId: string) {
  _knowledgeAgentId = agentId;
}
export function getKnowledgeAgentId(): string {
  return _knowledgeAgentId;
}

/**
 * Central knowledge API helper. Injects X-Agent-ID and optional X-Thread-ID
 * on every knowledge request. All knowledge calls MUST go through this helper.
 */
function knowledgeApiFetch(
  url: string,
  init?: RequestInit,
  threadId?: string
): Promise<Response> {
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string> || {}),
    "X-Agent-ID": _knowledgeAgentId,
  };
  if (threadId) {
    headers["X-Thread-ID"] = threadId;
  }
  return apiFetch(url, { ...init, headers });
}

// --- Health & Settings ---

export function getKnowledgeHealth(): Promise<Response> {
  return knowledgeApiFetch("/api/knowledge/health");
}

export function getKnowledgeAccelerator(): Promise<Response> {
  return apiFetch("/api/manage/knowledge/accelerator");
}

export function getKnowledgeDefaults(): Promise<Response> {
  return apiFetch("/api/manage/knowledge/defaults");
}

// Detected embedding-provider presets from .env / shell environment.
// Returns booleans + suggested config only — NEVER the actual env
// values. Used to power the "Quick setup from environment" panel in
// the knowledge config UI.
export function getKnowledgeEnvPresets(): Promise<Response> {
  return apiFetch("/api/manage/knowledge/env-presets");
}

export function testEmbeddingsConnection(body: {
  provider: string;
  model?: string;
  api_key?: string;
  base_url?: string;
  extra_params?: Record<string, unknown>;
}): Promise<Response> {
  return apiFetch("/api/manage/knowledge/test_embeddings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function enableKnowledge(): Promise<Response> {
  return knowledgeApiFetch("/api/knowledge/enable", { method: "POST" });
}

export function getKnowledgeSettings(): Promise<Response> {
  return knowledgeApiFetch("/api/knowledge/settings");
}

export function updateKnowledgeSettings(settings: Record<string, unknown>): Promise<Response> {
  return knowledgeApiFetch("/api/knowledge/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
}

// Per-conversation knowledge settings overrides (citations toggle).
// Backed by GET/PATCH /api/knowledge/session/settings, keyed on the
// X-Thread-ID header injected by knowledgeApiFetch.
export async function getSessionKnowledgeSettings(
  threadId: string
): Promise<{ overrides: Record<string, unknown> }> {
  const res = await knowledgeApiFetch("/api/knowledge/session/settings", { method: "GET" }, threadId);
  if (!res.ok) throw new Error(`session settings: ${res.status}`);
  return res.json();
}

export async function patchSessionKnowledgeSettings(
  threadId: string,
  patch: { citations_enabled?: boolean },
): Promise<{ overrides: Record<string, unknown> }> {
  const res = await knowledgeApiFetch(
    "/api/knowledge/session/settings",
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) },
    threadId,
  );
  if (!res.ok) throw new Error(`session settings patch: ${res.status}`);
  return res.json();
}

// --- Documents (agent scope) ---

export function uploadKnowledgeDocuments(files: File[], replaceDuplicates = true): Promise<Response> {
  const formData = new FormData();
  files.forEach((f) => formData.append("files", f));
  formData.append("scope", "agent");
  formData.append("replace_duplicates", String(replaceDuplicates));
  return knowledgeApiFetch("/api/knowledge/documents", {
    method: "POST",
    body: formData,
  });
}

export function uploadKnowledgeDocument(
  file: File,
  replaceDuplicates = true,
  wait = true,
  signal?: AbortSignal,
): Promise<Response> {
  const formData = new FormData();
  formData.append("files", file);
  formData.append("scope", "agent");
  formData.append("replace_duplicates", String(replaceDuplicates));
  formData.append("wait", String(wait));
  return knowledgeApiFetch("/api/knowledge/documents", {
    method: "POST",
    body: formData,
    signal,
  });
}

export function listKnowledgeDocuments(signal?: AbortSignal): Promise<Response> {
  return knowledgeApiFetch(
    "/api/knowledge/documents?scope=agent",
    signal ? { signal } : undefined,
  );
}

export function deleteKnowledgeDocument(filename: string): Promise<Response> {
  return knowledgeApiFetch("/api/knowledge/documents", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope: "agent", filename }),
  });
}

export function getKnowledgeDocumentFile(
  scope: "agent" | "session",
  filename: string,
  threadId?: string
): Promise<Response> {
  const params = new URLSearchParams({
    scope,
    filename,
  });
  return knowledgeApiFetch(`/api/knowledge/documents/file?${params.toString()}`, undefined, threadId);
}

// --- Search ---

export function searchKnowledge(
  query: string,
  limit = 10,
  scoreThreshold = 0
): Promise<Response> {
  return knowledgeApiFetch("/api/knowledge/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope: "agent", query, limit, score_threshold: scoreThreshold, include_scores: true }),
  });
}

// --- Tasks ---

export function getKnowledgeTasks(): Promise<Response> {
  return knowledgeApiFetch("/api/knowledge/tasks?scope=agent");
}

export function getKnowledgeTaskStatus(
  taskId: string,
  signal?: AbortSignal,
): Promise<Response> {
  return knowledgeApiFetch(
    `/api/knowledge/tasks/${encodeURIComponent(taskId)}`,
    signal ? { signal } : undefined,
  );
}

export function cancelKnowledgeTask(taskId: string): Promise<Response> {
  return knowledgeApiFetch(`/api/knowledge/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Session-level knowledge (scope=session via unified API)
// ---------------------------------------------------------------------------

export function uploadSessionKnowledgeDocuments(
  threadId: string,
  files: File[],
  replaceDuplicates = true
): Promise<Response> {
  const formData = new FormData();
  files.forEach((f) => formData.append("files", f));
  formData.append("scope", "session");
  formData.append("replace_duplicates", String(replaceDuplicates));
  return knowledgeApiFetch("/api/knowledge/documents", {
    method: "POST",
    body: formData,
  }, threadId);
}

export function listSessionKnowledgeDocuments(
  threadId: string
): Promise<Response> {
  return knowledgeApiFetch("/api/knowledge/documents?scope=session", undefined, threadId);
}

export function deleteSessionKnowledgeDocument(
  threadId: string,
  filename: string
): Promise<Response> {
  return knowledgeApiFetch("/api/knowledge/documents", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope: "session", filename }),
  }, threadId);
}

export function deleteSessionKnowledgeCollection(
  threadId: string
): Promise<Response> {
  return knowledgeApiFetch("/api/knowledge/session", {
    method: "DELETE",
  }, threadId);
}
