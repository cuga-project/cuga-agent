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
  InlineLoading,
  InlineNotification,
} from "@carbon/react";
import { Chat, Plug, Application, Flow, Idea, Launch, User, Settings, Bot } from "@carbon/icons-react";
import * as api from "./api";
import { CugaHeader } from "./CugaHeader";
import { ConciergeChat } from "./ConciergeChat";
import "./StudioPage.css";

// ---- small dumb render helpers ------------------------------------------------
const STATUS_TAG: Record<string, { type: string; label: string }> = {
  connected: { type: "green", label: "connected" },
  not_connected: { type: "gray", label: "not connected" },
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
function AgentsTab({ refresh }: { refresh: number }) {
  const { data, loading, error } = useEndpoint<any[]>(api.getEventsAgents, (d) => d.agents ?? [], refresh);
  return (
    <div>
      <Loader loading={loading} error={error} />
      {!loading && !error && (data?.length ?? 0) === 0 && (
        <InlineNotification kind="info" lowContrast hideCloseButton
          title="No agents yet"
          subtitle="A builder sets up agents (skill + tools + connectors); the concierge routes among them." />
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
                <Tag key={i.app} type="purple" size="sm">{i.app} ({i.ownership})</Tag>
              ))}
              {a.restricted && (
                <Tag type={a.can_use ? "green" : "red"} size="sm">
                  {a.can_use ? "restricted · you can use" : "restricted"}
                </Tag>
              )}
            </div>
          </Tile>
        ))}
      </div>
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
            {!c.live && <Tag type="warm-gray" size="sm">Phase 3</Tag>}
          </div>
        </Tile>
      ))}
    </div>
  );
}

function IntegrationsTab({ refresh }: { refresh: number }) {
  const { data, loading, error } = useEndpoint<any[]>(api.getEventsIntegrations, (d) => d.integrations ?? [], refresh);

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
          <div className="studio-card-foot">
            <Tag type="outline" size="sm">{i.auth === "oauth" ? "OAuth" : "token"}</Tag>
            {i.status !== "ap_not_configured" && (
              <Button kind="tertiary" size="sm" renderIcon={i.auth === "oauth" ? Launch : Plug}
                onClick={() => connect(i)}>
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
          {(g.creds || []).length === 0
            ? <p className="studio-muted" style={{ fontSize: 13 }}>No credentials needed.</p>
            : (g.creds || []).map((c: any) => (
              <div key={c.key} style={{ fontSize: 13, margin: "3px 0" }}>
                <Tag type={c.present ? "green" : (c.required ? "red" : "gray")} size="sm">
                  {c.present ? "set" : (c.required ? "missing" : "optional")}</Tag>
                <code>{c.key}</code> — {c.label} <span className="studio-muted">({c.where})</span>
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
          {(g.connect === "oauth" || g.connect === "token") && (
            <Button kind="tertiary" size="sm" renderIcon={g.connect === "oauth" ? Launch : Plug}
              onClick={() => connect(g)}>Connect {g.label}</Button>
          )}
        </Tile>
      ))}
    </div>
  );
}

function FlowsTab({ refresh }: { refresh: number }) {
  const { data, loading, error } = useEndpoint<any[]>(api.getEventsSubscriptions, (d) => d.subscriptions ?? [], refresh);
  return (
    <div>
      <Loader loading={loading} error={error} />
      {!loading && !error && (data?.length ?? 0) === 0 && (
        <InlineNotification kind="info" lowContrast hideCloseButton
          title="No armed flows yet"
          subtitle="Ask the concierge to watch or schedule something — it appears here." />
      )}
      <div className="studio-grid">
        {data?.map((s) => (
          <Tile key={s.id} className="studio-card">
            <div className="studio-card-head">
              <span className="studio-card-title"><Flow size={18} /> {s.target_agent}</span>
              <Tag type={(MODE_TAG[s.mode] as any) ?? "gray"} size="sm">{s.mode}</Tag>
            </div>
            <p className="studio-muted">{s.prompt || `${s.source_type}/${s.source_connector}`}</p>
            <div className="studio-card-foot">
              <Tag type="outline" size="sm">{s.backend}</Tag>
              <Tag type={s.status === "active" ? "green" : "gray"} size="sm">{s.status}</Tag>
              {s.deliver_to?.length > 0 && <Tag type="blue" size="sm">→ {s.deliver_to.join(", ")}</Tag>}
            </div>
          </Tile>
        ))}
      </div>
    </div>
  );
}

function ExamplesTab({ refresh, onTry }: { refresh: number; onTry: (utterance: string) => void }) {
  const { data, loading, error } = useEndpoint<any[]>(api.getEventsExamples, (d) => d.examples ?? [], refresh);
  return (
    <div className="studio-grid">
      <Loader loading={loading} error={error} />
      {data?.map((e) => (
        <Tile key={e.id} className="studio-card">
          <div className="studio-card-head">
            <span className="studio-card-title"><Idea size={18} /> {e.title}</span>
            <Tag type={(OUTCOME_TAG[e.outcome] as any) ?? "gray"} size="sm">{e.outcome}</Tag>
          </div>
          <p className="studio-example-utterance">"{e.utterance}"</p>
          <p className="studio-muted">agent: {e.agent} — {e.note}</p>
          <div className="studio-card-foot">
            <Button kind="tertiary" size="sm" onClick={() => onTry(e.utterance)}>
              Try it
            </Button>
          </div>
        </Tile>
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
      {(data?.connections ?? []).length === 0
        ? <p className="studio-muted">None yet — connect from the Integrations tab.</p>
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
      <OAuthAppsPanel refresh={refresh} />
    </div>
  );
}

function OAuthAppsPanel({ refresh }: { refresh: number }) {
  const { data, loading, error } = useEndpoint<any[]>(api.getEventsAdminOAuthApps, (d) => d.apps ?? [], refresh);
  const setApp = (app: string) => {
    const cid = window.prompt(`${app} — Client ID:`);
    if (!cid) return;
    const sec = window.prompt(`${app} — Client Secret:`);
    if (!sec) return;
    api.postEventsAdminOAuthApp({ app, client_id: cid, client_secret: sec })
      .then((r) => r.json()).then((res) => alert(res.ok ? `${app} OAuth app saved.` : `Failed: ${res.error}`));
  };
  return (
    <div style={{ marginTop: "1.5rem" }}>
      <h4 style={{ margin: "0 0 0.5rem" }}>OAuth apps (client id/secret — no .env editing)</h4>
      <Loader loading={loading} error={error} />
      <div className="studio-grid">
        {data?.map((a) => (
          <Tile key={a.app} className="studio-card">
            <div className="studio-card-head">
              <span className="studio-card-title"><Plug size={18} /> {a.app}</span>
              <StatusTag status={a.configured ? "connected" : "not_configured"} />
            </div>
            <div className="studio-card-foot">
              <Button kind="tertiary" size="sm" onClick={() => setApp(a.app)}>
                {a.configured ? "Update" : "Set client id/secret"}
              </Button>
            </div>
          </Tile>
        ))}
      </div>
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
            <Tab renderIcon={Idea}>Examples</Tab>
            <Tab renderIcon={User}>Profile</Tab>
            <Tab renderIcon={Settings}>Admin</Tab>
          </TabList>
          <TabPanels>
            <TabPanel><ConciergeChat draft={draft} setDraft={setDraft} /></TabPanel>
            <TabPanel><AgentsTab refresh={refresh} /></TabPanel>
            <TabPanel><ChannelsTab refresh={refresh} /></TabPanel>
            <TabPanel><IntegrationsTab refresh={refresh} /></TabPanel>
            <TabPanel><SetupTab refresh={refresh} /></TabPanel>
            <TabPanel><FlowsTab refresh={refresh} /></TabPanel>
            <TabPanel><ExamplesTab refresh={refresh} onTry={onTry} /></TabPanel>
            <TabPanel><ProfileTab refresh={refresh} /></TabPanel>
            <TabPanel><AdminTab refresh={refresh} /></TabPanel>
          </TabPanels>
        </Tabs>
      </div>
    </div>
  );
}
