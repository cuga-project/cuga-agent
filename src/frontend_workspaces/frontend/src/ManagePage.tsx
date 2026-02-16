import React, { useState, useEffect, useCallback, useRef } from "react";
import { Link, useParams, useLocation } from "react-router-dom";
import {
  Button,
  TextInput,
  FormGroup,
  Checkbox,
  NumberInput,
  Tag,
  ComposedModal,
  ModalHeader,
  ModalBody,
} from "@carbon/react";
import {
  Save,
  Time as HistoryIcon,
  Key as KeyIcon,
  Flag as FlagIcon,
  Security as ShieldIcon,
  Document as DocumentIcon,
  Close,
  Upload,
} from "@carbon/icons-react";
import { CustomChat } from "agentic_chat/CustomChat";
import PoliciesConfig from "agentic_chat/PoliciesConfig";
import VariablesSidebar from "agentic_chat/VariablesSidebar";
import { ToolsConfig, type ConnectedApp, type ConnectedTool } from "./ToolsConfig";
import type { ToolEntry } from "./types/tools";
import "./ManagePage.css";

export type { ToolEntry } from "./types/tools";

export interface AgentConfig {
  llm?: { api_key?: string; base_url?: string; model?: string; temperature?: number };
  tools?: ToolEntry[];
  feature_flags?: {
    enable_todos?: boolean;
    reflection?: boolean;
    max_steps?: number;
  };
  policies?: { enablePolicies: boolean; policies: unknown[] };
}

export interface ConfigVersion {
  version: number;
  created_at: string;
}

const DEFAULT_CONFIG: AgentConfig = {
  llm: { api_key: "", base_url: "", model: "", temperature: 0.7 },
  tools: [],
  feature_flags: { enable_todos: true, reflection: false, max_steps: 20 },
};

const POLICY_TYPE_LABELS: Record<string, string> = {
  intent_guard: "Intent guards",
  playbook: "Playbooks",
  tool_guide: "Tool guides",
  tool_approval: "Tool approval",
  output_formatter: "Output formatters",
};

function policiesSummary(policies: unknown[]): { total: number; byType: Record<string, number> } {
  const byType: Record<string, number> = {};
  for (const p of policies) {
    const t = (p as { policy_type?: string }).policy_type ?? "other";
    byType[t] = (byType[t] ?? 0) + 1;
  }
  return { total: policies.length, byType };
}

function maskSecrets(obj: unknown): unknown {
  if (obj === null || obj === undefined) return obj;
  if (Array.isArray(obj)) return obj.map(maskSecrets);
  if (typeof obj === "object") {
    const o = obj as Record<string, unknown>;
    const isAuth = "type" in o && typeof o.type === "string";
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(obj)) {
      const lower = k.toLowerCase();
      const shouldMask =
        lower === "api_key" ||
        (isAuth && (lower === "value" || lower === "key"));
      out[k] = shouldMask && typeof v === "string" && v.length > 0 ? "••••••••" : maskSecrets(v);
    }
    return out;
  }
  return obj;
}

export function ManagePage() {
  const { agentId } = useParams<{ agentId: string }>();
  const location = useLocation();
  const search = location.search || "";
  const [config, setConfig] = useState<AgentConfig>(DEFAULT_CONFIG);
  const [history, setHistory] = useState<ConfigVersion[]>([]);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "success" | "error">("idle");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showPoliciesModal, setShowPoliciesModal] = useState(false);
  const [viewVersion, setViewVersion] = useState<{ version: number; config: AgentConfig } | null>(null);
  const [connectedApps, setConnectedApps] = useState<ConnectedApp[]>([]);
  const [connectedTools, setConnectedTools] = useState<ConnectedTool[]>([]);
  const [importStatus, setImportStatus] = useState<"idle" | "ok" | "error">("idle");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [manageVariables, setManageVariables] = useState<Record<string, any>>({});
  const [manageVariablesHistory, setManageVariablesHistory] = useState<Array<{ id: string; title: string; timestamp: number; variables: Record<string, any> }>>([]);
  const [manageSelectedAnswerId, setManageSelectedAnswerId] = useState<string | null>(null);
  const [manageVariablesPanelOpen, setManageVariablesPanelOpen] = useState(false);
  const [currentVersion, setCurrentVersion] = useState<number | "draft" | null>(null);
  const skipDraftSaveRef = useRef(true);
  const draftSaveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleManageVariablesUpdate = useCallback((variables: Record<string, any>, history: Array<any>) => {
    setManageVariables(variables);
    setManageVariablesHistory(
      (history ?? []).map((h: any) => ({
        id: h.id ?? String(h.timestamp ?? Math.random()),
        title: h.title ?? "Turn",
        timestamp: h.timestamp ?? 0,
        variables: h.variables ?? {},
      }))
    );
    if (history?.length && !manageSelectedAnswerId) setManageSelectedAnswerId(history[0]?.id ?? null);
  }, [manageSelectedAnswerId]);

  const normalizeTools = useCallback((raw: unknown[]): ToolEntry[] => {
    return (raw ?? []).map((t: Record<string, unknown>) => {
      const type = (t.type as string) === "openapi" ? "openapi" : "mcp";
      let auth = t.auth as ToolEntry["auth"] | string | undefined;
      if (typeof auth === "string" && auth) {
        auth = { type: "bearer", value: auth };
      }
      const entry: ToolEntry = {
        name: (t.name as string) ?? type,
        type,
        url: (t.url as string) || undefined,
        description: t.description as string | undefined,
        auth,
      };
      if (Array.isArray(t.include) && t.include.length > 0) {
        entry.include = t.include as string[];
      }
      if (t.command != null && String(t.command).trim()) {
        entry.command = String(t.command).trim();
        entry.args = Array.isArray(t.args) ? (t.args as string[]) : [];
        entry.transport = (t.transport as ToolEntry["transport"]) || "stdio";
      } else if (type === "mcp" && entry.url) {
        entry.transport = (t.transport as ToolEntry["transport"]) || "sse";
      }
      return entry;
    });
  }, []);

  const loadLatest = useCallback(async () => {
    try {
      skipDraftSaveRef.current = true;
      const [draftRes, policiesRes, toolsListRes] = await Promise.all([
        fetch("/api/manage/config?draft=1"),
        fetch("/api/config/policies"),
        fetch("/api/tools/list"),
      ]);
      const out = { ...DEFAULT_CONFIG };
      let version: number | "draft" | null = null;
      if (draftRes.ok) {
        const data = await draftRes.json();
        if (data.version === "draft" || (data.config && Object.keys(data.config).length > 0)) {
          if (data.config) {
            Object.assign(out, data.config);
            if (Array.isArray(out.tools)) {
              out.tools = normalizeTools(out.tools);
            }
          }
          version = data.version === "draft" ? "draft" : (data.version ?? null);
        }
      }
      if (version === null) {
        const publishedRes = await fetch("/api/manage/config");
        if (publishedRes.ok) {
          const data = await publishedRes.json();
          if (data.config && Object.keys(data.config).length > 0) {
            Object.assign(out, data.config);
            if (Array.isArray(out.tools)) {
              out.tools = normalizeTools(out.tools);
            }
          }
          version = typeof data.version === "number" ? data.version : null;
        }
      }
      if (policiesRes.ok) {
        const policiesData = await policiesRes.json();
        out.policies = { enablePolicies: policiesData.enablePolicies ?? true, policies: policiesData.policies ?? [] };
      }
      if (toolsListRes.ok) {
        const toolsData = await toolsListRes.json();
        setConnectedApps(toolsData.apps ?? []);
        setConnectedTools(
          (toolsData.tools ?? []).map((t: ConnectedTool & { id?: string }) => ({
            ...t,
            id: t.id ?? t.name,
          }))
        );
      } else {
        setConnectedApps([]);
        setConnectedTools([]);
      }
      setConfig(out);
      setCurrentVersion(version);
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Failed to load config");
    }
  }, [normalizeTools]);

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch("/api/manage/config/history");
      if (res.ok) {
        const data = await res.json();
        setHistory(data.versions || []);
      }
    } catch {
      setHistory([]);
    }
  }, []);

  useEffect(() => {
    loadLatest();
    loadHistory();
  }, [loadLatest, loadHistory]);

  useEffect(() => {
    if (skipDraftSaveRef.current) {
      skipDraftSaveRef.current = false;
      return;
    }
    const t = setTimeout(() => {
      draftSaveTimeoutRef.current = null;
      fetch("/api/manage/config/draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config }),
      }).then(() => setCurrentVersion("draft")).catch(() => {});
    }, 500);
    draftSaveTimeoutRef.current = t;
    return () => {
      if (draftSaveTimeoutRef.current) clearTimeout(draftSaveTimeoutRef.current);
    };
  }, [config]);

  const loadVersion = async (version: number) => {
    try {
      const res = await fetch(`/api/manage/config?version=${version}`);
      if (res.ok) {
        const data = await res.json();
        const next = { ...DEFAULT_CONFIG, ...data.config };
        if (Array.isArray(next.tools)) {
          next.tools = normalizeTools(next.tools);
        }
        setConfig(next);
        setCurrentVersion(version);
      }
    } catch (e) {
      setSaveStatus("error");
      setTimeout(() => setSaveStatus("idle"), 2000);
    }
  };

  const saveConfig = async () => {
    setSaveStatus("saving");
    try {
      const policiesRes = await fetch("/api/config/policies");
      let toSave = { ...config };
      if (policiesRes.ok) {
        const policiesData = await policiesRes.json();
        toSave = { ...toSave, policies: { enablePolicies: policiesData.enablePolicies ?? true, policies: policiesData.policies ?? [] } };
      }
      const res = await fetch("/api/manage/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: toSave }),
      });
      if (res.ok) {
        const data = await res.json();
        setConfig(toSave);
        setCurrentVersion(typeof data.version === "number" ? data.version : "draft");
        setSaveStatus("success");
        loadHistory();
        setTimeout(() => setSaveStatus("idle"), 2000);
      } else {
        setSaveStatus("error");
        setTimeout(() => setSaveStatus("idle"), 2000);
      }
    } catch {
      setSaveStatus("error");
      setTimeout(() => setSaveStatus("idle"), 2000);
    }
  };

  const updateLlm = (field: "api_key" | "base_url" | "model", value: string) => {
    setConfig((c) => ({
      ...c,
      llm: { ...(c.llm ?? {}), [field]: value },
    }));
  };
  const updateLlmTemperature = (value: number) => {
    setConfig((c) => ({
      ...c,
      llm: { ...(c.llm ?? {}), temperature: value },
    }));
  };

  const updateFeatureFlag = (field: "enable_todos" | "reflection", value: boolean) => {
    setConfig((c) => ({
      ...c,
      feature_flags: { ...(c.feature_flags ?? {}), [field]: value },
    }));
  };

  const updateMaxSteps = (value: number) => {
    setConfig((c) => ({
      ...c,
      feature_flags: { ...(c.feature_flags ?? {}), max_steps: value },
    }));
  };

  const setTools = (tools: ToolEntry[]) => {
    setConfig((c) => ({ ...c, tools }));
  };

  const handleImportJson = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = "";
      if (!file) return;
      setImportStatus("idle");
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const text = reader.result as string;
          const raw = JSON.parse(text) as Record<string, unknown>;
          const out: AgentConfig = { ...DEFAULT_CONFIG };
          if (raw.llm && typeof raw.llm === "object") {
            out.llm = { ...out.llm, ...(raw.llm as Record<string, unknown>) };
          }
          if (Array.isArray(raw.tools)) {
            out.tools = normalizeTools(raw.tools);
          }
          if (raw.feature_flags && typeof raw.feature_flags === "object") {
            out.feature_flags = { ...out.feature_flags, ...(raw.feature_flags as Record<string, unknown>) };
          }
          if (raw.policies !== undefined) {
            const p = raw.policies;
            if (Array.isArray(p)) {
              out.policies = { enablePolicies: true, policies: p };
            } else if (p && typeof p === "object" && "policies" in p) {
              const po = p as { enablePolicies?: boolean; policies?: unknown[] };
              out.policies = {
                enablePolicies: po.enablePolicies ?? true,
                policies: Array.isArray(po.policies) ? po.policies : [],
              };
            }
          }
          setConfig(out);
          setImportStatus("ok");
          setTimeout(() => setImportStatus("idle"), 2500);
        } catch {
          setImportStatus("error");
          setTimeout(() => setImportStatus("idle"), 2500);
        }
      };
      reader.readAsText(file);
    },
    [normalizeTools]
  );

  const llm = config.llm ?? {};
  const flags = config.feature_flags ?? {};
  const tools = config.tools ?? [];
  const policiesList = config.policies?.policies ?? [];
  const summary = policiesSummary(policiesList);
  const policiesEnabled = config.policies?.enablePolicies ?? false;

  return (
    <div className="manage-page">
      <header className="manage-header">
        <h1 className="manage-header-title">{agentId ? `${agentId} — configuration` : "Agent configuration"}</h1>
        <div className="manage-header-links">
          <Link to={`/manage${search}`}>← Agents</Link>
          <Link to={search ? `/${search}` : "/"}>Chat</Link>
        </div>
      </header>

      <div className="manage-layout">
        <aside className="manage-left">
          <div className="manage-config">
            <div className="manage-config-grid">
              <section className="manage-section">
                <h3 className="manage-section-title">
                  <KeyIcon size={16} className="manage-section-icon" />
                  LLM
                </h3>
                <div className="manage-section-grid">
                  <FormGroup legendText="">
                    <TextInput
                      type="password"
                      id="llm-api-key"
                      labelText="API Key"
                      value={llm.api_key ?? ""}
                      onChange={(e) => updateLlm("api_key", e.target.value)}
                      placeholder="sk-..."
                    />
                  </FormGroup>
                  <FormGroup legendText="">
                    <TextInput
                      type="text"
                      id="llm-base-url"
                      labelText="Base URL"
                      value={llm.base_url ?? ""}
                      onChange={(e) => updateLlm("base_url", e.target.value)}
                      placeholder="https://api.openai.com/v1"
                    />
                    <span className="manage-helper">Optional; leave empty for default</span>
                  </FormGroup>
                  <FormGroup legendText="">
                    <TextInput
                      type="text"
                      id="llm-model"
                      labelText="Model"
                      value={llm.model ?? ""}
                      onChange={(e) => updateLlm("model", e.target.value)}
                      placeholder="gpt-4o"
                    />
                  </FormGroup>
                  <FormGroup legendText="" className="manage-section-grid-span-2">
                    <NumberInput
                      id="llm-temperature"
                      label="Temperature"
                      min={0}
                      max={2}
                      step={0.1}
                      value={llm.temperature ?? 0.7}
                      onChange={(_e: unknown, { value }: { value: number | string }) =>
                        updateLlmTemperature(Number(value) || 0.7)
                      }
                    />
                  </FormGroup>
                </div>
              </section>

              <ToolsConfig
                tools={tools}
                onChange={setTools}
                connectedApps={connectedApps}
                connectedTools={connectedTools}
              />

              <section className="manage-section">
                <h3 className="manage-section-title">
                  <FlagIcon size={16} className="manage-section-icon" />
                  Feature flags
                </h3>
                <div className="manage-section-grid">
                  <div className="manage-checkbox-row">
                    <Checkbox
                      id="enable_todos"
                      labelText="Enable todos"
                      checked={flags.enable_todos ?? true}
                      onChange={(_e, { checked }) => updateFeatureFlag("enable_todos", !!checked)}
                    />
                  </div>
                  <div className="manage-checkbox-row">
                    <Checkbox
                      id="reflection"
                      labelText="Reflection"
                      checked={flags.reflection ?? false}
                      onChange={(_e, { checked }) => updateFeatureFlag("reflection", !!checked)}
                    />
                  </div>
                  <FormGroup legendText="" className="manage-section-grid-span-2">
                    <NumberInput
                      id="max_steps"
                      label="Max steps"
                      min={1}
                      max={200}
                      value={flags.max_steps ?? 20}
                      onChange={(_e: unknown, { value }: { value: number | string }) =>
                        updateMaxSteps(Number(value) || 20)
                      }
                    />
                  </FormGroup>
                </div>
              </section>

              <section className="manage-section">
                <h3 className="manage-section-title">
                  <ShieldIcon size={16} className="manage-section-icon" />
                  Policies
                </h3>
                <div className="manage-policies-summary">
                  <div className="manage-policies-summary-row">
                    <span className="manage-policies-total">
                      {policiesEnabled ? `${summary.total} policy${summary.total !== 1 ? "ies" : ""} defined` : "Policies disabled"}
                    </span>
                  </div>
                  {policiesEnabled && summary.total > 0 && (
                    <div className="manage-policies-breakdown">
                      {Object.entries(summary.byType).map(([type, count]) => (
                        <Tag key={type} type="gray" size="md" className="manage-policies-badge">
                          {POLICY_TYPE_LABELS[type] ?? type}: {count}
                        </Tag>
                      ))}
                    </div>
                  )}
                </div>
                <Button
                  kind="primary"
                  renderIcon={ShieldIcon}
                  onClick={() => setShowPoliciesModal(true)}
                  className="manage-policies-open-btn"
                >
                  Configure policies
                </Button>
              </section>

              <div className="manage-history">
                <h4 className="manage-history-title">
                  <HistoryIcon size={16} className="manage-section-icon" />
                  Version history
                </h4>
                <div className="manage-history-list">
                  {history.length === 0 && (
                    <div className="manage-history-empty">No versions yet</div>
                  )}
                  {history.map((v) => (
                    <div key={v.version} className="manage-history-item">
                      <span
                        className="manage-history-item-main"
                        onClick={() => loadVersion(v.version)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => e.key === "Enter" && loadVersion(v.version)}
                      >
                        <Tag type="blue" size="md" className="version-badge">v{v.version}</Tag>
                        {new Date(v.created_at).toLocaleString()}
                      </span>
                      <Button
                        kind="ghost"
                        size="sm"
                        hasIconOnly
                        iconDescription="View JSON"
                        renderIcon={DocumentIcon}
                        onClick={(e) => {
                          e.stopPropagation();
                          fetch(`/api/manage/config?version=${v.version}`)
                            .then((res) => (res.ok ? res.json() : null))
                            .then((data) => data && setViewVersion({ version: v.version, config: data.config ?? {} }))
                            .catch(() => {});
                        }}
                      />
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <div className="manage-save-bar">
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,application/json"
              className="manage-import-input"
              aria-label="Import config JSON"
              onChange={handleImportJson}
            />
            <div className="manage-save-bar-actions">
              <Button
                kind="secondary"
                renderIcon={Upload}
                onClick={() => fileInputRef.current?.click()}
                className="manage-import-btn"
              >
                Import JSON
              </Button>
              <Button
                kind="primary"
                renderIcon={Save}
                onClick={saveConfig}
                disabled={saveStatus === "saving"}
                className="manage-save-btn"
              >
                {saveStatus === "idle" && "Save configuration"}
                {saveStatus === "saving" && "Saving…"}
                {saveStatus === "success" && "Saved"}
                {saveStatus === "error" && "Error"}
              </Button>
            </div>
            <div
              className={`manage-save-status ${
                saveStatus === "success" || importStatus === "ok" ? "success" : saveStatus === "error" || importStatus === "error" ? "error" : ""
              }`}
            >
              {currentVersion != null && (
                <span className="manage-version-label">
                  Version: {currentVersion === "draft" ? "draft" : currentVersion}
                </span>
              )}
              {loadError && <span>{loadError}</span>}
              {!loadError && importStatus === "ok" && <span>Config imported</span>}
              {!loadError && importStatus === "error" && <span>Invalid JSON</span>}
            </div>
          </div>
        </aside>

        <main className="manage-right">
          <p className="manage-chat-label">Try your configuration</p>
          <div className="manage-chat-wrap">
            <CustomChat
              initialChatStarted={true}
              forceAdvancedMode={true}
              useDraftAgent={true}
              onVariablesUpdate={handleManageVariablesUpdate}
            />
          </div>
        </main>
      </div>

      {(manageVariablesHistory.length > 0 || Object.keys(manageVariables).length > 0) && (
        <>
          <div className="manage-variables-toggle-wrap">
            <Button
              kind="secondary"
              className="manage-variables-toggle"
              onClick={() => setManageVariablesPanelOpen((o) => !o)}
              title={manageVariablesPanelOpen ? "Close variables" : "Open variables"}
              aria-expanded={manageVariablesPanelOpen}
              renderIcon={DocumentIcon}
            >
              Variables
            </Button>
            {!manageVariablesPanelOpen && (
              <Tag type="blue" size="sm" className="manage-variables-toggle-count">
                {Object.keys(manageVariables).length || manageVariablesHistory.length}
              </Tag>
            )}
          </div>
          {manageVariablesPanelOpen && (
            <ComposedModal
              open={manageVariablesPanelOpen}
              onClose={() => setManageVariablesPanelOpen(false)}
              className="manage-variables-modal"
            >
              <ModalHeader title="Variables" />
              <ModalBody className="manage-variables-panel-body">
                <VariablesSidebar
                  variables={manageVariables}
                  history={manageVariablesHistory}
                  selectedAnswerId={manageSelectedAnswerId}
                  onSelectAnswer={(id: string) => setManageSelectedAnswerId(id)}
                />
              </ModalBody>
            </ComposedModal>
          )}
        </>
      )}

      {showPoliciesModal && (
        <PoliciesConfig
          draftMode={true}
          onClose={() => setShowPoliciesModal(false)}
        />
      )}

      <ComposedModal
        open={!!viewVersion}
        onClose={() => setViewVersion(null)}
        size="lg"
        isFullWidth
      >
        <ModalHeader
          title={viewVersion ? `Version ${viewVersion.version}` : ""}
          buttonOnClick={() => setViewVersion(null)}
        />
        <ModalBody hasScrollingContent>
          {viewVersion && (
            <pre className="manage-json-viewer-pre">
              <code>{JSON.stringify(maskSecrets(viewVersion.config), null, 2)}</code>
            </pre>
          )}
        </ModalBody>
      </ComposedModal>
    </div>
  );
}
