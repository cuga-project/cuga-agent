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
  Grid,
  Row,
  Column,
  Stack,
  VStack,
  HStack,
  Tile,
  ClickableTile,
  InlineNotification,
  Layer,
  Accordion,
  AccordionItem,
  ToastNotification,
} from "@carbon/react";
import { CugaHeader } from "agentic_chat/CugaHeader";
import {
  Save,
  Time as HistoryIcon,
  Key as KeyIcon,
  Flag as FlagIcon,
  Security as ShieldIcon,
  Document as DocumentIcon,
  Upload,
  Tools,
} from "@carbon/icons-react";
import CarbonChat from "./carbon-chat/CarbonChat";
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
  const [toastNotifications, setToastNotifications] = useState<Array<{ id: string; kind: "error" | "info" | "success" | "warning"; title: string; subtitle: string }>>([]);
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

  type ToastNotification = { id: string; kind: "error" | "info" | "success" | "warning"; title: string; subtitle: string };

  const addToast = useCallback((kind: "error" | "info" | "success" | "warning", title: string, subtitle: string) => {
    const id = `toast-${Date.now()}-${Math.random()}`;
    console.log('[Toast Debug] Adding toast:', { id, kind, title, subtitle });
    setToastNotifications((prev: ToastNotification[]) => {
      const newToasts = [...prev, { id, kind, title, subtitle }];
      console.log('[Toast Debug] Current toasts:', newToasts);
      return newToasts;
    });
    setTimeout(() => {
      console.log('[Toast Debug] Removing toast:', id);
      setToastNotifications((prev: ToastNotification[]) => prev.filter((t: ToastNotification) => t.id !== id));
    }, 5000);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToastNotifications((prev: ToastNotification[]) => prev.filter((t: ToastNotification) => t.id !== id));
  }, []);

  const loadLatest = useCallback(async () => {
    try {
      skipDraftSaveRef.current = true;
      const [draftRes, toolsListRes] = await Promise.all([
        fetch("/api/manage/config?draft=1"),
        fetch("/api/tools/list"),
      ]);
      
      // Check for HTTP errors
      if (!draftRes.ok && draftRes.status >= 400) {
        const errorMsg = `Failed to load draft config (${draftRes.status} ${draftRes.statusText})`;
        addToast("error", "Load Error", errorMsg);
      }
      if (!toolsListRes.ok && toolsListRes.status >= 400) {
        const errorMsg = `Failed to load tools list (${toolsListRes.status} ${toolsListRes.statusText})`;
        addToast("warning", "Load Warning", errorMsg);
      }
      
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
            // Policies are now included in the config from manage API
            if (out.policies) {
              // Ensure policies structure is correct
              if (!out.policies.enablePolicies && out.policies.enablePolicies !== false) {
                out.policies.enablePolicies = true;
              }
              if (!Array.isArray(out.policies.policies)) {
                out.policies.policies = [];
              }
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
            // Policies are now included in the config from manage API
            if (out.policies) {
              // Ensure policies structure is correct
              if (!out.policies.enablePolicies && out.policies.enablePolicies !== false) {
                out.policies.enablePolicies = true;
              }
              if (!Array.isArray(out.policies.policies)) {
                out.policies.policies = [];
              }
            }
          }
          version = typeof data.version === "number" ? data.version : null;
        } else if (publishedRes.status >= 400) {
          const errorMsg = `Failed to load published config (${publishedRes.status} ${publishedRes.statusText})`;
          addToast("error", "Load Error", errorMsg);
        }
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
      const errorMsg = e instanceof Error ? e.message : "Failed to load config";
      setLoadError(errorMsg);
      addToast("error", "Load Error", errorMsg);
    }
  }, [normalizeTools, addToast]);

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
      })
        .then(async (res) => {
          if (res.ok) {
            setCurrentVersion("draft");
          } else {
            const errorMsg = `Failed to save draft (${res.status} ${res.statusText})`;
            console.error('[Draft Save Error]', errorMsg);
            addToast("warning", "Draft Save Failed", errorMsg);
          }
        })
        .catch((error) => {
          const errorMsg = error instanceof Error ? error.message : "Network error saving draft";
          console.error('[Draft Save Error]', errorMsg);
          addToast("warning", "Draft Save Failed", errorMsg);
        });
    }, 500);
    draftSaveTimeoutRef.current = t;
    return () => {
      if (draftSaveTimeoutRef.current) clearTimeout(draftSaveTimeoutRef.current);
    };
  }, [config, addToast]);

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
        addToast("success", "Version Loaded", `Loaded version ${version}`);
      } else {
        const errorMsg = `Failed to load version ${version} (${res.status} ${res.statusText})`;
        addToast("error", "Load Error", errorMsg);
        setSaveStatus("error");
        setTimeout(() => setSaveStatus("idle"), 2000);
      }
    } catch (e) {
      const errorMsg = e instanceof Error ? e.message : `Failed to load version ${version}`;
      addToast("error", "Load Error", errorMsg);
      setSaveStatus("error");
      setTimeout(() => setSaveStatus("idle"), 2000);
    }
  };

  const saveConfig = async () => {
    setSaveStatus("saving");
    try {
      // Policies are now part of the config, no need to fetch separately
      let toSave = { ...config };
      
      // Ensure policies structure exists
      if (!toSave.policies) {
        toSave.policies = { enablePolicies: true, policies: [] };
      }
      const res = await fetch("/api/manage/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: toSave }),
      });
      if (res.ok) {
        const data = await res.json();
        console.log('[Save Config] Response data:', data);
        
        // Check for partial status and tool errors
        const hasPartialErrors = data.status === "partial" && data.tool_errors;
        console.log('[Save Config] Has partial errors:', hasPartialErrors);
        
        if (hasPartialErrors) {
          console.log('[Save Config] Processing tool errors:', data.tool_errors);
          // Show warning toast for each tool error
          Object.entries(data.tool_errors as Record<string, any>).forEach(([toolName, errorInfo]: [string, any]) => {
            const errorMsg = errorInfo.error || errorInfo.message || "Unknown error";
            const errorType = errorInfo.type ? ` (${errorInfo.type})` : "";
            addToast("warning", `Tool initialization failed: ${toolName}`, `${errorMsg}${errorType}`);
          });
          
          // Show summary message
          const errorCount = Object.keys(data.tool_errors).length;
          addToast("info", "Configuration partially saved", data.message || `${errorCount} tool(s) failed to initialize`);
        }
        
        // Also check for legacy partial_errors format
        if (data.partial_errors && Array.isArray(data.partial_errors) && data.partial_errors.length > 0) {
          data.partial_errors.forEach((error: any) => {
            const errorMsg = typeof error === "string" ? error : (error.message || error.error || "Unknown error");
            addToast("warning", "Partial save error", errorMsg);
          });
        }
        
        setConfig(toSave);
        setCurrentVersion(typeof data.version === "number" ? data.version : "draft");
        setSaveStatus("success");
        
        // Show success toast only if no errors
        if (!hasPartialErrors && (!data.partial_errors || data.partial_errors.length === 0)) {
          addToast("success", "Configuration saved", "Your configuration has been saved successfully");
        }
        
        loadHistory();
        setTimeout(() => setSaveStatus("idle"), 2000);
      } else {
        // Handle HTTP error response
        let errorMsg = `Failed to save configuration (${res.status} ${res.statusText})`;
        try {
          const errorData = await res.json();
          errorMsg = errorData.error || errorData.message || errorMsg;
        } catch {
          // If response is not JSON, use default error message
        }
        
        setSaveStatus("error");
        addToast("error", "Save Failed", errorMsg);
        setTimeout(() => setSaveStatus("idle"), 2000);
      }
    } catch (error) {
      const errorMsg = error instanceof Error ? error.message : "Network error occurred";
      setSaveStatus("error");
      addToast("error", "Network Error", errorMsg);
      setTimeout(() => setSaveStatus("idle"), 2000);
    }
  };

  const updateLlm = (field: "api_key" | "base_url" | "model", value: string) => {
    setConfig((c: AgentConfig) => ({
      ...c,
      llm: { ...(c.llm ?? {}), [field]: value },
    }));
  };
  const updateLlmTemperature = (value: number) => {
    setConfig((c: AgentConfig) => ({
      ...c,
      llm: { ...(c.llm ?? {}), temperature: value },
    }));
  };

  const updateFeatureFlag = (field: "enable_todos" | "reflection", value: boolean) => {
    setConfig((c: AgentConfig) => ({
      ...c,
      feature_flags: { ...(c.feature_flags ?? {}), [field]: value },
    }));
  };

  const updateMaxSteps = (value: number) => {
    setConfig((c: AgentConfig) => ({
      ...c,
      feature_flags: { ...(c.feature_flags ?? {}), max_steps: value },
    }));
  };

  const setTools = (tools: ToolEntry[]) => {
    setConfig((c: AgentConfig) => ({ ...c, tools }));
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
      <CugaHeader
        title={agentId ? `${agentId} — configuration` : "Agent configuration"}
        navItems={[
          { label: "Agents", to: `/manage${search}` },
          { label: "Chat", to: search ? `/${search}` : "/chat" },
        ]}
        linkComponent={Link}
      />

      <div className="manage-layout">
        <div className="manage-config-panel">
          <div className="manage-config-scroll">
            <Layer withBackground>
            <Accordion align="start" size="lg">
              <AccordionItem title="LLM Configuration" open>
                  <VStack gap={5} className="manage-llm-fields">
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
                        helperText="Optional; leave empty for default"
                      />
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
                    <FormGroup legendText="">
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
                  </VStack>
              </AccordionItem>

              <AccordionItem title="Tools" open>
                  <ToolsConfig
                    tools={tools}
                    onChange={setTools}
                    connectedApps={connectedApps}
                    connectedTools={connectedTools}
                    agentId= {"cuga-default"}
                    onError={(title, message) => addToast("error", title, message)}
                  />
              </AccordionItem>

              <AccordionItem title="Feature Flags">
                  <VStack gap={5}>
                    <FormGroup legendText="">
                      <Checkbox
                        id="enable_todos"
                        labelText="Enable todos"
                        checked={flags.enable_todos ?? true}
                        onChange={(_e, { checked }) => updateFeatureFlag("enable_todos", !!checked)}
                      />
                    </FormGroup>
                    <FormGroup legendText="">
                      <Checkbox
                        id="reflection"
                        labelText="Reflection"
                        checked={flags.reflection ?? false}
                        onChange={(_e, { checked }) => updateFeatureFlag("reflection", !!checked)}
                      />
                    </FormGroup>
                    <FormGroup legendText="">
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
                  </VStack>
              </AccordionItem>

              <AccordionItem title="Policies">
                  <Stack gap={3} orientation="vertical">
                    <p className="cds--type-body-compact-01">
                      {policiesEnabled
                        ? `${summary.total} policy${summary.total !== 1 ? "ies" : ""} defined`
                        : "Policies disabled"}
                    </p>
                    {policiesEnabled && summary.total > 0 && (
                      <div className="manage-policies-tags">
                        {Object.entries(summary.byType).map(([type, count]) => (
                          <Tag key={type} type="gray" size="md">
                            {POLICY_TYPE_LABELS[type] ?? type}: {count}
                          </Tag>
                        ))}
                      </div>
                    )}
                    <Button
                      kind="primary"
                      renderIcon={ShieldIcon}
                      onClick={() => setShowPoliciesModal(true)}
                    >
                      Configure policies
                    </Button>
                  </Stack>
              </AccordionItem>

              <AccordionItem title="Version History">
                  {history.length === 0 ? (
                    <p className="cds--type-body-compact-01 cds--color-text-placeholder">No versions yet</p>
                  ) : (
                    <Stack gap={2} orientation="vertical" className="manage-history-stack">
                      {history.map((v: ConfigVersion) => (
                        <ClickableTile
                          key={v.version}
                          onClick={() => loadVersion(v.version)}
                          className="manage-history-tile"
                        >
                          <div className="manage-history-tile-row">
                            <div className="manage-tile-heading">
                              <Tag type="blue" size="md">v{v.version}</Tag>
                              <span className="cds--type-body-compact-01">
                                {new Date(v.created_at).toLocaleString()}
                              </span>
                            </div>
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
                        </ClickableTile>
                      ))}
                    </Stack>
                  )}
              </AccordionItem>
            </Accordion>
            </Layer>
</div>
              <Layer withBackground className="manage-save-bar">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".json,application/json"
                  className="manage-import-input"
                  aria-label="Import config JSON"
                  onChange={handleImportJson}
                />
                <div className="manage-save-bar-content">
                  <div className="manage-save-bar-buttons">
                    <Button
                      kind="secondary"
                      renderIcon={Upload}
                      onClick={() => fileInputRef.current?.click()}
                      className="manage-save-bar-button"
                    >
                      Import JSON
                    </Button>
                    <Button
                      kind="primary"
                      renderIcon={Save}
                      onClick={saveConfig}
                      disabled={saveStatus === "saving"}
                      className="manage-save-bar-button"
                    >
                      {saveStatus === "idle" && "Save Configuration"}
                      {saveStatus === "saving" && "Saving…"}
                      {saveStatus === "success" && "Saved"}
                      {saveStatus === "error" && "Error"}
                    </Button>
                  </div>
                  {(loadError || currentVersion != null || importStatus !== "idle") && (
                    <div className="manage-save-bar-status">
                      {loadError && (
                        <InlineNotification kind="error" title="Error" subtitle={loadError} lowContrast hideCloseButton />
                      )}
                      {!loadError && importStatus === "ok" && (
                        <InlineNotification kind="success" title="Success" subtitle="Config imported" lowContrast hideCloseButton />
                      )}
                      {!loadError && importStatus === "error" && (
                        <InlineNotification kind="error" title="Error" subtitle="Invalid JSON" lowContrast hideCloseButton />
                      )}
                      {!loadError && currentVersion != null && (
                        <p className="manage-save-bar-version">
                          Version: {currentVersion === "draft" ? "draft" : String(currentVersion)}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              </Layer>
          </div>

        <Layer withBackground className="manage-chat-panel">
          <p className="manage-chat-label">Try your configuration</p>
          <div className="manage-chat-wrap">
            <CarbonChat contained={true} useDraft={true} disableHistory={true} />
          </div>
        </Layer>
      </div>

      {(manageVariablesHistory.length > 0 || Object.keys(manageVariables).length > 0) && (
        <>
          <div className="manage-variables-toggle-wrap">
            <Button
              kind="secondary"
              className="manage-variables-toggle"
              onClick={() => setManageVariablesPanelOpen((o: boolean) => !o)}
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

      {/* Toast Notifications */}
      <div
        style={{
          position: "fixed",
          top: "3rem",
          right: "1rem",
          zIndex: 9999,
          display: "flex",
          flexDirection: "column",
          gap: "0.5rem",
          maxWidth: "400px"
        }}
      >
        {console.log('[Toast Debug] Rendering toasts:', toastNotifications)}
        {toastNotifications.map((toast: { id: string; kind: "error" | "info" | "success" | "warning"; title: string; subtitle: string }) => {
          console.log('[Toast Debug] Rendering individual toast:', toast);
          return (
            <ToastNotification
              key={toast.id}
              kind={toast.kind}
              title={toast.title}
              subtitle={toast.subtitle}
              timeout={5000}
              onClose={() => removeToast(toast.id)}
              lowContrast
            />
          );
        })}
      </div>
    </div>
  );
}
