/*
 *  Copyright IBM Corp. 2025
 *
 *  This source code is licensed under the Apache-2.0 license found in the
 *  LICENSE file in the root directory of this source tree.
 *
 *  @license
 */

/** The events layer's HTTP surface — every `/api/events/*`, `/api/concierge` and `/invoke` call.
 *
 * Split out of CUGA's central `api.ts` so the events UI is one directory. These 30 exports were
 * 237 lines inside a client that otherwise knows nothing about triggers, mailboxes or the Studio;
 * a reader of `api.ts` should not have to skip past them, and a repo split should not have to
 * untangle them.
 *
 * The seam is deliberate and one-directional: this imports from the core client — auth handling,
 * 401 behaviour and origin routing stay in ONE place — and the core client re-exports these names
 * so existing callers keep working unchanged.
 *
 * `getEventsBaseUrl`/`eventsBaseUrlSync` deliberately do NOT live here despite the name. They are
 * routing, they mutate a cache `apiFetch` owns, and splitting them from that cache is exactly the
 * bug that silently disabled every events call.
 */
import { apiFetch, eventsBaseUrlSync } from "../api";

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
// Returns null unless the events service actually answers — callers use that to hide the Studio
// entry point entirely. Robust against the SPA catch-all: with no events service reachable,
// /api/events/status is unrouted and CUGA serves index.html with HTTP 200. We therefore must NOT
// trust res.ok alone — we require a JSON content-type AND an explicit enabled:true, or the Studio
// would leak into vanilla CUGA.
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
// them internally. Source: events/examples/rosters/default.yaml.
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
  // The EVENTS origin, not CUGA's. This used to build on getApiBaseUrl(), so in a split deployment
  // "Connect" opened cuga-core — which serves no /api/events/*, so the SPA catch-all swallowed the
  // request and the OAuth consent simply never appeared. apiFetch already routes events paths this
  // way; this is the one link that did not.
  return `${eventsBaseUrlSync()}/api/events/connect/${encodeURIComponent(app)}${own}`;
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
