import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Heading,
  Link,
  ClickableTile,
  Tag,
  InlineLoading,
  InlineNotification,
  Button,
} from "@carbon/react";
import {
  Bot,
  Tools,
  Launch,
  Settings,
  DocumentMultiple_01,
} from "@carbon/icons-react";
import * as api from "./api";
import { handleOidcCallback } from "./auth";
import { CugaHeader } from "./CugaHeader";
import "./ManageDashboard.css";

export interface AgentItem {
  id: string;
  description: string;
  tools_count: number;
  logs_url: string | null;
  latest_version: number | null;
  latest_version_created_at: string | null;
}

export function ManageDashboard() {
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [agentContext, setAgentContext] = useState<{ agent_id: string; config_version: number | null } | null>(null);
  const [callbackPending, setCallbackPending] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");

    if (code && state) {
      setCallbackPending(true);
      handleOidcCallback(code, state)
        .catch((e) => setError(e instanceof Error ? e.message : "Auth callback failed"))
        .finally(() => setCallbackPending(false));
      return;
    }

    api.getAuthConfig().then((c) => {
      if (!c.enabled) return;
      const base = api.getApiBaseUrl();
      api.apiFetch(`${base}/auth/userinfo`).then((r) => {
        if (r.status === 401) {
          window.location.href = `${base}/auth/login`;
        }
      }).catch(() => {});
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (callbackPending) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.getAgents()
      .then((res) => {
        if (!res.ok) throw new Error(res.statusText);
        return res.json();
      })
      .then((data) => {
        if (!cancelled) setAgents(data.agents ?? []);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load agents");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [callbackPending]);

  useEffect(() => {
    if (callbackPending) return;
    api.getAgentContext()
      .then((res) => (res.ok ? res.json() : null))
      .then(
        (data) =>
          data &&
          setAgentContext({
            agent_id: data.agent_id ?? "cuga-default",
            config_version: data.config_version ?? null,
          })
      )
      .catch(() => {});
  }, [callbackPending]);

  return (
    <div className="manage-dashboard-page" style={{ width: "100%", display: "flex", flexDirection: "column", height: "100vh" }}>
      <CugaHeader
        title="CUGA Agent"
        agentContext={agentContext ?? undefined}
        navItems={[
          { label: "Chat", href: "/chat" },
        ]}
      />

      <div className="manage-dashboard-content" style={{ flex: 1, overflow: "auto", padding: "2rem 3rem", marginTop: "3rem", width: "100%" }}>
        <Heading style={{ marginBottom: "0.5rem" }}>Agent dashboard</Heading>
        <p style={{ marginBottom: "2rem", color: "#525252" }}>
          Select an agent to configure it and try it out.
        </p>

        {(loading || callbackPending) && (
          <InlineLoading description={callbackPending ? "Completing sign-in…" : "Loading agents…"} />
        )}

        {error && (
          <InlineNotification
            kind="error"
            title="Error"
            subtitle={error}
            lowContrast
          />
        )}

        {!loading && !error && agents.length > 0 && (
          <div
            className="manage-dashboard-list"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(400px, 1fr))",
              gap: "1.5rem",
              marginTop: "1rem"
            }}
          >
            {agents.map((agent: AgentItem) => (
              <ClickableTile
                key={agent.id}
                onClick={() => navigate(`/manage/${encodeURIComponent(agent.id)}`)}
                style={{ display: "flex", flexDirection: "column", padding: "1.5rem", minHeight: "200px" }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1.5rem" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontWeight: 600 }}>
                    <Bot size={20} />
                    {agent.id}
                  </div>
                  <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                    <Tag type="blue" size="sm">
                      <Tools size={12} style={{ marginRight: "0.25rem" }} />
                      {agent.tools_count} tool{agent.tools_count !== 1 ? "s" : ""}
                    </Tag>
                    {agent.latest_version != null && (
                      <Tag
                        type="gray"
                        size="sm"
                        title={agent.latest_version_created_at ? new Date(agent.latest_version_created_at).toLocaleString() : undefined}
                      >
                        <DocumentMultiple_01 size={12} style={{ marginRight: "0.25rem" }} />
                        v{agent.latest_version}
                      </Tag>
                    )}
                  </div>
                </div>
                {agent.description && (
                  <p style={{ marginBottom: "1.5rem", color: "#525252", flex: 1, lineHeight: "1.5" }}>{agent.description}</p>
                )}
                <div style={{ display: "flex", gap: "0.75rem", marginTop: "auto", flexWrap: "wrap" }}>
                  <Button
                    kind="tertiary"
                    size="sm"
                    renderIcon={Settings}
                    onClick={(e: React.MouseEvent) => {
                      e.preventDefault();
                      e.stopPropagation();
                      navigate(`/manage/${encodeURIComponent(agent.id)}`);
                    }}
                  >
                    Configure & try it out
                  </Button>
                  <Link
                    href={agent.logs_url ?? "#"}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e: React.MouseEvent) => e.stopPropagation()}
                    title={agent.logs_url ? "Open logs in Loki" : "Set CUGA_LOKI_LOGS_URL or LOKI_URL for your Loki dashboard"}
                    style={{ display: "flex", alignItems: "center", gap: "0.25rem" }}
                  >
                    <Launch size={16} />
                    Logs (Loki)
                  </Link>
                </div>
              </ClickableTile>
            ))}
          </div>
        )}

        {!loading && !error && agents.length === 0 && (
          <InlineNotification
            kind="info"
            title="No agents configured"
            subtitle="Create an agent to get started"
            lowContrast
            hideCloseButton
          />
        )}
      </div>
    </div>
  );
}
