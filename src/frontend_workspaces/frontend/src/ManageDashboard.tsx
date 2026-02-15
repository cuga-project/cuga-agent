import React, { useState, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bot, Wrench, ExternalLink, Settings, FileStack } from "lucide-react";
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
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch("/api/agents")
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
  }, []);

  return (
    <div className="manage-dashboard-page">
      <header className="manage-dashboard-header">
        <h1>Agents</h1>
        <Link to="/">← Back to chat</Link>
      </header>

      <div className="manage-dashboard-content">
        <h2 className="manage-dashboard-title">Agent dashboard</h2>
        <p className="manage-dashboard-subtitle">
          Select an agent to configure it and try it out.
        </p>

        {loading && (
          <div className="manage-dashboard-loading">Loading agents…</div>
        )}

        {error && (
          <div className="manage-dashboard-error">{error}</div>
        )}

        {!loading && !error && agents.length > 0 && (
          <div className="manage-dashboard-list">
            {agents.map((agent) => (
              <div
                key={agent.id}
                className="manage-dashboard-card"
                onClick={() => navigate(`/manage/${encodeURIComponent(agent.id)}`)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    navigate(`/manage/${encodeURIComponent(agent.id)}`);
                  }
                }}
                role="button"
                tabIndex={0}
              >
                <div className="manage-dashboard-card-top">
                  <span className="manage-dashboard-card-id">
                    <Bot size={18} style={{ verticalAlign: "middle", marginRight: 8 }} />
                    {agent.id}
                  </span>
                  <div className="manage-dashboard-card-meta">
                    <span className="manage-dashboard-tools-badge">
                      <Wrench size={12} style={{ verticalAlign: "middle", marginRight: 4 }} />
                      {agent.tools_count} tool{agent.tools_count !== 1 ? "s" : ""}
                    </span>
                    {agent.latest_version != null && (
                      <span className="manage-dashboard-version-badge" title={agent.latest_version_created_at ? new Date(agent.latest_version_created_at).toLocaleString() : undefined}>
                        <FileStack size={12} style={{ verticalAlign: "middle", marginRight: 4 }} />
                        v{agent.latest_version}
                      </span>
                    )}
                  </div>
                </div>
                {agent.description && (
                  <p className="manage-dashboard-card-desc">{agent.description}</p>
                )}
                <div className="manage-dashboard-card-actions">
                  <span
                    className="manage-dashboard-card-btn"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      navigate(`/manage/${encodeURIComponent(agent.id)}`);
                    }}
                  >
                    <Settings size={14} />
                    Configure & try it out
                  </span>
                  <a
                    href={agent.logs_url ?? "#"}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="manage-dashboard-card-link manage-dashboard-logs-link"
                    onClick={(e) => e.stopPropagation()}
                    title={agent.logs_url ? "Open logs in Loki" : "Set CUGA_LOKI_LOGS_URL or LOKI_URL for your Loki dashboard"}
                  >
                    <ExternalLink size={14} />
                    Logs (Loki)
                  </a>
                </div>
              </div>
            ))}
          </div>
        )}

        {!loading && !error && agents.length === 0 && (
          <div className="manage-dashboard-loading">No agents configured.</div>
        )}
      </div>
    </div>
  );
}
