import React, { useState, useEffect, useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { Save, History, Key, Flag, Trash2, Shield, FileJson, X } from "lucide-react";
import { CustomChat } from "agentic_chat/CustomChat";
import PoliciesConfig from "agentic_chat/PoliciesConfig";
import { ToolsConfig } from "./ToolsConfig";
import type { ToolEntry } from "./types/tools";
import "./ManagePage.css";

export type { ToolEntry } from "./types/tools";

export interface AgentConfig {
  llm?: { api_key?: string; base_url?: string };
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
  llm: { api_key: "", base_url: "" },
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
  const [config, setConfig] = useState<AgentConfig>(DEFAULT_CONFIG);
  const [history, setHistory] = useState<ConfigVersion[]>([]);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "success" | "error">("idle");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showPoliciesModal, setShowPoliciesModal] = useState(false);
  const [viewVersion, setViewVersion] = useState<{ version: number; config: AgentConfig } | null>(null);

  const normalizeTools = useCallback((raw: unknown[]): ToolEntry[] => {
    return (raw ?? []).map((t: Record<string, unknown>) => {
      const type = (t.type as string) === "openapi" ? "openapi" : "mcp";
      let auth = t.auth as ToolEntry["auth"] | string | undefined;
      if (typeof auth === "string" && auth) {
        auth = { type: "bearer", value: auth };
      }
      return {
        name: (t.name as string) ?? type,
        type,
        url: (t.url as string) ?? "",
        description: t.description as string | undefined,
        auth,
      };
    });
  }, []);

  const loadLatest = useCallback(async () => {
    try {
      const [configRes, policiesRes] = await Promise.all([
        fetch("/api/manage/config"),
        fetch("/api/config/policies"),
      ]);
      const out = { ...DEFAULT_CONFIG };
      if (configRes.ok) {
        const data = await configRes.json();
        Object.assign(out, data.config);
        if (Array.isArray(out.tools)) {
          out.tools = normalizeTools(out.tools);
        }
      }
      if (policiesRes.ok) {
        const policiesData = await policiesRes.json();
        out.policies = { enablePolicies: policiesData.enablePolicies ?? true, policies: policiesData.policies ?? [] };
      }
      setConfig(out);
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
      }
    } catch (e) {
      setSaveStatus("error");
      setTimeout(() => setSaveStatus("idle"), 2000);
    }
  };

  const refetchPolicies = useCallback(async () => {
    try {
      const res = await fetch("/api/config/policies");
      if (res.ok) {
        const data = await res.json();
        setConfig((c) => ({ ...c, policies: { enablePolicies: data.enablePolicies ?? true, policies: data.policies ?? [] } }));
      }
    } catch {
      // ignore
    }
  }, []);

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
        setConfig(toSave);
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

  const updateLlm = (field: "api_key" | "base_url", value: string) => {
    setConfig((c) => ({
      ...c,
      llm: { ...(c.llm ?? {}), [field]: value },
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

  const llm = config.llm ?? {};
  const flags = config.feature_flags ?? {};
  const tools = config.tools ?? [];
  const policiesList = config.policies?.policies ?? [];
  const summary = policiesSummary(policiesList);
  const policiesEnabled = config.policies?.enablePolicies ?? false;

  return (
    <div className="manage-page">
      <header className="manage-header">
        <h1>{agentId ? `${agentId} — configuration` : "Agent configuration"}</h1>
        <div className="manage-header-links">
          <Link to="/manage">← Agents</Link>
          <Link to="/">Chat</Link>
        </div>
      </header>

      <div className="manage-layout">
        <aside className="manage-left">
          <div className="manage-config">
            <section className="manage-section">
              <h3>
                <Key size={14} style={{ verticalAlign: "middle", marginRight: 6 }} />
                LLM
              </h3>
              <div className="manage-form-group">
                <label>API Key</label>
                <input
                  type="password"
                  value={llm.api_key ?? ""}
                  onChange={(e) => updateLlm("api_key", e.target.value)}
                  placeholder="sk-..."
                />
              </div>
              <div className="manage-form-group">
                <label>Base URL</label>
                <input
                  type="text"
                  value={llm.base_url ?? ""}
                  onChange={(e) => updateLlm("base_url", e.target.value)}
                  placeholder="https://api.openai.com/v1"
                />
                <small>Optional; leave empty for default</small>
              </div>
            </section>

            <ToolsConfig tools={tools} onChange={setTools} />

            <section className="manage-section">
              <h3>
                <Flag size={14} style={{ verticalAlign: "middle", marginRight: 6 }} />
                Feature flags
              </h3>
              <div className="manage-checkbox-row">
                <input
                  type="checkbox"
                  id="enable_todos"
                  checked={flags.enable_todos ?? true}
                  onChange={(e) => updateFeatureFlag("enable_todos", e.target.checked)}
                />
                <label htmlFor="enable_todos">Enable todos</label>
              </div>
              <div className="manage-checkbox-row">
                <input
                  type="checkbox"
                  id="reflection"
                  checked={flags.reflection ?? false}
                  onChange={(e) => updateFeatureFlag("reflection", e.target.checked)}
                />
                <label htmlFor="reflection">Reflection</label>
              </div>
              <div className="manage-form-group">
                <label>Max steps</label>
                <input
                  type="number"
                  min={1}
                  max={200}
                  value={flags.max_steps ?? 20}
                  onChange={(e) => updateMaxSteps(parseInt(e.target.value, 10) || 20)}
                />
              </div>
            </section>

            <section className="manage-section">
              <h3>
                <Shield size={14} style={{ verticalAlign: "middle", marginRight: 6 }} />
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
                      <span key={type} className="manage-policies-badge">
                        {POLICY_TYPE_LABELS[type] ?? type}: {count}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <button
                type="button"
                className="manage-policies-open-btn"
                onClick={() => setShowPoliciesModal(true)}
              >
                <Shield size={14} />
                Configure policies
              </button>
            </section>

            <div className="manage-history">
              <h4>
                <History size={12} style={{ verticalAlign: "middle", marginRight: 4 }} />
                Version history
              </h4>
              <div className="manage-history-list">
                {history.length === 0 && <div style={{ fontSize: 12, color: "#71717a", padding: 8 }}>No versions yet</div>}
                {history.map((v) => (
                  <div key={v.version} className="manage-history-item">
                    <span
                      className="manage-history-item-main"
                      onClick={() => loadVersion(v.version)}
                    >
                      <span className="version-badge">v{v.version}</span>
                      {new Date(v.created_at).toLocaleString()}
                    </span>
                    <button
                      type="button"
                      className="manage-history-view-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        fetch(`/api/manage/config?version=${v.version}`)
                          .then((res) => res.ok ? res.json() : null)
                          .then((data) => data && setViewVersion({ version: v.version, config: data.config ?? {} }))
                          .catch(() => {});
                      }}
                      title="View JSON"
                    >
                      <FileJson size={14} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="manage-save-bar">
            <button
              className="manage-save-btn"
              onClick={saveConfig}
              disabled={saveStatus === "saving"}
            >
              <Save size={16} style={{ verticalAlign: "middle", marginRight: 6 }} />
              {saveStatus === "idle" && "Save configuration"}
              {saveStatus === "saving" && "Saving…"}
              {saveStatus === "success" && "Saved"}
              {saveStatus === "error" && "Error"}
            </button>
            <div className={`manage-save-status ${saveStatus === "success" ? "success" : saveStatus === "error" ? "error" : ""}`}>
              {loadError && <span>{loadError}</span>}
            </div>
          </div>
        </aside>

        <main className="manage-right">
          <div className="manage-chat-wrap">
            <CustomChat initialChatStarted={true} />
          </div>
        </main>
      </div>

      {showPoliciesModal && (
        <PoliciesConfig
          onClose={() => {
            setShowPoliciesModal(false);
            refetchPolicies();
          }}
        />
      )}

      {viewVersion && (
        <div className="manage-json-viewer-overlay" onClick={() => setViewVersion(null)}>
          <div className="manage-json-viewer-modal" onClick={(e) => e.stopPropagation()}>
            <div className="manage-json-viewer-header">
              <h2>
                <FileJson size={18} style={{ verticalAlign: "middle", marginRight: 8 }} />
                Version {viewVersion.version}
              </h2>
              <button type="button" className="manage-json-viewer-close" onClick={() => setViewVersion(null)} aria-label="Close">
                <X size={20} />
              </button>
            </div>
            <div className="manage-json-viewer-body">
              <pre className="manage-json-viewer-pre">
                <code>{JSON.stringify(maskSecrets(viewVersion.config), null, 2)}</code>
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
