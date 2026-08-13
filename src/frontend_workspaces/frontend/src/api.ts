/*
 * Central API client. All backend requests go through this module so auth (cookies, 401 handling) is consistent.
 */

export function getApiBaseUrl(): string {
  if (typeof window === "undefined") return "http://localhost:7860";
  const { origin, protocol } = window.location;
  // The SPA is served BY the FastAPI backend, so the API lives at the SAME origin — on whatever
  // port served this page: 7860, 8100, the :3002 webpack dev server (which proxies /api → backend),
  // or a production domain. This must NOT hardcode a port, or a CUGA server on any non-7860 port
  // (e.g. the events server on :8100) has its API calls silently sent to :7860 instead.
  // Only fall back to the default CUGA port for non-web origins (electron file://), which have none.
  if (protocol === "http:" || protocol === "https:") return origin;
  return "http://localhost:7860";
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

let uiConfigCache: { hide_cuga_logo: boolean; brand_name: string } | null = null;

export async function getUiConfig(): Promise<{ hide_cuga_logo: boolean; brand_name: string }> {
  if (uiConfigCache !== null) return uiConfigCache;
  const base = getApiBaseUrl();
  const res = await fetch(`${base}/api/ui/config`, { credentials: "include" });
  const data = await res.json().catch(() => ({ hide_cuga_logo: false, brand_name: "CUGA Agent" }));
  uiConfigCache = {
    hide_cuga_logo: !!data.hide_cuga_logo,
    brand_name: data.brand_name && String(data.brand_name).trim() ? String(data.brand_name).trim() : "CUGA Agent",
  };
  return uiConfigCache;
}

// ── the eventing layer's origin ───────────────────────────────────────────────────────────────
// Combined deployment: events is mounted on this same server, so same-origin is right and this
// resolves to getApiBaseUrl() — nothing changes. SPLIT deployment: the UI is served by cuga-core
// while /api/events/*, /api/concierge and /invoke live on the events service, so those calls must
// be sent there. The server tells us where via /api/ui/config (EVENTS_API_URL); resolved once and
// cached, and any failure falls back to same-origin rather than breaking the page.
const EVENTS_PATHS = ["/api/events", "/api/concierge", "/invoke"];
let eventsBaseCache: string | null = null;
let eventsBaseInFlight: Promise<string> | null = null;

export async function getEventsBaseUrl(): Promise<string> {
  if (eventsBaseCache !== null) return eventsBaseCache;
  if (!eventsBaseInFlight) {
    eventsBaseInFlight = fetch(`${getApiBaseUrl()}/api/ui/config`, { credentials: "include" })
      .then((r): Promise<Record<string, unknown>> => (r.ok ? r.json() : Promise.resolve({})))
      .then((c): string => {
        const configured = String(c?.events_api_url ?? "").replace(/\/$/, "");
        const resolved = configured || getApiBaseUrl();
        eventsBaseCache = resolved;
        return resolved;
      })
      .catch((): string => {
        const resolved = getApiBaseUrl();
        eventsBaseCache = resolved;
        return resolved;
      });
  }
  return eventsBaseInFlight;
}

export async function apiFetch(
  url: string | URL,
  init?: RequestInit
): Promise<Response> {
  const base = getApiBaseUrl();
  const isEvents =
    typeof url === "string" && EVENTS_PATHS.some((p) => url.startsWith(p));
  const callBase = isEvents ? await getEventsBaseUrl() : base;
  const fullUrl = typeof url === "string" && !url.startsWith("http") ? `${callBase}${url.startsWith("/") ? "" : "/"}${url}` : url;
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
  }
): Promise<Response> {
  const base = getApiBaseUrl();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Thread-ID": options.threadId,
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
  agent: { name?: string; description?: string },
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
// Events / Studio API (opt-in: EVENTS_ENABLED on the backend).
// These endpoints only exist when the events layer is mounted; the Studio UI
// hides itself when getEventsStatus() is not ok. The UI is dumb — it renders
// exactly what these return and never computes status itself.
// ---------------------------------------------------------------------------

export interface EventsStatus {
  ok: boolean;
  enabled: boolean;
  scope: string;
  backends: string[];
  worker_backend?: string;      // who does the hard work of answering (cuga by default)
  concierge_backend?: string;   // NL→flow control plane (react)
  ap_configured: boolean;
  project_grain: string;
  features: Record<string, boolean>;
}

// Returns null unless the events layer is actually mounted AND enabled — callers
// use that to hide the Studio entry point entirely. Robust against the SPA catch-all:
// when EVENTS_ENABLED is off, /api/events/status is unrouted, so the server returns
// index.html with HTTP 200. We therefore must NOT trust res.ok alone — we require a
// JSON content-type AND an explicit enabled:true, or the Studio would leak into vanilla CUGA.
export async function getEventsStatus(): Promise<EventsStatus | null> {
  try {
    const res = await apiFetch("/api/events/status");
    if (!res.ok) return null;
    const contentType = res.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) return null; // SPA fallback served HTML → events off
    const data = (await res.json()) as EventsStatus;
    return data && data.enabled === true ? data : null;         // only when the backend says enabled
  } catch {
    return null;
  }
}

export async function getEventsChannels(): Promise<Response> {
  return apiFetch("/api/events/channels");
}

export async function getEventsIntegrations(): Promise<Response> {
  return apiFetch("/api/events/integrations");
}

export async function getEventsSubscriptions(): Promise<Response> {
  return apiFetch("/api/events/subscriptions");
}

// Flow lifecycle — CUGA drives Activepieces internally (pause/resume/delete), so operators never
// open the AP console. getEventsFlowDetail returns the CUGA Source→Agent→Sink model + live AP flow.
export async function pauseFlow(id: string): Promise<Response> {
  return apiFetch(`/api/events/subscriptions/${encodeURIComponent(id)}/pause`, { method: "POST" });
}
export async function resumeFlow(id: string): Promise<Response> {
  return apiFetch(`/api/events/subscriptions/${encodeURIComponent(id)}/resume`, { method: "POST" });
}
export async function deleteFlow(id: string): Promise<Response> {
  return apiFetch(`/api/events/subscriptions/${encodeURIComponent(id)}`, { method: "DELETE" });
}
export async function getEventsFlowDetail(id: string): Promise<Response> {
  return apiFetch(`/api/events/subscriptions/${encodeURIComponent(id)}/flow`);
}

// Execution log — recent flow runs (joined to their subscription: agent/mode/integration/channel/
// status), and one run's detail with the agent's output.
export async function getEventsRuns(): Promise<Response> {
  return apiFetch("/api/events/runs");
}
export async function getEventsRunDetail(id: string): Promise<Response> {
  return apiFetch(`/api/events/runs/${encodeURIComponent(id)}`);
}

/**
 * The web channel's mailbox — asynchronous flow fires waiting for this browser.
 *
 * A flow armed in a chat here fires minutes or days later, when no request is in flight. Slack gets
 * a push; a tab can only be drained. So the server delivers into a per-thread mailbox and the chat
 * surface polls this. `since` is EXCLUSIVE — send back the `cursor` you were given and a message is
 * never rendered twice; send `0` to recover the backlog after a reload.
 *
 * `maxAgeSeconds` bounds that first load. A minute-by-minute cron piles up hundreds of fires, and
 * replaying all of them is a flood, not a recovery. The server applies the cutoff with its own
 * clock — deliberately not ours, since the cursor is a server timestamp and any disagreement
 * between the two clocks would skip or repeat messages.
 */
export async function getEventsInbox(
  threadId: string,
  since = 0,
  maxAgeSeconds = 86400,
): Promise<Response> {
  const age = since > 0 ? "" : `&max_age=${encodeURIComponent(String(maxAgeSeconds))}`;
  return apiFetch(
    `/api/events/inbox?thread_id=${encodeURIComponent(threadId)}&since=${encodeURIComponent(String(since))}${age}`,
  );
}

// The one agent CUGA's sub-agent roster (geobot, pricebot, …) — read-only; the supervisor picks among
// them internally. Source: supervisor_agents.yaml.
export async function getEventsAgents(): Promise<Response> {
  return apiFetch("/api/events/agents");
}

// The tool servers a builder can attach to an agent (drives the Agent editor form).
export async function getEventsMcpServers(): Promise<Response> {
  return apiFetch("/api/events/mcp-servers");
}

export interface AgentSpecBody {
  name: string;
  prompt?: string;
  backend?: string;                 // cuga | react
  mcp_servers?: string[];
  channels?: string[];
  // triggers = WHICH of the app's events this agent handles; absent/empty = all of them
  integrations?: { app: string; ownership: string; triggers?: string[] }[];
  access?: string[];
}

// Builder: create (or upsert) an agent. Idempotent by name.
export async function postEventsAgent(spec: AgentSpecBody): Promise<Response> {
  return apiFetch("/api/events/agents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec),
  });
}

// Builder: update an existing agent.
export async function putEventsAgent(name: string, spec: AgentSpecBody): Promise<Response> {
  return apiFetch(`/api/events/agents/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec),
  });
}

export async function getEventsExamples(): Promise<Response> {
  return apiFetch("/api/events/examples");
}

// The trigger registry — every (integration, event) the platform can watch, grouped per app.
// Drives the Agent editor's trigger-grain picker; generated from the backend registry.
export async function getEventsTriggers(): Promise<Response> {
  return apiFetch("/api/events/triggers");
}

// The caller's own connected integrations (which apps they've logged into).
export async function getEventsConnections(): Promise<Response> {
  return apiFetch("/api/events/connections");
}

// Where to send the user to log in for an integration (OAuth → redirects to consent).
// Per-connector setup guides (how to connect, creds present?, ownership options, steps).
export async function getEventsSetupGuides(): Promise<Response> {
  return apiFetch("/api/events/setup-guides");
}

export function eventsConnectUrl(app: string, ownership?: string): string {
  const own = ownership ? `?ownership=${encodeURIComponent(ownership)}` : "";
  return `${getApiBaseUrl()}/api/events/connect/${encodeURIComponent(app)}${own}`;
}

// Set/modify one connector credential (its .env variable) from the Studio — persists to .env AND
// applies live where the value is read at use-time (Slack/Box/OAuth-app). Admin only.
export async function postEventsSetCredential(key: string, value: string): Promise<Response> {
  return apiFetch("/api/events/admin/credential", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, value }),
  });
}

// Token apps (GitHub PAT / Telegram bot): store a pasted token as a per-user OR tenant connection.
export async function postEventsConnectToken(app: string, token: string, ownership?: string): Promise<Response> {
  return apiFetch(`/api/events/connect/${encodeURIComponent(app)}/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, ownership: ownership || "per_user" }),
  });
}

// The caller's profile (identity anchor): who they are, roles, linked channels, connections.
export async function getEventsMe(): Promise<Response> {
  return apiFetch("/api/events/me");
}

// Issue a link token to bind a channel (Telegram/Discord) to this profile.
export async function postEventsLinkChannel(channel: string): Promise<Response> {
  return apiFetch(`/api/events/link/${encodeURIComponent(channel)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

// Admin: list / add users (tenant).
export async function getEventsAdminUsers(): Promise<Response> {
  return apiFetch("/api/events/admin/users");
}
export async function postEventsAdminUser(user: {
  user_id: string; email?: string; roles?: string[]; password?: string;
}): Promise<Response> {
  return apiFetch("/api/events/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(user),
  });
}

// Admin: OAuth app credentials (client id/secret per provider) — UI instead of .env.
export async function getEventsAdminOAuthApps(): Promise<Response> {
  return apiFetch("/api/events/admin/oauth-apps");
}
export async function postEventsAdminOAuthApp(app: {
  app: string; client_id: string; client_secret: string; scopes?: string;
}): Promise<Response> {
  return apiFetch("/api/events/admin/oauth-apps", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(app),
  });
}

// Talk to the concierge (NL → reuse/create worker + arm trigger). ``dryRun``
// returns the plan with no side effects (great for a "preview" toggle).
export async function postConcierge(
  text: string,
  opts?: { threadId?: string; dryRun?: boolean; agent?: string }
): Promise<Response> {
  const q = opts?.dryRun ? "?dry_run=1" : "";
  return apiFetch(`/api/concierge${q}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      thread_id: opts?.threadId ?? "web:studio",
      ...(opts?.agent ? { agent: opts.agent } : {}),
    }),
  });
}

// ---------------------------------------------------------------------------
// Knowledge API (unified — LangChain + Milvus Lite engine)
// ---------------------------------------------------------------------------

// Current agent context — set by the app when agent is selected
let _knowledgeAgentId = "default";
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
