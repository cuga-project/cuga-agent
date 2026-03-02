import React, { useState, useEffect } from "react";
import {
  ComposedModal,
  ModalHeader,
  ModalBody,
  ModalFooter,
  Button,
  TextInput,
  FormGroup,
  Stack,
  Tag,
  Tabs,
  Tab,
  TabList,
  TabPanels,
  TabPanel,
  InlineNotification,
  InlineLoading,
} from "@carbon/react";
import { Add, TrashCan, Copy, Edit } from "@carbon/icons-react";
import * as api from "./api";

interface SecretMeta {
  id: string;
  agent_id?: string;
  created_by?: string;
  description?: string;
  created_at?: string;
  updated_at?: string;
  source?: "env" | "vault" | "db";
}

interface SecretsConfig {
  mode: string;
  force_env: boolean;
}

interface SecretsManagerProps {
  open: boolean;
  onClose: () => void;
  agentId?: string;
}

function sourceTag(source?: string, mode?: string) {
  if (source === "vault") return <Tag type="purple" size="sm">vault</Tag>;
  if (source === "env") return <Tag type="gray" size="sm">env</Tag>;
  if (source === "db") return <Tag type="green" size="sm">db</Tag>;
  if (mode === "vault") return <Tag type="purple" size="sm">vault</Tag>;
  return <Tag type="green" size="sm">db</Tag>;
}

function refForSecret(item: SecretMeta, mode?: string): string {
  if (item.source === "vault" || mode === "vault") return `vault://secret/${item.id}#value`;
  if (item.source === "env") return item.id.toUpperCase().replace(/-/g, "_");
  return `db://${item.id}`;
}

export function SecretsManager({ open, onClose, agentId }: SecretsManagerProps) {
  const [allSecrets, setAllSecrets] = useState<SecretMeta[]>([]);
  const [secretsConfig, setSecretsConfig] = useState<SecretsConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [addId, setAddId] = useState("");
  const [addValue, setAddValue] = useState("");
  const [addDescription, setAddDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  const load = async () => {
    if (!open) return;
    setLoading(true);
    setError(null);
    try {
      const [secretsRes, configRes] = await Promise.all([
        api.getSecrets(agentId),
        api.getSecretsConfig(),
      ]);
      if (secretsRes.ok) {
        const data = await secretsRes.json();
        setAllSecrets(data.secrets || data.overrides || []);
      }
      if (configRes.ok) {
        const cfg = await configRes.json();
        setSecretsConfig(cfg);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [open, agentId]);

  const mode = secretsConfig?.mode ?? "local";

  // Environment tab: env-sourced items (local/force_env mode only)
  const envSecrets = allSecrets.filter((s) => s.source === "env");
  // Overrides tab: db + vault items (user-managed)
  const storedSecrets = allSecrets.filter((s) => s.source !== "env");

  const handleAdd = async () => {
    const id = addId.trim().toLowerCase().replace(/\s+/g, "-");
    if (!id || !addValue.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const res = await api.createSecret(id, addValue.trim(), addDescription.trim() || undefined, undefined, agentId);
      if (res.ok) {
        setAddId("");
        setAddValue("");
        setAddDescription("");
        load();
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || data.error || "Failed to create");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!window.confirm(`Delete secret "${id}"?`)) return;
    setError(null);
    try {
      const res = await api.deleteSecret(id);
      if (res.ok) load();
      else {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || data.error || "Failed to delete");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete");
    }
  };

  const handleSaveEdit = async (id: string) => {
    if (!editValue.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const res = await api.updateSecret(id, editValue.trim());
      if (res.ok) {
        setEditingId(null);
        setEditValue("");
        load();
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || data.error || "Failed to update");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update");
    } finally {
      setSaving(false);
    }
  };

  const copyRef = (item: SecretMeta) => {
    navigator.clipboard.writeText(refForSecret(item, mode)).catch(() => {});
  };

  const rowStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: "0.75rem",
    padding: "0.5rem 0",
    borderBottom: "1px solid var(--cds-border-subtle)",
    flexWrap: "wrap",
  };

  const renderRow = (s: SecretMeta) => (
    <div key={s.id}>
      <div style={rowStyle}>
        <span style={{ fontWeight: 600, fontFamily: "monospace" }}>{s.id}</span>
        {sourceTag(s.source, mode)}
        {s.agent_id && s.agent_id !== "*" && <Tag type="blue" size="sm">{s.agent_id}</Tag>}
        {s.description && (
          <span className="cds--type-helper-text-01" style={{ color: "var(--cds-text-secondary)" }}>
            {s.description}
          </span>
        )}
        {s.source !== "env" && (
          <Button kind="ghost" size="sm" hasIconOnly iconDescription="Edit" renderIcon={Edit}
            onClick={() => { setEditingId(s.id); setEditValue(""); }} />
        )}
        <Button kind="ghost" size="sm" hasIconOnly iconDescription="Copy reference" renderIcon={Copy}
          onClick={() => copyRef(s)} />
        {s.source !== "env" && (
          <Button kind="ghost" size="sm" hasIconOnly iconDescription="Delete" renderIcon={TrashCan}
            onClick={() => handleDelete(s.id)} />
        )}
      </div>
      {editingId === s.id && (
        <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
          <TextInput
            id={`edit-${s.id}`}
            labelText=""
            type="password"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            placeholder="New value"
            autoComplete="off"
          />
          <Button size="sm" onClick={() => handleSaveEdit(s.id)} disabled={saving || !editValue.trim()}>Save</Button>
          <Button kind="ghost" size="sm" onClick={() => { setEditingId(null); setEditValue(""); }}>Cancel</Button>
        </div>
      )}
    </div>
  );

  const initialTab = mode === "vault" || storedSecrets.length > 0 ? 1 : 0;

  return (
    <ComposedModal open={open} onClose={onClose} size="lg">
      <ModalHeader title="Secrets Manager" buttonOnClick={onClose} />
      <ModalBody hasScrollingContent>
        {error && (
          <InlineNotification kind="error" title="Error" subtitle={error} lowContrast
            style={{ marginBottom: "1rem" }} onCloseButtonClick={() => setError(null)} />
        )}
        {secretsConfig?.force_env && (
          <InlineNotification kind="warning" title="force_env is enabled"
            subtitle="All secret resolution uses environment variables only."
            lowContrast style={{ marginBottom: "1rem" }} />
        )}
        {loading ? (
          <InlineLoading description="Loading secrets…" />
        ) : (
          <Tabs defaultSelectedIndex={initialTab}>
            <TabList aria-label="Secrets tabs">
              <Tab>Environment {envSecrets.length > 0 && `(${envSecrets.length})`}</Tab>
              <Tab>
                {mode === "vault" ? "Vault" : "Overrides"}{" "}
                {storedSecrets.length > 0 && `(${storedSecrets.length})`}
              </Tab>
            </TabList>
            <TabPanels>
              {/* Environment tab */}
              <TabPanel>
                <p className="cds--type-helper-text-01" style={{ margin: "1rem 0", color: "var(--cds-text-secondary)" }}>
                  Environment variables detected at startup. Available as references in local mode.
                </p>
                {envSecrets.length === 0 ? (
                  <p className="cds--type-body-compact-01" style={{ color: "var(--cds-text-placeholder)" }}>
                    {mode === "vault"
                      ? "Environment variable list is not shown in vault mode."
                      : "No known LLM environment variables detected."}
                  </p>
                ) : (
                  <Stack gap={0}>{envSecrets.map(renderRow)}</Stack>
                )}
              </TabPanel>

              {/* Overrides / Vault tab */}
              <TabPanel>
                <FormGroup legendText="Add secret" style={{ marginBottom: "1.5rem" }}>
                  <Stack gap={3}>
                    <TextInput id="secret-id" labelText="Name (id)" value={addId}
                      onChange={(e) => setAddId(e.target.value)} placeholder="e.g. my-openai-key" />
                    <TextInput id="secret-value" type="password" labelText="Value" value={addValue}
                      onChange={(e) => setAddValue(e.target.value)} placeholder="Secret value" autoComplete="off" />
                    <TextInput id="secret-description" labelText="Description (optional)" value={addDescription}
                      onChange={(e) => setAddDescription(e.target.value)} placeholder="Optional description" />
                    <Button kind="secondary" size="sm" renderIcon={Add} onClick={handleAdd}
                      disabled={saving || !addId.trim() || !addValue.trim()}>
                      {saving ? "Saving…" : "Add"}
                    </Button>
                  </Stack>
                </FormGroup>
                <FormGroup legendText={mode === "vault" ? "Vault secrets" : "Stored overrides"}>
                  {storedSecrets.length === 0 ? (
                    <p className="cds--type-body-compact-01" style={{ color: "var(--cds-text-placeholder)" }}>
                      No secrets stored yet.
                    </p>
                  ) : (
                    <Stack gap={0}>{storedSecrets.map(renderRow)}</Stack>
                  )}
                </FormGroup>
              </TabPanel>
            </TabPanels>
          </Tabs>
        )}
      </ModalBody>
      <ModalFooter>
        <Button kind="secondary" onClick={onClose}>Close</Button>
      </ModalFooter>
    </ComposedModal>
  );
}
