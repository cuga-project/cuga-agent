import { ContentSkeleton, AsyncButton, useModalClose } from "agentic_chat/LoadingState";
import React, { useState, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Heading,
  Tile,
  Search,
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
  Flow,
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
  const closeModal = useModalClose(saving, onClose);

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
    <ComposedModal aria-label="Create agent" open onClose={closeModal} size="sm" preventCloseOnClickOutside>
      <ModalHeader title="Create agent" />
      <ModalBody>
        {error && (
          <InlineNotification kind="error" title="Error" subtitle={error} lowContrast hideCloseButton style={{ marginBottom: "1rem" }} />
        )}
        <TextInput
          id="create-agent-name"
          disabled={saving}
          labelText="Name"
          placeholder="e.g. Flight Booker"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ marginBottom: "1rem" }}
        />
        <TextArea
          id="create-agent-description"
          disabled={saving}
          labelText="Description"
          placeholder="Optional"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          style={{ marginBottom: "1rem" }}
        />
        <RadioButtonGroup
          legendText="Kind"
          name="create-agent-kind" disabled={saving}
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
        <AsyncButton kind="primary" busy={saving} busyLabel="Creating…" onClick={handleCreate} disabled={!name.trim()}>Create</AsyncButton>
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
  const [agentRegistry, setAgentRegistry] = useState(false);
  const [query, setQuery] = useState("");
  const [pendingDelete, setPendingDelete] = useState<AgentItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const closeDeleteModal = useModalClose(deleting, () => setPendingDelete(null));
  const filteredAgents = agents.filter((agent) =>
    `${agent.name ?? ""} ${agent.id} ${agent.description}`.toLowerCase().includes(query.toLowerCase())
  );
  const navigate = useNavigate();
  const agentGroups = [
    {
      id: "supervisors",
      title: "Supervisors",
      description: "Coordinate other agents to complete tasks.",
      agents: filteredAgents.filter((agent) => agent.kind === "supervisor"),
    },
    {
      id: "agents",
      title: "Individual agents",
      description: "Agents with their own tools and instructions.",
      agents: filteredAgents.filter((agent) => agent.kind !== "supervisor"),
    },
  ].filter((group) => group.agents.length > 0);

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

  const handleDelete = async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      const res = await api.deleteAgent(pendingDelete.id);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Failed to delete agent");
      }
      setPendingDelete(null);
      reloadAgents();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Failed to delete agent");
    } finally {
      setDeleting(false);
    }
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

  useEffect(() => {
    api.getUiConfig()
      .then((c) => setAgentRegistry(!!c.agent_registry))
      .catch(() => setAgentRegistry(false));
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
    <div className="manage-dashboard-page">
      <CugaHeader
        title="CUGA Agent"
        agentContext={agentContext ?? undefined}
        linkComponent={Link as any}
      />

      <div role="main" id="main-content" tabIndex={-1} className="manage-dashboard-content">
        <div className="manage-dashboard-page-header">
        <p className="manage-dashboard-eyebrow">Workspace</p>
        <div className="manage-dashboard-title-row">
          <Heading className="manage-dashboard-heading">Agent dashboard</Heading>
          {agentRegistry && (
            <Button kind="primary" renderIcon={Add} onClick={() => setShowCreateModal(true)}>
              Create agent
            </Button>
          )}
        </div>
        <p className="manage-dashboard-description">Select an agent to configure it and try it out.</p>
        </div>
        <div className="manage-dashboard-toolbar">
          <Search id="agent-search" labelText="Search agents" placeholder="Search agents" size="lg"
            value={query} onChange={(event) => setQuery(event.target.value)} />
          <span className="manage-dashboard-count" aria-live="polite">{loading ? "" : `${filteredAgents.length} of ${agents.length} agents`}</span>
        </div>

        {loading && <ContentSkeleton label="Loading agents" kind="cards" />}

        {error && (
          <InlineNotification
            kind="error"
            title="Error"
            subtitle={error}
            lowContrast
          />
        )}

        {!loading && !error && agents.length > 0 && (
          <div className="manage-dashboard-groups">
            {agentGroups.map((group) => (
              <section key={group.id} aria-labelledby={`manage-${group.id}-heading`}>
                <div className="manage-dashboard-group-heading">
                  <h2 id={`manage-${group.id}-heading`}>{group.title}</h2>
                  <Tag type="gray" size="sm">{group.agents.length}</Tag>
                </div>
                <p className="manage-dashboard-group-description">{group.description}</p>
                <div className="manage-dashboard-list">
                  {group.agents.map((agent: AgentItem) => (
                    <Tile key={agent.id} className="manage-dashboard-card">
                      <div className="manage-dashboard-card-top">
                        {agent.kind === "supervisor" ? <Flow size={24} aria-hidden="true" /> : <Bot size={24} aria-hidden="true" />}
                        <Tag type={agent.kind === "supervisor" ? "purple" : "gray"} size="sm">
                          {agent.kind === "supervisor" ? "Supervisor" : "Single agent"}
                        </Tag>
                      </div>
                      <h3 className="manage-dashboard-card-name">{agent.name?.trim() || "Agent"}</h3>
                      <p className="manage-dashboard-card-id">{agent.id}</p>
                      <p className="manage-dashboard-card-description">{agent.description}</p>
                      <div className="manage-dashboard-card-meta">
                        <span><Tools size={16} /> {agent.tools_count} tool{agent.tools_count !== 1 ? "s" : ""}</span>
                        {agent.latest_version != null && <span title={agent.latest_version_created_at ? new Date(agent.latest_version_created_at).toLocaleString() : undefined}>
                          <DocumentMultiple_01 size={16} /> v{agent.latest_version}
                        </span>}
                      </div>
                      <div className="manage-dashboard-card-actions">
                        <Button kind="tertiary" size="sm" renderIcon={Settings}
                          href={`/manage/${encodeURIComponent(agent.id)}`}>
                          Configure & try it out
                        </Button>
                        <Button kind="ghost" size="sm" renderIcon={Chat}
                          href={`/chat/${encodeURIComponent(agent.id)}`}>
                          Chat
                        </Button>
                        {agentRegistry && agent.id !== "cuga-default" && <IconButton
                          label={`Delete agent ${agent.name?.trim() || agent.id}`} kind="ghost" size="sm"
                          onClick={() => { setDeleteError(null); setPendingDelete(agent); }}>
                          <TrashCan />
                        </IconButton>}
                      </div>
                    </Tile>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}

        {!loading && !error && agents.length > 0 && filteredAgents.length === 0 && (
          <Tile className="manage-dashboard-empty">
            <h2>No matching agents</h2>
            <p>Try a different name or description.</p>
            <Button kind="ghost" onClick={() => setQuery("")}>Clear search</Button>
          </Tile>
        )}

        {!loading && !error && agents.length === 0 && (
          <div>
            <InlineNotification
              kind="info"
              title="No agents configured"
              subtitle={agentRegistry ? "Create an agent to get started" : "No agents configured"}
              lowContrast
              hideCloseButton
            />
            {agentRegistry && (
              <Button kind="primary" renderIcon={Add} onClick={() => setShowCreateModal(true)} style={{ marginTop: "1rem" }}>
                Create agent
              </Button>
            )}
          </div>
        )}
      </div>

      <ComposedModal aria-label="Delete agent" open={!!pendingDelete} size="sm" preventCloseOnClickOutside
        onClose={closeDeleteModal}>
        <ModalHeader label="Agent dashboard" title={`Delete agent "${pendingDelete?.name?.trim() || pendingDelete?.id || ""}"?`} />
        <ModalBody>
          <p>This cannot be undone.</p>
          {deleteError && <InlineNotification kind="error" title="Error" subtitle={deleteError} lowContrast hideCloseButton />}
        </ModalBody>
        <ModalFooter>
          <Button kind="secondary" disabled={deleting} onClick={() => setPendingDelete(null)}>Cancel</Button>
          <AsyncButton kind="danger" busy={deleting} busyLabel="Deleting…" onClick={handleDelete}>Delete agent</AsyncButton>
        </ModalFooter>
      </ComposedModal>
      {agentRegistry && showCreateModal && (
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
