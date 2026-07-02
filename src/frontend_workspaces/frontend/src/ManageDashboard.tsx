import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Heading,
  ClickableTile,
  Tag,
  InlineLoading,
  InlineNotification,
  Button,
  ComposedModal,
  ModalHeader,
  ModalBody,
  ModalFooter,
  TextInput,
  TextArea,
  RadioButtonGroup,
  RadioButton,
  IconButton,
} from "@carbon/react";
import {
  Bot,
  Tools,
  Settings,
  DocumentMultiple_01,
  Add,
  TrashCan,
  Chat,
} from "@carbon/icons-react";
import * as api from "./api";
import { CugaHeader } from "./CugaHeader";
import "./ManageDashboard.css";

export interface AgentItem {
  id: string;
  name?: string;
  description: string;
  kind: "single" | "supervisor";
  tools_count: number;
  latest_version: number | null;
  latest_version_created_at: string | null;
}

function CreateAgentModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [kind, setKind] = useState<"single" | "supervisor">("single");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCreate = async () => {
    if (!name.trim()) {
      setError("Name is required");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await api.createAgent(name.trim(), description.trim(), kind);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || res.statusText);
      }
      const data = await res.json();
      onCreated(data.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create agent");
    } finally {
      setSaving(false);
    }
  };

  return (
    <ComposedModal open onClose={onClose} size="sm">
      <ModalHeader title="Create agent" />
      <ModalBody>
        {error && (
          <InlineNotification kind="error" title="Error" subtitle={error} lowContrast hideCloseButton style={{ marginBottom: "1rem" }} />
        )}
        <TextInput
          id="create-agent-name"
          labelText="Name"
          placeholder="e.g. Flight Booker"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ marginBottom: "1rem" }}
        />
        <TextArea
          id="create-agent-description"
          labelText="Description"
          placeholder="Optional"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          style={{ marginBottom: "1rem" }}
        />
        <RadioButtonGroup
          legendText="Kind"
          name="create-agent-kind"
          valueSelected={kind}
          onChange={(v) => setKind(v as "single" | "supervisor")}
        >
          <RadioButton labelText="Single agent" value="single" id="create-agent-kind-single" />
          <RadioButton labelText="Supervisor (orchestrates other agents)" value="supervisor" id="create-agent-kind-supervisor" />
        </RadioButtonGroup>
      </ModalBody>
      <ModalFooter>
        <Button kind="secondary" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button kind="primary" onClick={handleCreate} disabled={saving}>
          {saving ? "Creating…" : "Create"}
        </Button>
      </ModalFooter>
    </ComposedModal>
  );
}

export function ManageDashboard() {
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [agentContext, setAgentContext] = useState<{ agent_id: string; config_version: number | null } | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const navigate = useNavigate();

  const reloadAgents = () => {
    setLoading(true);
    setError(null);
    api.getAgents()
      .then((res) => {
        if (!res.ok) throw new Error(res.statusText);
        return res.json();
      })
      .then((data) => setAgents(data.agents ?? []))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load agents"))
      .finally(() => setLoading(false));
  };

  const handleDelete = async (agent: AgentItem, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!window.confirm(`Delete agent "${agent.name?.trim() || agent.id}"? This cannot be undone.`)) return;
    const res = await api.deleteAgent(agent.id);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.detail || "Failed to delete agent");
      return;
    }
    reloadAgents();
  };

  useEffect(() => {
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

  useEffect(reloadAgents, []);

  useEffect(() => {
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
  }, []);

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
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.5rem" }}>
          <Heading>Agent dashboard</Heading>
          <Button kind="primary" renderIcon={Add} onClick={() => setShowCreateModal(true)}>
            Create agent
          </Button>
        </div>
        <p style={{ marginBottom: "2rem", color: "#525252" }}>
          Select an agent to configure it and try it out.
        </p>

        {loading && <InlineLoading description="Loading agents…" />}

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
                    {agent.name?.trim() || "Agent"}
                  </div>
                  <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                    {agent.kind === "supervisor" && (
                      <Tag type="purple" size="sm">
                        Supervisor
                      </Tag>
                    )}
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
                  <Button
                    kind="ghost"
                    size="sm"
                    renderIcon={Chat}
                    onClick={(e: React.MouseEvent) => {
                      e.preventDefault();
                      e.stopPropagation();
                      navigate(`/chat/${encodeURIComponent(agent.id)}`);
                    }}
                  >
                    Chat
                  </Button>
                  {agent.id !== "cuga-default" && (
                    <IconButton
                      label="Delete agent"
                      kind="ghost"
                      size="sm"
                      onClick={(e: React.MouseEvent) => handleDelete(agent, e)}
                    >
                      <TrashCan />
                    </IconButton>
                  )}
                </div>
              </ClickableTile>
            ))}
          </div>
        )}

        {!loading && !error && agents.length === 0 && (
          <div>
            <InlineNotification
              kind="info"
              title="No agents configured"
              subtitle="Create an agent to get started"
              lowContrast
              hideCloseButton
            />
            <Button kind="primary" renderIcon={Add} onClick={() => setShowCreateModal(true)} style={{ marginTop: "1rem" }}>
              Create agent
            </Button>
          </div>
        )}
      </div>

      {showCreateModal && (
        <CreateAgentModal
          onClose={() => setShowCreateModal(false)}
          onCreated={(id) => {
            setShowCreateModal(false);
            navigate(`/manage/${encodeURIComponent(id)}`);
          }}
        />
      )}
    </div>
  );
}
