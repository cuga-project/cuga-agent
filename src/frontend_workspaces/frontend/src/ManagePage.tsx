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
  ModalFooter,
  Grid,
  Row,
  Column,
  Stack,
  VStack,
  HStack,
  Tile,
  ClickableTile,
  InlineNotification,
  InlineLoading,
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
  Download,
  Upload,
  Tools,
} from "@carbon/icons-react";
import Markdown from "@carbon/ai-chat-components/es/react/markdown.js";
import CarbonChat from "./carbon-chat/CarbonChat";
import PoliciesConfig from "agentic_chat/PoliciesConfig";
import VariablesSidebar from "agentic_chat/VariablesSidebar";
import { ToolsConfig, type ConnectedApp, type ConnectedTool } from "./ToolsConfig";
import type { ToolEntry } from "./types/tools";
import "./ManagePage.css";

export type { ToolEntry } from "./types/tools";

export interface HomescreenConfig {
  isOn?: boolean;
  greeting?: string;
  starters?: string[];
}

export interface AgentConfig {
  llm?: { api_key?: string; base_url?: string; model?: string; temperature?: number };
  tools?: ToolEntry[];
  feature_flags?: {
    enable_todos?: boolean;
    reflection?: boolean;
    max_steps?: number;
    shortlisting_tool_threshold?: number;
  };
  policies?: { enablePolicies: boolean; policies: unknown[] };
  homescreen?: HomescreenConfig;
}

const DEFAULT_HOMESCREEN: HomescreenConfig = {
  isOn: true,
  greeting: "Hello, how can I help you today?",
  starters: ["Hi, what can you do for me?"],
};

export interface ConfigVersion {
  version: number;
  created_at: string;
}

const DEFAULT_CONFIG: AgentConfig = {
  llm: { api_key: "", base_url: "", model: "", temperature: 0.7 },
  tools: [],
  feature_flags: { enable_todos: true, reflection: false, max_steps: 70, shortlisting_tool_threshold: 35 },
  homescreen: { ...DEFAULT_HOMESCREEN },
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
  const [importError, setImportError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [manageVariables, setManageVariables] = useState<Record<string, any>>({});
  const [manageVariablesHistory, setManageVariablesHistory] = useState<Array<{ id: string; title: string; timestamp: number; variables: Record<string, any> }>>([]);
  const [manageSelectedAnswerId, setManageSelectedAnswerId] = useState<string | null>(null);
  const [manageVariablesPanelOpen, setManageVariablesPanelOpen] = useState(false);
  const [currentVersion, setCurrentVersion] = useState<number | "draft" | null>(null);
  const [draftSaving, setDraftSaving] = useState(false);
  const [agentContext, setAgentContext] = useState<{ agent_id: string; config_version: number | null } | null>(null);
  const skipDraftSaveRef = useRef(true);
  const draftSaveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const configRef = useRef(config);
  configRef.current = config;

  useEffect(() => {
    fetch("/api/agent/context")
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
        fetch("/api/tools/list?draft=1"),
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
            if (data.config.homescreen) {
              const hs = data.config.homescreen;
              out.homescreen = {
                isOn: hs.isOn ?? DEFAULT_HOMESCREEN.isOn,
                greeting: hs.greeting ?? DEFAULT_HOMESCREEN.greeting,
                starters: Array.isArray(hs.starters)
                  ? hs.starters.slice(0, 4).filter((s): s is string => typeof s === "string")
                  : DEFAULT_HOMESCREEN.starters ?? [],
              };
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
            if (data.config.homescreen) {
              const hs = data.config.homescreen;
              out.homescreen = {
                isOn: hs.isOn ?? DEFAULT_HOMESCREEN.isOn,
                greeting: hs.greeting ?? DEFAULT_HOMESCREEN.greeting,
                starters: Array.isArray(hs.starters)
                  ? hs.starters.slice(0, 4).filter((s): s is string => typeof s === "string")
                  : DEFAULT_HOMESCREEN.starters ?? [],
              };
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
      setTimeout(() => {
        skipDraftSaveRef.current = false;
      }, 0);
    } catch (e) {
      const errorMsg = e instanceof Error ? e.message : "Failed to load config";
      setLoadError(errorMsg);
      addToast("error", "Load Error", errorMsg);
      skipDraftSaveRef.current = false;
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

  const performDraftSave = useCallback(async () => {
    const toSave = configRef.current;
    setDraftSaving(true);
    try {
      const res = await fetch("/api/manage/config/draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: toSave }),
      });
      setDraftSaving(false);
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        setCurrentVersion("draft");
        const hasPartialErrors = data.status === "partial" && (data.tool_errors || data.policy_errors);
        if (hasPartialErrors) {
          if (data.tool_errors) {
            Object.entries(data.tool_errors as Record<string, { error?: string; message?: string; type?: string }>).forEach(
              ([toolName, err]) => {
                const msg = err?.error || err?.message || "Unknown error";
                const type = err?.type ? ` (${err.type})` : "";
                addToast("warning", `Tool failed: ${toolName}`, `${msg}${type}`);
              }
            );
          }
          if (data.policy_errors) {
            const errs = Array.isArray(data.policy_errors) ? data.policy_errors : [data.policy_errors];
            errs.forEach((e: unknown) => addToast("warning", "Policy error", typeof e === "string" ? e : String(e)));
          }
          addToast("info", "Draft saved with warnings", data.message || "Some tools or policies failed to load");
        } else {
          addToast("success", "Draft saved", "Your changes have been saved to draft");
        }
      } else {
        const errorMsg = `Failed to save draft (${res.status} ${res.statusText})`;
        addToast("error", "Draft Save Failed", errorMsg);
      }
    } catch (error) {
      setDraftSaving(false);
      const errorMsg = error instanceof Error ? error.message : "Network error saving draft";
      addToast("error", "Draft Save Failed", errorMsg);
    }
  }, [addToast]);

  useEffect(() => {
    if (skipDraftSaveRef.current) {
      return;
    }
    const t = setTimeout(() => {
      draftSaveTimeoutRef.current = null;
      performDraftSave();
    }, 500);
    draftSaveTimeoutRef.current = t;
    return () => {
      if (draftSaveTimeoutRef.current) clearTimeout(draftSaveTimeoutRef.current);
    };
  }, [JSON.stringify({ llm: config.llm, tools: config.tools, policies: config.policies, homescreen: config.homescreen }), performDraftSave]);

  useEffect(() => {
    if (importStatus === "ok") {
      performDraftSave();
    }
  }, [importStatus, performDraftSave]);

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

  const updateShortlistingThreshold = (value: number) => {
    setConfig((c: AgentConfig) => ({
      ...c,
      feature_flags: { ...(c.feature_flags ?? {}), shortlisting_tool_threshold: value },
    }));
  };

  const setTools = (tools: ToolEntry[]) => {
    setConfig((c: AgentConfig) => ({ ...c, tools }));
  };

  const updateHomescreen = (field: "isOn" | "greeting", value: boolean | string) => {
    setConfig((c: AgentConfig) => ({
      ...c,
      homescreen: {
        ...(c.homescreen ?? DEFAULT_HOMESCREEN),
        [field]: value,
      },
    }));
  };

  const updateStarter = (index: number, value: string) => {
    setConfig((c: AgentConfig) => {
      const starters = [...(c.homescreen?.starters ?? DEFAULT_HOMESCREEN.starters ?? [])];
      while (starters.length <= index) starters.push("");
      starters[index] = value;
      return {
        ...c,
        homescreen: {
          ...(c.homescreen ?? DEFAULT_HOMESCREEN),
          starters: starters.slice(0, 4),
        },
      };
    });
  };

  const handleImportJson = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = "";
      if (!file) return;
      setImportStatus("idle");
      setImportError(null);
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
          if (raw.homescreen && typeof raw.homescreen === "object") {
            const hs = raw.homescreen as HomescreenConfig;
            out.homescreen = {
              isOn: hs.isOn ?? DEFAULT_HOMESCREEN.isOn,
              greeting: hs.greeting ?? DEFAULT_HOMESCREEN.greeting,
              starters: Array.isArray(hs.starters)
                ? hs.starters.slice(0, 4).filter((s): s is string => typeof s === "string")
                : DEFAULT_HOMESCREEN.starters ?? [],
            };
          }
          setConfig(out);
          setImportStatus("ok");
          setImportError(null);
          setTimeout(() => setImportStatus("idle"), 2500);
        } catch {
          const msg = "Invalid JSON";
          setImportStatus("error");
          setImportError(msg);
          addToast("error", "Import failed", msg);
          setTimeout(() => {
            setImportStatus("idle");
            setImportError(null);
          }, 2500);
        }
      };
      reader.onerror = () => {
        const msg = "Failed to read file";
        setImportStatus("error");
        setImportError(msg);
        addToast("error", "Import failed", msg);
        setTimeout(() => {
          setImportStatus("idle");
          setImportError(null);
        }, 2500);
      };
      reader.readAsText(file);
    },
    [normalizeTools, addToast]
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
        title="CUGA Agent"
        agentContext={agentContext ?? undefined}
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
            <Accordion align="start" size="md">
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

              <AccordionItem title="Welcome Screen">
                  <VStack gap={5}>
                    <FormGroup legendText="">
                      <Checkbox
                        id="homescreen-isOn"
                        labelText="Show welcome screen"
                        checked={config.homescreen?.isOn ?? true}
                        onChange={(_e, { checked }) => updateHomescreen("isOn", !!checked)}
                      />
                    </FormGroup>
                    <FormGroup legendText="">
                      <TextInput
                        id="homescreen-greeting"
                        labelText="Greeting message"
                        value={config.homescreen?.greeting ?? DEFAULT_HOMESCREEN.greeting ?? ""}
                        onChange={(e) => updateHomescreen("greeting", e.target.value)}
                        placeholder="Hello, how can I help you today?"
                      />
                    </FormGroup>
                    <FormGroup legendText="Starter buttons (max 4)">
                      {[0, 1, 2, 3].map((i) => (
                        <TextInput
                          key={i}
                          id={`homescreen-starter-${i}`}
                          labelText={`Starter ${i + 1}`}
                          value={(config.homescreen?.starters ?? [])[i] ?? ""}
                          onChange={(e) => updateStarter(i, e.target.value)}
                          placeholder={i === 0 ? "Hi, what can you do for me?" : "Optional"}
                        />
                      ))}
                    </FormGroup>
                    <Stack gap={3} orientation="horizontal">
                      <Button
                        kind="secondary"
                        size="sm"
                        renderIcon={Save}
                        onClick={() => performDraftSave()}
                        disabled={draftSaving}
                      >
                        {draftSaving ? "Saving…" : "Save welcome screen"}
                      </Button>
                    </Stack>
                  </VStack>
              </AccordionItem>

              <AccordionItem title="Features">
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
                        value={flags.max_steps ?? 70}
                        onChange={(_e: unknown, { value }: { value: number | string }) =>
                          updateMaxSteps(Number(value) || 70)
                        }
                      />
                    </FormGroup>
                    <FormGroup legendText="">
                      <NumberInput
                        id="shortlisting_tool_threshold"
                        label="Shortlisting tool threshold"
                        min={1}
                        max={500}
                        value={flags.shortlisting_tool_threshold ?? 35}
                        onChange={(_e: unknown, { value }: { value: number | string }) =>
                          updateShortlistingThreshold(Number(value) || 35)
                        }
                        helperText="Enable find_tools when total tools exceed this count"
                      />
                    </FormGroup>
                    <Stack gap={3} orientation="horizontal">
                      <Button
                        kind="secondary"
                        size="sm"
                        renderIcon={Save}
                        onClick={() => performDraftSave()}
                        disabled={draftSaving}
                      >
                        {draftSaving ? "Saving…" : "Save Flags"}
                      </Button>
                    </Stack>
                  </VStack>
              </AccordionItem>

              <AccordionItem title="Policies">
                  <Stack gap={3} orientation="vertical">
                    <p className="cds--type-body-compact-01">
                      {policiesEnabled
                        ? `${summary.total} ${summary.total !== 1 ? "policies" : "policy"} defined`
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
                      kind="secondary"
                      size="sm"
                      renderIcon={ShieldIcon}
                      onClick={() => setShowPoliciesModal(true)}
                    >
                      Configure policies
                    </Button>
                  </Stack>
              </AccordionItem>

              <AccordionItem title="Version History">
                  <p className="cds--type-helper-text-01 manage-history-helper">
                    Click a version to set it as your current configuration.
                  </p>
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
                              <span className="manage-tile-action-hint cds--type-helper-text-01">
                                Set as current
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
                      Import
                    </Button>
                    <Button
                      kind="primary"
                      renderIcon={Save}
                      onClick={saveConfig}
                      disabled={saveStatus === "saving"}
                      className="manage-save-bar-button"
                    >
                      {saveStatus === "idle" && "Publish"}
                      {saveStatus === "saving" && "Publishing…"}
                      {saveStatus === "success" && "Published"}
                      {saveStatus === "error" && "Error"}
                    </Button>
                  </div>
                  {(loadError || currentVersion != null || importStatus !== "idle" || draftSaving) && (
                    <div className="manage-save-bar-status">
                      {draftSaving && (
                        <InlineLoading description="Saving draft…" className="manage-draft-saving" />
                      )}
                      {loadError && (
                        <InlineNotification kind="error" title="Error" subtitle={loadError} lowContrast hideCloseButton />
                      )}
                      {!loadError && importStatus === "ok" && (
                        <InlineNotification kind="success" title="Success" subtitle="Config imported" lowContrast hideCloseButton />
                      )}
                      {!loadError && importStatus === "error" && (
                        <InlineNotification kind="error" title="Import failed" subtitle={importError ?? "Import failed"} lowContrast hideCloseButton />
                      )}
                      {!loadError && !draftSaving && currentVersion != null && (
                        <p className="manage-save-bar-version">
                          Version: {currentVersion === "draft" ? "draft" : `v${currentVersion}`}
                          {history.length > 0 && (
                            <span className="manage-save-bar-last-publish">
                              {" · "}
                              Last publish: v{history[0].version}
                              {typeof history[0].created_at === "string" && (
                                <> ({new Date(history[0].created_at).toLocaleDateString()})</>
                              )}
                            </span>
                          )}
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
            <CarbonChat
              contained={true}
              useDraft={true}
              disableHistory={true}
              homescreen={config.homescreen}
            />
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
          onSave={(policies) => setConfig((c) => ({ ...c, policies }))}
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
        <ModalBody>
          {viewVersion && (
            <div className="manage-json-viewer-markdown">
              <Markdown>
                {"```json\n" + JSON.stringify(maskSecrets(viewVersion.config), null, 2) + "\n```"}
              </Markdown>
            </div>
          )}
        </ModalBody>
        {viewVersion && (
          <ModalFooter>
            <Button
              kind="secondary"
              renderIcon={Download}
              onClick={() => {
                const blob = new Blob(
                  [JSON.stringify(maskSecrets(viewVersion.config), null, 2)],
                  { type: "application/json" }
                );
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `config-v${viewVersion.version}.json`;
                a.click();
                URL.revokeObjectURL(url);
              }}
            >
              Download
            </Button>
            <Button
              kind="primary"
              renderIcon={Save}
              onClick={() => {
                const next = { ...DEFAULT_CONFIG, ...viewVersion.config };
                if (Array.isArray(next.tools)) {
                  next.tools = normalizeTools(next.tools);
                }
                setConfig(next);
                setCurrentVersion(viewVersion.version);
                setViewVersion(null);
                addToast("success", "Version loaded", `Version ${viewVersion.version} is now your current configuration`);
              }}
            >
              Use as current
            </Button>
          </ModalFooter>
        )}
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
