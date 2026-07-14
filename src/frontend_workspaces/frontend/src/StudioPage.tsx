import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Tabs,
  TabList,
  Tab,
  TabPanels,
  TabPanel,
  Tile,
  Tag,
  Button,
  CopyButton,
  InlineLoading,
  InlineNotification,
  Modal,
  TextInput,
  TextArea,
  Select,
  SelectItem,
  Checkbox,
  Table,
  TableHead,
  TableRow,
  TableHeader,
  TableBody,
  TableCell,
} from "@carbon/react";
import { Chat, Plug, Application, Flow, Idea, Launch, User, Settings, Bot, Add, Edit, View, Pause, Play, TrashCan, Activity } from "@carbon/icons-react";
import * as api from "./api";
import { CugaHeader } from "./CugaHeader";
import { ConciergeChat } from "./ConciergeChat";
import "./StudioPage.css";

// ---- small dumb render helpers ------------------------------------------------
const STATUS_TAG: Record<string, { type: string; label: string }> = {
  connected: { type: "green", label: "connected" },
  not_connected: { type: "gray", label: "not connected" },
  auto_connect_pending: { type: "cyan", label: "auto-connect pending" },
  not_configured: { type: "gray", label: "not configured" },
  ap_not_configured: { type: "gray", label: "AP not configured" },
  unknown: { type: "cool-gray", label: "unknown" },
};
function StatusTag({ status }: { status: string }) {
  const s = STATUS_TAG[status] ?? { type: "gray", label: status };
  return <Tag type={s.type as any} size="sm">{s.label}</Tag>;
}

const MODE_TAG: Record<string, string> = {
  NOW: "blue", CRON: "purple", POLL: "teal", PUSH: "magenta",
};

const RUN_STATUS_TAG: Record<string, string> = {
  SUCCEEDED: "green", FAILED: "red", RUNNING: "blue",
  PAUSED: "gray", STOPPED: "gray", TIMEOUT: "red", INTERNAL_ERROR: "red",
};

function fmtTime(s?: string): string {
  if (!s) return "—";
  try { return new Date(s).toLocaleString(); } catch { return s; }
}

// subscription.created_at is epoch SECONDS (a float); AP timestamps are ISO strings — hence two helpers.
function fmtEpoch(secs?: number): string {
  if (!secs) return "—";
  try { return new Date(secs * 1000).toLocaleString(); } catch { return String(secs); }
}

// router outcomes shown on Examples
const OUTCOME_TAG: Record<string, string> = {
  "answer-now": "blue", "flow-cron": "purple", "flow-poll": "teal",
  connect: "cyan", decline: "gray",
};

// ---- data hook (dumb fetch → render) -----------------------------------------
function useEndpoint<T>(fn: () => Promise<Response>, pick: (d: any) => T, dep: number) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(null);
    fn()
      .then((r) => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then((d) => { if (!cancelled) setData(pick(d)); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dep]);
  return { data, loading, error };
}

function Loader({ loading, error }: { loading: boolean; error: string | null }) {
  if (loading) return <InlineLoading description="Loading…" />;
  if (error) return <InlineNotification kind="error" title="Error" subtitle={error} lowContrast hideCloseButton />;
  return null;
}

// ---- tabs --------------------------------------------------------------------
const KNOWN_CHANNELS = ["web", "telegram", "slack", "discord"];
// Fallback only — the editor derives the live list (and each app's triggers) from
// /api/events/triggers, the backend registry, so the UI can never drift from the code.
const KNOWN_INTEGRATIONS = ["box", "discord", "github", "gmail", "slack", "telegram", "webhook"];
// Fallback tool catalog so the picker is NEVER empty (used if /api/events/mcp-servers is
// unreachable, e.g. an older running server). Enriched with live hints when the fetch succeeds.
const FALLBACK_MCP = [
  { name: "cuga-web", hint: "web search / browse / weather / wiki" },
  { name: "cuga-finance", hint: "stock quote / crypto price" },
  { name: "cuga-knowledge", hint: "search arXiv (recent papers)" },
  { name: "cuga-geo", hint: "country capital / population / region" },
  { name: "cuga-text", hint: "summarize / translate / text utilities" },
  { name: "cuga-code", hint: "explain / analyze code" },
  { name: "cuga-local", hint: "local / system operations" },
];

// The Add/Edit agent form. A builder defines an agent = skill (prompt) + tools (mcp_servers) +
// the connectors it may use (channels to converse on, integrations to watch/act on). Saving
// upserts by name (POST for new, PUT for existing).
function AgentEditor({ open, editing, onClose }: {
  open: boolean; editing: any | null; onClose: (saved: boolean) => void;
}) {
  const isEdit = !!editing;
  const [name, setName] = useState("");
  const [backend, setBackend] = useState("cuga");
  const [prompt, setPrompt] = useState("");
  const [mcp, setMcp] = useState<string[]>([]);
  const [channels, setChannels] = useState<string[]>(["web"]);
  const [integrations, setIntegrations] = useState<Record<string, string>>({});
  // trigger-grain: app → the trigger events this agent handles ([] = ALL of the app's triggers).
  // This used to be dropped on save, silently widening an agent to every trigger of the app.
  const [trigs, setTrigs] = useState<Record<string, string[]>>({});
  // the registry (app → its triggers) from /api/events/triggers; drives the picker below
  const [registry, setRegistry] = useState<Record<string, { event: string; title: string }[]>>({});
  const [access, setAccess] = useState("");
  const [servers, setServers] = useState<{ name: string; hint: string }[]>([]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setErr(null); setSaving(false);
    setName(editing?.name ?? "");
    setBackend(editing?.backend || "cuga");
    setPrompt(editing?.prompt ?? "");
    setMcp(editing?.mcp_servers ?? []);
    setChannels(editing?.channels?.length ? editing.channels : ["web"]);
    const im: Record<string, string> = {};
    const tm: Record<string, string[]> = {};
    (editing?.integrations ?? []).forEach((i: any) => {
      im[i.app] = i.ownership || "per-user";
      tm[i.app] = Array.isArray(i.triggers) ? i.triggers : [];
    });
    setIntegrations(im);
    setTrigs(tm);
    setAccess((editing?.access ?? []).join(", "));
    setServers(FALLBACK_MCP);   // always have a catalog; enrich from the server if reachable
    api.getEventsMcpServers().then((r) => r.json())
      .then((d) => { if (d.servers?.length) setServers(d.servers); })
      .catch(() => {});
    api.getEventsTriggers().then((r) => r.json())
      .then((d) => {
        const reg: Record<string, { event: string; title: string }[]> = {};
        (d.apps ?? []).forEach((a: any) => { reg[a.app] = a.triggers ?? []; });
        setRegistry(reg);
      })
      .catch(() => {});
  }, [open, editing]);

  const toggle = (list: string[], set: (v: string[]) => void, v: string) =>
    set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  const save = () => {
    setSaving(true); setErr(null);
    const spec: api.AgentSpecBody = {
      name: name.trim(), backend, prompt,
      mcp_servers: mcp, channels,
      integrations: Object.entries(integrations).map(([app, ownership]) => ({
        app, ownership, ...(trigs[app]?.length ? { triggers: trigs[app] } : {}),
      })),
      access: access.split(",").map((s) => s.trim()).filter(Boolean),
    };
    const req = isEdit ? api.putEventsAgent(editing.name, spec) : api.postEventsAgent(spec);
    req.then((r) => r.json()).then((res) => {
      if (res.ok) { onClose(true); }
      else { setErr(res.error || "save failed"); setSaving(false); }
    }).catch((e) => { setErr(String(e)); setSaving(false); });
  };

  return (
    <Modal open={open} modalHeading={isEdit ? `Edit agent · ${editing?.name}` : "Add agent"}
      primaryButtonText={saving ? "Saving…" : "Save"} secondaryButtonText="Cancel"
      primaryButtonDisabled={saving || !name.trim()}
      onRequestClose={() => onClose(false)} onRequestSubmit={save}>
      {err && <InlineNotification kind="error" title="Error" subtitle={err} lowContrast hideCloseButton />}
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <TextInput id="agent-name" labelText="Name" value={name} disabled={isEdit}
          placeholder="e.g. support_digest" onChange={(e) => setName(e.target.value)} />
        <Select id="agent-backend" labelText="Backend" value={backend}
          onChange={(e) => setBackend(e.target.value)}>
          <SelectItem value="cuga" text="cuga — full CUGA worker (tools + web)" />
          <SelectItem value="react" text="react — lightweight ReAct agent" />
        </Select>
        <TextArea id="agent-prompt" labelText="Skill (prompt)" rows={4} value={prompt}
          placeholder="What this agent does and how it should answer."
          onChange={(e) => setPrompt(e.target.value)} />
        <div>
          <div className="cds--label">Tools (MCP servers)</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "2px 16px" }}>
            {servers.map((s) => (
              <Checkbox key={s.name} id={`mcp-${s.name}`} checked={mcp.includes(s.name)}
                labelText={`${s.name}${s.hint ? " — " + s.hint : ""}`}
                onChange={() => toggle(mcp, setMcp, s.name)} />
            ))}
          </div>
        </div>
        <div>
          <div className="cds--label">Channels (converse on)</div>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
            {KNOWN_CHANNELS.map((ch) => (
              <Checkbox key={ch} id={`ch-${ch}`} labelText={ch} checked={channels.includes(ch)}
                onChange={() => toggle(channels, setChannels, ch)} />
            ))}
          </div>
        </div>
        <div>
          <div className="cds--label">Integrations (watch / act on)</div>
          {(Object.keys(registry).length ? Object.keys(registry).sort() : KNOWN_INTEGRATIONS).map((app) => (
            <div key={app} style={{ marginBottom: 4 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <Checkbox id={`int-${app}`} labelText={app} checked={app in integrations}
                  onChange={(_: any, { checked }: any) => setIntegrations((prev) => {
                    const next = { ...prev };
                    if (checked) next[app] = next[app] || "per-user"; else delete next[app];
                    return next;
                  })} />
                {app in integrations && (
                  <Select id={`own-${app}`} labelText="" size="sm" value={integrations[app]}
                    inline onChange={(e) => setIntegrations((prev) => ({ ...prev, [app]: e.target.value }))}>
                    <SelectItem value="per-user" text="per-user (each user logs in)" />
                    <SelectItem value="shared" text="shared (one service account)" />
                  </Select>
                )}
              </div>
              {/* trigger-grain: pick WHICH of the app's triggers this agent handles. Nothing
                  selected = all of them (the registry default). */}
              {app in integrations && (registry[app]?.length ?? 0) > 0 && (
                <div style={{ margin: "2px 0 6px 28px" }}>
                  <div className="studio-muted" style={{ fontSize: 12, marginBottom: 2 }}>
                    Triggers — {trigs[app]?.length
                      ? `${trigs[app].length} of ${registry[app].length} selected`
                      : `all ${registry[app].length} (none selected = all)`}
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 16px" }}>
                    {registry[app].map((t) => (
                      <Checkbox key={t.event} id={`trg-${app}-${t.event}`}
                        labelText={`${t.event} — ${t.title}`}
                        checked={(trigs[app] ?? []).includes(t.event)}
                        onChange={() => setTrigs((prev) => {
                          const cur = prev[app] ?? [];
                          const next = cur.includes(t.event)
                            ? cur.filter((e) => e !== t.event) : [...cur, t.event];
                          return { ...prev, [app]: next };
                        })} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
        <TextInput id="agent-access" labelText="Access (roles / user-ids, comma-separated · blank = everyone)"
          value={access} placeholder="e.g. builder, admin" onChange={(e) => setAccess(e.target.value)} />
      </div>
    </Modal>
  );
}

function AgentsTab({ refresh, onTry }: { refresh: number; onTry: (u: string) => void }) {
  const [localBump, setLocalBump] = useState(0);
  const { data, loading, error } = useEndpoint<any[]>(
    api.getEventsAgents, (d) => d.agents ?? [], refresh + localBump);
  const mcp = useEndpoint<any>(api.getEventsMcpServers, (d) => d, refresh);
  const explorerUrl = mcp.data?.explorer_url || "http://localhost:8001/docs";
  const mcpServers: { name: string }[] = mcp.data?.servers || [];
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<any | null>(null);
  const openAdd = () => { setEditing(null); setEditorOpen(true); };
  const openEdit = (a: any) => { setEditing(a); setEditorOpen(true); };
  const onClose = (saved: boolean) => { setEditorOpen(false); if (saved) setLocalBump((n) => n + 1); };
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <p className="studio-muted" style={{ margin: 0 }}>
          A builder defines agents (skill + tools + connectors); the concierge routes among them.
        </p>
        <Button size="sm" renderIcon={Add} onClick={openAdd}>Add agent</Button>
      </div>
      {/* pointer to the MCP tool explorer — where the tools these agents use come from */}
      <div className="studio-muted" style={{
        display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 16,
        fontSize: 13, padding: "8px 12px",
        border: "1px solid var(--cds-border-subtle, #e0e0e0)", borderRadius: 6,
      }}>
        <Plug size={16} />
        <span>Agents draw on <b>MCP tool servers</b> — browse every server's tools (and try them) in the</span>
        <Button kind="ghost" size="sm" renderIcon={Launch} href={explorerUrl} target="_blank">MCP tool explorer</Button>
        {mcpServers.length > 0 && (
          <span style={{ opacity: 0.85 }}>· {mcpServers.map((s) => s.name).join(", ")}</span>
        )}
      </div>
      <Loader loading={loading} error={error} />
      {!loading && !error && (data?.length ?? 0) === 0 && (
        <InlineNotification kind="info" lowContrast hideCloseButton
          title="No agents yet"
          subtitle="Click “Add agent” to define one — a skill (prompt) plus the tools and connectors it may use." />
      )}
      <div className="studio-grid">
        {data?.map((a) => (
          <Tile key={a.name} className="studio-card">
            <div className="studio-card-head">
              <span className="studio-card-title"><Bot size={18} /> {a.name}</span>
              <Tag type={(a.backend === "cuga" ? "blue" : "cool-gray") as any} size="sm">{a.backend}</Tag>
            </div>
            <p className="studio-muted">{a.prompt || "—"}</p>
            <div className="studio-card-foot">
              {a.mcp_servers?.map((m: string) => (
                <Tag key={m} type="teal" size="sm">{m}</Tag>
              ))}
              {a.channels?.map((c: string) => (
                <Tag key={c} type="outline" size="sm">{c}</Tag>
              ))}
              {a.integrations?.map((i: any) => (
                <Tag key={i.app} type="purple" size="sm"
                  title={i.triggers?.length ? i.triggers.join(", ") : "all triggers"}>
                  {i.app} ({i.ownership}){i.triggers?.length ? ` · ${i.triggers.length} trigger${i.triggers.length > 1 ? "s" : ""}` : ""}
                </Tag>
              ))}
              {a.restricted && (
                <Tag type={a.can_use ? "green" : "red"} size="sm">
                  {a.can_use ? "restricted · you can use" : "restricted"}
                </Tag>
              )}
            </div>
            {(a.examples?.length ?? 0) > 0 && (
              <div style={{ marginTop: 10 }}>
                <div className="studio-muted" style={{ fontSize: 12, marginBottom: 4 }}>Try:</div>
                {a.examples.map((u: string) => (
                  <button key={u} className="studio-example-chip" title="Load into Concierge"
                    onClick={() => onTry(u)}>"{u}"</button>
                ))}
              </div>
            )}
            <div className="studio-card-foot">
              <Button kind="ghost" size="sm" renderIcon={Edit} onClick={() => openEdit(a)}>Edit</Button>
            </div>
          </Tile>
        ))}
      </div>
      <AgentEditor open={editorOpen} editing={editing} onClose={onClose} />
    </div>
  );
}

function ChannelsTab({ refresh }: { refresh: number }) {
  const { data, loading, error } = useEndpoint<any[]>(api.getEventsChannels, (d) => d.channels ?? [], refresh);
  return (
    <div className="studio-grid">
      <Loader loading={loading} error={error} />
      {data?.map((c) => (
        <Tile key={c.name} className="studio-card">
          <div className="studio-card-head">
            <span className="studio-card-title"><Chat size={18} /> {c.label}</span>
            <StatusTag status={c.status} />
          </div>
          <p className="studio-muted">{c.note}</p>
          <div className="studio-card-foot">
            <Tag type="outline" size="sm">converse</Tag>
            {c.backend && <Tag type={c.backend === "direct" ? "cyan" : "teal"} size="sm">
              {c.backend === "direct" ? "direct" : "Activepieces"}</Tag>}
            <Tag type="blue" size="sm">TENANT — shared bot</Tag>
          </div>
        </Tile>
      ))}
    </div>
  );
}

function IntegrationsTab({ refresh }: { refresh: number }) {
  const { data, loading, error } = useEndpoint<any[]>(api.getEventsIntegrations, (d) => d.integrations ?? [], refresh);
  // the trigger registry, so each card can say WHAT the integration can watch
  const reg = useEndpoint<any[]>(api.getEventsTriggers, (d) => d.apps ?? [], refresh);
  const trigsFor = (app: string) =>
    (reg.data ?? []).find((a: any) => a.app === app)?.triggers ?? [];

  // "log in with your own account" — OAuth apps open the consent flow; token apps paste a secret.
  const connect = (i: any) => {
    if (i.auth === "oauth") {
      window.open(api.eventsConnectUrl(i.name), "_blank", "noreferrer");
    } else {
      const token = window.prompt(`Paste your ${i.label} token:`);
      if (token) {
        api.postEventsConnectToken(i.name, token).then((r) => r.json()).then((res) => {
          alert(res.ok ? `${i.label} connected.` : `Failed: ${res.error || "error"}`);
        });
      }
    }
  };

  return (
    <div className="studio-grid">
      <Loader loading={loading} error={error} />
      {data?.map((i) => (
        <Tile key={i.name} className="studio-card">
          <div className="studio-card-head">
            <span className="studio-card-title"><Application size={18} /> {i.label}</span>
            <StatusTag status={i.status} />
          </div>
          <p className="studio-muted">{i.note}</p>
          {trigsFor(i.name).length > 0 && (
            <details style={{ marginBottom: 8 }}>
              <summary className="studio-muted" style={{ cursor: "pointer", fontSize: 12 }}>
                {trigsFor(i.name).length} trigger{trigsFor(i.name).length > 1 ? "s" : ""}
              </summary>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 6 }}>
                {trigsFor(i.name).map((t: any) => (
                  <Tag key={t.event} type={t.backend === "direct" ? "cyan" : "gray"} size="sm"
                    title={t.title}>{t.event}{t.default ? " ★" : ""}</Tag>
                ))}
              </div>
            </details>
          )}
          <div className="studio-card-foot">
            <Tag type={i.backend === "direct" ? "teal" : "blue"} size="sm">
              {i.backend === "direct" ? "direct backend" : "via Activepieces"}
            </Tag>
            <Tag type="outline" size="sm">
              {i.backend === "direct" ? "token" : (i.auth === "oauth" ? "OAuth" : "token")}
            </Tag>
            {i.status !== "ap_not_configured" && (
              <Button kind={i.status === "connected" ? "ghost" : "tertiary"} size="sm"
                renderIcon={i.auth === "oauth" ? Launch : Plug} onClick={() => connect(i)}>
                {i.status === "connected" ? "Reconnect" : "Connect"}
              </Button>
            )}
          </div>
        </Tile>
      ))}
    </div>
  );
}

// The connect SETUP GUIDE — per connector: required creds (+ present?), ownership (per-user vs
// tenant), and the concrete steps. Dumb: it renders the server's guides + drives the connect action.
function SetupTab({ refresh }: { refresh: number }) {
  const { data, loading, error } = useEndpoint<any[]>(api.getEventsSetupGuides, (d) => d.guides ?? [], refresh);
  const [own, setOwn] = useState<Record<string, string>>({});

  const connect = (g: any) => {
    const ownership = own[g.app] || g.ownership_default || (g.ownership || [])[0] || "per_user";
    if (g.connect === "oauth") {
      window.open(api.eventsConnectUrl(g.app, ownership), "_blank", "noreferrer");
    } else if (g.connect === "token") {
      const token = window.prompt(`Paste your ${g.label} token/secret:`);
      if (token) api.postEventsConnectToken(g.app, token, ownership).then((r) => r.json())
        .then((res) => alert(res.ok ? `${g.label} connected (${ownership}).` : `Failed: ${res.error || "error"}`));
    }
  };
  // TENANT-level OAuth app registration (client id/secret) — the org-wide credential that must exist
  // before per-user OAuth connect works. Consolidated here (was a separate Admin panel) so all
  // credential setup lives in ONE place.
  const setOAuthApp = (g: any) => {
    const cid = window.prompt(`${g.label} — OAuth app Client ID:`);
    if (!cid) return;
    const sec = window.prompt(`${g.label} — OAuth app Client Secret:`);
    if (!sec) return;
    api.postEventsAdminOAuthApp({ app: g.app, client_id: cid, client_secret: sec })
      .then((r) => r.json())
      .then((res) => alert(res.ok ? `${g.label} OAuth app saved (tenant).` : `Failed: ${res.error || "error"}`));
  };
  // Edit ANY connector credential (its .env variable) in place — persists to .env + applies live where
  // the value is read at use-time (Slack/Box/OAuth); flags a reload for the boot-time ones (Discord).
  const setCred = (c: any) => {
    const v = window.prompt(`Set ${c.key} — ${c.label}\n(value is written to .env)`, "");
    if (v == null || v === "") return;
    api.postEventsSetCredential(c.key, v).then((r) => r.json())
      .then((res) => alert(res.ok ? `${c.key} saved.\n${res.note}` : `Failed: ${res.error || "error"}`));
  };
  const pill = (label: string, on: boolean, click?: () => void) => (
    <span onClick={click} style={{ cursor: click ? "pointer" : "default", fontSize: 12, fontWeight: 600,
      padding: "2px 10px", borderRadius: 20, marginRight: 6,
      background: on ? "#0f62fe" : "#e0e0e0", color: on ? "#fff" : "#525252" }}>{label}</span>
  );

  return (
    <div>
      <Loader loading={loading} error={error} />
      <p className="studio-muted" style={{ marginBottom: 12 }}>How to connect each channel &amp; integration —
        required credentials, where to store them (<b>per-user</b> vs <b>per-tenant</b>), and the steps.</p>
      {data?.map((g) => (
        <Tile key={g.app} className="studio-card" style={{ marginBottom: 12 }}>
          <div className="studio-card-head">
            <span className="studio-card-title">{g.label}</span>
            <span>
              <Tag type={g.kind === "channel" ? "blue" : "teal"} size="sm">{g.kind}</Tag>
              <Tag type="outline" size="sm">{g.wiring}</Tag>
            </span>
          </div>
          {g.needs_connection && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "6px 0 10px" }}>
              <Tag type={g.connected ? "green" : "red"} size="md">
                {g.connected ? "● Connected" : "○ Not connected"}</Tag>
              <Tag type={g.connection_scope === "tenant" ? "blue" : "purple"} size="sm">
                {g.connection_scope === "tenant" ? "TENANT (one per org)" : "USER (per-person login)"}</Tag>
              {(g.connect === "oauth" || g.connect === "token") && (
                <Button kind={g.connected ? "ghost" : "tertiary"} size="sm"
                  renderIcon={g.connect === "oauth" ? Launch : Plug} onClick={() => connect(g)}>
                  {g.connected ? "Reconnect" : "Connect"} {g.label}</Button>
              )}
              {g.connect === "oauth" && g.connection_scope === "user" && (
                <Button kind="ghost" size="sm" renderIcon={Settings} onClick={() => setOAuthApp(g)}>
                  OAuth app creds (tenant)</Button>
              )}
            </div>
          )}
          {(g.creds || []).length === 0
            ? <p className="studio-muted" style={{ fontSize: 13 }}>No credentials needed.</p>
            : (g.creds || []).map((c: any) => (
              <div key={c.key} style={{ fontSize: 13, margin: "3px 0", display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
                <Tag type={c.present ? "green" : (c.required ? "red" : "gray")} size="sm">
                  {c.present ? "configured ✓" : (c.required ? "set up →" : "optional")}</Tag>
                <Tag type={c.scope === "tenant" ? "blue" : "purple"} size="sm">
                  {c.scope === "tenant" ? "TENANT" : "USER"}</Tag>
                <code>{c.key}</code> — {c.label} <span className="studio-muted">({c.where})</span>
                <Button kind="ghost" size="sm" renderIcon={Edit} onClick={() => setCred(c)}>
                  {c.present ? "Edit" : "Set"}</Button>
              </div>
            ))}
          {(g.ownership || []).length > 0 && (
            <div style={{ fontSize: 13, margin: "8px 0 4px" }}>
              <b>Store credential:</b>{" "}
              {(g.ownership || []).map((o: string) =>
                <React.Fragment key={o}>{pill(o === "per_user" ? "per-user" : "per-tenant",
                  (own[g.app] || g.ownership_default) === o,
                  (g.ownership || []).length > 1 ? () => setOwn({ ...own, [g.app]: o }) : undefined)}</React.Fragment>)}
            </div>
          )}
          <ol style={{ fontSize: 13, margin: "6px 0", paddingLeft: 18 }}>
            {(g.steps || []).map((s: string, i: number) => <li key={i} style={{ margin: "3px 0" }}>{s}</li>)}
          </ol>
          {g.note && <p className="studio-muted" style={{ fontSize: 12.5 }}>⚠ {g.note}</p>}
        </Tile>
      ))}
    </div>
  );
}

// Walk the AP flow's trigger→nextAction chain into a flat step list, then show the CUGA
// Source→Agent→Sink model + those AP steps. This is the "see it like AP" view, in-Studio.
function FlowDetail({ detail }: { detail: any }) {
  const s = detail?.subscription || {};
  const ap = detail?.ap_flow || null;
  const steps: any[] = [];
  let node = ap?.version?.trigger;
  while (node) {
    const set = node.settings || {};
    steps.push({ name: node.displayName || node.name, piece: set.pieceName,
      action: set.actionName || set.triggerName });
    node = node.nextAction;
  }
  return (
    <div>
      <p className="studio-muted" style={{ marginBottom: 10 }}>
        <strong>Source</strong> {s.source_connector || s.source_type} → <strong>Agent</strong>{" "}
        {s.target_agent} → <strong>Sink</strong> {(s.deliver_to || []).join(", ") || "web"}
      </p>
      <p style={{ margin: "0 0 8px" }}>
        <Tag type={(MODE_TAG[s.mode] as any) ?? "gray"} size="sm">{s.mode}</Tag>
        <Tag type={s.status === "paused" ? "gray" : "green"} size="sm">{s.status}</Tag>
        {s.flow_name && <Tag type="outline" size="sm">{s.flow_name}</Tag>}
      </p>
      <p className="studio-muted" style={{ fontSize: 13 }}>{s.prompt}</p>
      <h5 style={{ margin: "16px 0 6px" }}>Activepieces flow steps</h5>
      {ap ? (
        <ol style={{ paddingLeft: 18, margin: 0 }}>
          {steps.map((st, i) => (
            <li key={i} style={{ fontSize: 13, marginBottom: 4 }}>
              <strong>{st.name}</strong>{" "}
              {st.piece && <code>{String(st.piece).replace("@activepieces/piece-", "")}</code>}
              {st.action && <span className="studio-muted"> · {st.action}</span>}
            </li>
          ))}
        </ol>
      ) : <p className="studio-muted">No live AP flow (a direct/no-AP flow, or AP unreachable).</p>}
    </div>
  );
}

function FlowsTab({ refresh }: { refresh: number }) {
  const [tick, setTick] = useState(0);
  const { data, loading, error } = useEndpoint<any[]>(
    api.getEventsSubscriptions, (d) => d.subscriptions ?? [], refresh + tick);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [detail, setDetail] = useState<any | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const act = async (id: string, fn: (id: string) => Promise<Response>) => {
    setBusy(id); setActionError(null);
    try {
      const res = await fn(id);
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.error || res.statusText);
      setTick((t) => t + 1);
    } catch (e) { setActionError(e instanceof Error ? e.message : "action failed"); }
    finally { setBusy(null); }
  };
  const del = async (id: string, name: string) => {
    if (!window.confirm(`Delete flow "${name}"? This removes it from Activepieces too.`)) return;
    await act(id, api.deleteFlow);
  };
  const view = async (id: string) => {
    setDetailLoading(true); setDetail({ id }); setActionError(null);
    try {
      const res = await api.getEventsFlowDetail(id);
      const d = await res.json();
      if (!res.ok) throw new Error(d?.error || res.statusText);
      setDetail(d);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "could not load flow"); setDetail(null);
    } finally { setDetailLoading(false); }
  };

  return (
    <div>
      <Loader loading={loading} error={error} />
      {actionError && <InlineNotification kind="error" lowContrast title="Error"
        subtitle={actionError} onCloseButtonClick={() => setActionError(null)} />}
      {!loading && !error && (data?.length ?? 0) === 0 && (
        <InlineNotification kind="info" lowContrast hideCloseButton
          title="No armed flows yet"
          subtitle="Ask the concierge to watch or schedule something — or type /automate <what>. It appears here." />
      )}
      <div className="studio-grid">
        {data?.map((s) => {
          const paused = s.status === "paused";
          return (
            <Tile key={s.id} className="studio-card">
              <div className="studio-card-head">
                <span className="studio-card-title"><Flow size={18} /> {s.target_agent}</span>
                <Tag type={(MODE_TAG[s.mode] as any) ?? "gray"} size="sm">{s.mode}</Tag>
              </div>
              {s.flow_name && <p className="studio-muted" style={{ fontSize: 12, margin: "0 0 4px" }}>
                flow <code>{s.flow_name}</code></p>}
              <p className="studio-muted">{s.prompt || `${s.source_type}/${s.source_connector}`}</p>
              <p className="studio-muted" style={{ fontSize: 11, margin: "4px 0 0" }}>
                {paused ? "paused" : "enabled"} · armed {fmtEpoch(s.created_at)}</p>
              <div className="studio-card-foot">
                <Tag type="outline" size="sm">{s.backend}</Tag>
                <Tag type={paused ? "gray" : "green"} size="sm">{s.status}</Tag>
                {s.deliver_to?.length > 0 && <Tag type="blue" size="sm">→ {s.deliver_to.join(", ")}</Tag>}
              </div>
              <div style={{ display: "flex", gap: 4, marginTop: 12, flexWrap: "wrap" }}>
                <Button size="sm" kind="ghost" renderIcon={View}
                  onClick={() => view(s.id)} disabled={busy === s.id}>View</Button>
                <Button size="sm" kind="ghost" renderIcon={paused ? Play : Pause}
                  onClick={() => act(s.id, paused ? api.resumeFlow : api.pauseFlow)}
                  disabled={busy === s.id}>{paused ? "Resume" : "Pause"}</Button>
                <Button size="sm" kind="danger--ghost" renderIcon={TrashCan}
                  onClick={() => del(s.id, s.flow_name || s.id)} disabled={busy === s.id}>Delete</Button>
              </div>
            </Tile>
          );
        })}
      </div>
      {detail && (
        <Modal open passiveModal
          modalHeading={`Flow — ${detail?.subscription?.flow_name || detail?.id || ""}`}
          onRequestClose={() => setDetail(null)}>
          {detailLoading ? <InlineLoading description="Loading flow…" />
            : detail?.subscription ? <FlowDetail detail={detail} />
            : <p className="studio-muted">No detail.</p>}
        </Modal>
      )}
    </div>
  );
}

// The execution log — every standing-flow run (cron/poll/push), filterable by agent / integration /
// channel / trigger / status, with the agent's output on demand.
function RunDetail({ detail }: { detail: any }) {
  if (detail?.error) {
    return <InlineNotification kind="error" lowContrast hideCloseButton title="Error"
      subtitle={String(detail.error)} />;
  }
  const run = detail?.run || {};
  const trig = detail?.trigger_payload;
  return (
    <div>
      <p style={{ margin: "0 0 6px", display: "flex", gap: 8, alignItems: "center" }}>
        <Tag type={(RUN_STATUS_TAG[run.status] as any) ?? "gray"} size="sm">{run.status}</Tag>
        <span className="studio-muted" style={{ fontSize: 12 }}>{fmtTime(run.started_at)}</span>
      </p>
      {detail?.utterance && <p className="studio-muted" style={{ fontSize: 12, margin: "0 0 8px" }}>
        Flow: <i>{detail.utterance}</i></p>}
      {detail?.error_msg && <InlineNotification kind="error" lowContrast hideCloseButton
        title="Flow error" subtitle={String(detail.error_msg)} />}
      <h5 style={{ margin: "8px 0 4px" }}>Agent output</h5>
      {detail?.answer
        ? <pre className="studio-msg-text" style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{detail.answer}</pre>
        : <p className="studio-muted">No answer captured (a failed run, or the flow doesn't return one).</p>}
      {trig != null && (
        <details style={{ marginTop: 10 }}>
          <summary className="studio-muted" style={{ cursor: "pointer", fontSize: 12 }}>Trigger payload</summary>
          <pre className="studio-msg-text" style={{ whiteSpace: "pre-wrap", fontSize: 12, maxHeight: 220, overflow: "auto" }}>
            {JSON.stringify(trig, null, 2).slice(0, 4000)}</pre>
        </details>
      )}
    </div>
  );
}

function RunsTab({ refresh }: { refresh: number }) {
  const [tick, setTick] = useState(0);
  const { data, loading, error } = useEndpoint<any[]>(
    api.getEventsRuns, (d) => d.runs ?? [], refresh + tick);
  const [f, setF] = useState<Record<string, string>>(
    { agent: "all", integration: "all", channel: "all", mode: "all", status: "all" });
  const [sort, setSort] = useState<{ col: string; dir: 1 | -1 }>({ col: "started_at", dir: -1 });
  const [detail, setDetail] = useState<any | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const runs = data ?? [];
  const uniq = (k: string) => Array.from(new Set(runs.map((r) => r[k]).filter(Boolean))).sort();
  const filtered = runs.filter((r) => ["agent", "integration", "channel", "mode", "status"]
    .every((k) => f[k] === "all" || String(r[k]) === f[k]));
  const sorted = [...filtered].sort((a, b) => {
    const av = a[sort.col] ?? "", bv = b[sort.col] ?? "";
    return (av < bv ? -1 : av > bv ? 1 : 0) * sort.dir;
  });

  const view = async (row: any) => {
    setDetailLoading(true); setDetail({ id: row.id, utterance: row.utterance });
    try {
      const res = await api.getEventsRunDetail(row.id);
      const d = await res.json();
      setDetail(res.ok ? { ...d, utterance: row.utterance, error_msg: d.error }
        : { error: d?.error || res.statusText });
    } catch (e) { setDetail({ error: e instanceof Error ? e.message : "failed" }); }
    finally { setDetailLoading(false); }
  };

  const th = (col: string, label: string) => (
    <TableHeader onClick={() => setSort((s) =>
      ({ col, dir: s.col === col ? (s.dir * -1) as 1 | -1 : 1 }))} style={{ cursor: "pointer" }}>
      {label}{sort.col === col ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
    </TableHeader>
  );
  const filterSel = (key: string, label: string) => (
    <Select id={`run-f-${key}`} labelText={label} size="sm"
      value={f[key]} onChange={(e: any) => setF((p) => ({ ...p, [key]: e.target.value }))}>
      <SelectItem value="all" text={`All`} />
      {uniq(key).map((v) => <SelectItem key={String(v)} value={String(v)} text={String(v)} />)}
    </Select>
  );

  return (
    <div>
      <Loader loading={loading} error={error} />
      {!loading && !error && (
        <>
          <p className="studio-muted" style={{ margin: "0 0 10px", fontSize: 13 }}>
            Every execution, newest first — <b>NOW</b> answers (Studio chat + channel DMs) and
            standing flows (cron / poll / push). Click a column to sort; filter with the dropdowns.
            <Button kind="ghost" size="sm"
              onClick={() => setTick((t) => t + 1)}>Refresh</Button>
          </p>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12, alignItems: "flex-end" }}>
            {filterSel("agent", "Agent")}
            {filterSel("integration", "Integration")}
            {filterSel("channel", "Channel")}
            {filterSel("mode", "Trigger")}
            {filterSel("status", "Status")}
          </div>
          {runs.length === 0 ? (
            <InlineNotification kind="info" lowContrast hideCloseButton title="No runs yet"
              subtitle="Chat with the Concierge or arm a flow — every answer and flow-fire shows up here with status and output." />
          ) : (
            <Table size="sm">
              <TableHead>
                <TableRow>
                  {th("started_at", "Time")}{th("agent", "Agent")}{th("utterance", "Flow (utterance)")}
                  {th("mode", "Trigger")}{th("integration", "Integration")}{th("channel", "Channel")}
                  {th("status", "Status")}<TableHeader>Output</TableHeader>
                </TableRow>
              </TableHead>
              <TableBody>
                {sorted.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>{fmtTime(r.started_at)}</TableCell>
                    <TableCell>{r.agent}</TableCell>
                    <TableCell title={r.utterance || ""} style={{ maxWidth: 260 }}>
                      {r.utterance ? (r.utterance.length > 52 ? r.utterance.slice(0, 52) + "…" : r.utterance) : "—"}</TableCell>
                    <TableCell><Tag type={(MODE_TAG[r.mode] as any) ?? "gray"} size="sm">{r.mode}</Tag></TableCell>
                    <TableCell>{r.integration}</TableCell>
                    <TableCell>{r.channel}</TableCell>
                    <TableCell><Tag type={(RUN_STATUS_TAG[r.status] as any) ?? "gray"} size="sm">{r.status}</Tag></TableCell>
                    <TableCell><Button kind="ghost" size="sm" renderIcon={View}
                      onClick={() => view(r)}>View</Button></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          <p className="studio-muted" style={{ fontSize: 12, marginTop: 8 }}>
            Showing {sorted.length} of {runs.length}. NOW = an immediate chat/DM answer; CRON/POLL/PUSH = a standing flow firing.
          </p>
        </>
      )}
      {detail && (
        <Modal open passiveModal modalHeading="Run output" onRequestClose={() => setDetail(null)}>
          {detailLoading ? <InlineLoading description="Loading…" /> : <RunDetail detail={detail} />}
        </Modal>
      )}
    </div>
  );
}

function ExamplesTab({ refresh, onTry }: { refresh: number; onTry: (utterance: string) => void }) {
  const { data, loading, error } = useEndpoint<any[]>(api.getEventsExamples, (d) => d.examples ?? [], refresh);
  const [starOnly, setStarOnly] = useState(false);
  const [q, setQ] = useState("");

  const query = q.trim().toLowerCase();
  const items = (data ?? []).filter((e) =>
    (!starOnly || e.star) &&
    (!query || `${e.title} ${e.utterance} ${e.agent} ${e.note}`.toLowerCase().includes(query)));

  // group by agent → a long, copy-pasteable list per agent
  const groups = new Map<string, any[]>();
  for (const e of items) {
    const k = e.agent || "—";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k)!.push(e);
  }
  // agents with a ⭐ example first, then alphabetical
  const agents = [...groups.keys()].sort((a, b) => {
    const sa = groups.get(a)!.some((e) => e.star) ? 0 : 1;
    const sb = groups.get(b)!.some((e) => e.star) ? 0 : 1;
    return sa - sb || a.localeCompare(b);
  });

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 16, margin: "0 0 14px", flexWrap: "wrap" }}>
        <p className="studio-muted" style={{ margin: 0, fontSize: 13, flex: "1 1 320px" }}>
          Prompts grouped by <b>agent</b>. <b>Copy</b> one to paste anywhere, or <b>Try it</b> to load it
          into the Concierge. <b>⭐ = recommended starter</b>.
        </p>
        <TextInput id="ex-search" labelText="" size="sm" placeholder="Filter examples…"
          value={q} onChange={(e: any) => setQ(e.target.value)} style={{ maxWidth: 220 }} />
        <Checkbox id="ex-star-only" labelText="⭐ Recommended only"
          checked={starOnly} onChange={(_e: any, d: any) => setStarOnly(!!d?.checked)} />
      </div>
      <Loader loading={loading} error={error} />
      {!loading && !error && agents.length === 0 && <p className="studio-muted">No examples match.</p>}
      {agents.map((agent) => (
        <div key={agent} style={{ marginBottom: 22 }}>
          <h4 style={{ margin: "0 0 6px", display: "flex", alignItems: "center", gap: 8 }}>
            <Bot size={18} /> {agent}
            <Tag type="cool-gray" size="sm">{groups.get(agent)!.length}</Tag>
          </h4>
          {groups.get(agent)!
            .sort((a, b) => (b.star ? 1 : 0) - (a.star ? 1 : 0))
            .map((e) => (
              <div key={e.id} style={{
                display: "flex", alignItems: "center", gap: 10, padding: "7px 0",
                borderBottom: "1px solid var(--cds-border-subtle, #e0e0e0)",
              }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <span className="studio-example-utterance" style={{ fontSize: 13 }}>
                    {e.star && <span title="recommended">⭐ </span>}"{e.utterance}"</span>
                  {e.note && <p className="studio-muted" style={{ margin: "2px 0 0", fontSize: 11 }}>{e.note}</p>}
                </div>
                <Tag type={(OUTCOME_TAG[e.outcome] as any) ?? "gray"} size="sm">{e.trigger || e.outcome}</Tag>
                <CopyButton iconDescription="Copy prompt" feedback="Copied!"
                  onClick={() => { try { navigator.clipboard.writeText(e.utterance); } catch { /* noop */ } }} />
                <Button kind="tertiary" size="sm" onClick={() => onTry(e.utterance)}>Try it</Button>
              </div>
            ))}
        </div>
      ))}
    </div>
  );
}

function ProfileTab({ refresh }: { refresh: number }) {
  const { data, loading, error } = useEndpoint<any>(api.getEventsMe, (d) => d, refresh);
  const link = (channel: string) => {
    api.postEventsLinkChannel(channel).then((r) => r.json()).then((res) => {
      alert(res.ok ? `To link ${channel}: ${res.how}` : `Failed: ${res.error || "error"}`);
    });
  };
  if (loading || error) return <Loader loading={loading} error={error} />;
  return (
    <div>
      <Tile className="studio-card" style={{ marginBottom: "1rem" }}>
        <div className="studio-card-head">
          <span className="studio-card-title">{data?.user_id}</span>
          {(data?.roles ?? []).map((r: string) => <Tag key={r} type="cyan" size="sm">{r}</Tag>)}
        </div>
        <p className="studio-muted">{data?.email} · scope <code>{data?.scope}</code></p>
      </Tile>
      <h4 style={{ margin: "1rem 0 0.5rem" }}>My channels</h4>
      <div className="studio-grid">
        {["telegram", "discord", "slack"].map((ch) => {
          const linked = (data?.linked_channels ?? []).some((l: any) => l.channel === ch);
          return (
            <Tile key={ch} className="studio-card">
              <div className="studio-card-head">
                <span className="studio-card-title"><Chat size={18} /> {ch}</span>
                <StatusTag status={linked ? "connected" : "not_connected"} />
              </div>
              <div className="studio-card-foot">
                <Button kind="tertiary" size="sm" onClick={() => link(ch)}>
                  {linked ? "Re-link" : "Link my account"}
                </Button>
              </div>
            </Tile>
          );
        })}
      </div>
      <h4 style={{ margin: "1.5rem 0 0.5rem" }}>My connected integrations</h4>
      <p className="studio-muted" style={{ fontSize: 13, marginTop: 0 }}>
        Read-only — this is who you are. To connect or reconnect an app, use the <b>Setup</b> tab.
      </p>
      {(data?.connections ?? []).length === 0
        ? <p className="studio-muted">None yet.</p>
        : (data.connections).map((c: any) => (
            <Tag key={c.externalId} type="green" size="sm">{c.externalId}</Tag>))}
    </div>
  );
}

function AdminTab({ refresh }: { refresh: number }) {
  const { data, loading, error } = useEndpoint<any[]>(api.getEventsAdminUsers, (d) => d.users ?? [], refresh);
  const add = () => {
    const id = window.prompt("New user id (e.g. carol):");
    if (!id) return;
    const email = window.prompt("Email:") || "";
    api.postEventsAdminUser({ user_id: id, email, roles: ["user"] })
      .then((r) => r.json()).then((res) => alert(res.ok ? `Added ${id}` : `Failed: ${res.error}`));
  };
  return (
    <div>
      <Loader loading={loading} error={error} />
      <p className="studio-muted" style={{ marginBottom: 12 }}>
        Users &amp; roles for this tenant. <b>Credentials &amp; app connections</b> (OAuth app client
        id/secret, tokens, connect/reconnect) all live in the <b>Setup</b> tab — one place.
      </p>
      <Button kind="tertiary" size="sm" onClick={add} style={{ marginBottom: "1rem" }}>Add user</Button>
      <div className="studio-grid">
        {data?.map((u) => (
          <Tile key={u.user_id} className="studio-card">
            <div className="studio-card-head">
              <span className="studio-card-title">{u.user_id}</span>
              {(u.roles ?? []).map((r: string) => <Tag key={r} type="gray" size="sm">{r}</Tag>)}
            </div>
            <p className="studio-muted">{u.email}</p>
          </Tile>
        ))}
      </div>
    </div>
  );
}

// The API reference, embedded — both the human-readable guide and the full OpenAPI spec, served by
// the backend (/api/events/docs/{api,spec,examples}) so they render inside the Studio.
function ApiTab() {
  const base = api.getApiBaseUrl();
  const pages: { key: string; label: string }[] = [
    { key: "api", label: "API guide" },
    { key: "spec", label: "OpenAPI spec" },
    { key: "examples", label: "Examples board" },
  ];
  const [page, setPage] = useState("api");
  const url = `${base}/api/events/docs/${page}`;
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
        <p className="studio-muted" style={{ margin: 0, fontSize: 13, flex: "1 1 240px" }}>
          The events API reference, embedded — the human-readable guide and the full OpenAPI spec.
        </p>
        {pages.map((p) => (
          <Button key={p.key} size="sm" kind={page === p.key ? "primary" : "tertiary"}
            onClick={() => setPage(p.key)}>{p.label}</Button>
        ))}
        <Button size="sm" kind="ghost" renderIcon={Launch} href={url} target="_blank">Open full page</Button>
      </div>
      <iframe title="API reference" src={url}
        style={{ width: "100%", height: "72vh", border: "1px solid var(--cds-border-subtle, #e0e0e0)",
                 borderRadius: 6, background: "#fff" }} />
    </div>
  );
}

// ---- page --------------------------------------------------------------------
export function StudioPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<api.EventsStatus | null>(null);
  const [checked, setChecked] = useState(false);
  const [selected, setSelected] = useState(0);
  const [draft, setDraft] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    api.getEventsStatus().then((s) => { setStatus(s); setChecked(true); });
  }, []);

  // refresh Flows/Integrations after a concierge action or manual refresh
  const bump = () => setRefresh((n) => n + 1);

  if (checked && !status) {
    // events layer not mounted → this route shouldn't be reachable; guide back.
    return (
      <div className="studio-page">
        <CugaHeader title="CUGA Agent" navItems={[{ label: "Chat", href: "/chat" }]} />
        <div className="studio-content">
          <InlineNotification kind="info" lowContrast hideCloseButton
            title="Studio is off"
            subtitle="The events layer is not enabled. Start CUGA with EVENTS_ENABLED=1 to use the Studio." />
          <Button kind="tertiary" onClick={() => navigate("/manage")} style={{ marginTop: 16 }}>
            Back to dashboard
          </Button>
        </div>
      </div>
    );
  }

  const onTry = (utterance: string) => { setDraft(utterance); setSelected(0); };

  return (
    <div className="studio-page">
      <CugaHeader
        title="CUGA Studio"
        prefix="Events"
        navItems={[
          { label: "Chat", href: "/chat" },
          { label: "Agents", href: "/manage" },
        ]}
      />
      <div className="studio-content">
        <div className="studio-heading-row">
          <div>
            <h2 className="studio-title">Event Studio</h2>
            <p className="studio-muted">
              Turn natural language into worker agents + triggers. Configuration &amp; visibility
              only — all decisions run server-side.
              {status && (
                <span className="studio-scope"> · scope <code>{status.scope}</code>
                  {" · workers "}<code>{status.worker_backend ?? "cuga"}</code>
                  {" · concierge "}<code>{status.concierge_backend ?? "react"}</code>
                  {" · AP "}{status.ap_configured ? "connected" : "off"}</span>
              )}
            </p>
          </div>
          <Button kind="ghost" size="sm" onClick={bump}>Refresh</Button>
        </div>

        <Tabs selectedIndex={selected} onChange={(e: { selectedIndex: number }) => setSelected(e.selectedIndex)}>
          <TabList aria-label="Studio sections" contained>
            <Tab renderIcon={Chat}>Concierge</Tab>
            <Tab renderIcon={Bot}>Agents</Tab>
            <Tab renderIcon={Chat}>Channels</Tab>
            <Tab renderIcon={Plug}>Integrations</Tab>
            <Tab renderIcon={Settings}>Setup</Tab>
            <Tab renderIcon={Flow}>Flows</Tab>
            <Tab renderIcon={Activity}>Runs</Tab>
            <Tab renderIcon={Idea}>Examples</Tab>
            <Tab renderIcon={Launch}>API</Tab>
            <Tab renderIcon={User}>Profile</Tab>
            <Tab renderIcon={Settings}>Admin</Tab>
          </TabList>
          <TabPanels>
            <TabPanel><ConciergeChat draft={draft} setDraft={setDraft} /></TabPanel>
            <TabPanel><AgentsTab refresh={refresh} onTry={onTry} /></TabPanel>
            <TabPanel><ChannelsTab refresh={refresh} /></TabPanel>
            <TabPanel><IntegrationsTab refresh={refresh} /></TabPanel>
            <TabPanel><SetupTab refresh={refresh} /></TabPanel>
            <TabPanel><FlowsTab refresh={refresh} /></TabPanel>
            <TabPanel><RunsTab refresh={refresh} /></TabPanel>
            <TabPanel><ExamplesTab refresh={refresh} onTry={onTry} /></TabPanel>
            <TabPanel><ApiTab /></TabPanel>
            <TabPanel><ProfileTab refresh={refresh} /></TabPanel>
            <TabPanel><AdminTab refresh={refresh} /></TabPanel>
          </TabPanels>
        </Tabs>
      </div>
    </div>
  );
}
