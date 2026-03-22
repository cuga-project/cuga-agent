import React, { useState, useEffect, useCallback, useRef } from "react";
import { Link, useParams, useLocation } from "react-router-dom";
import * as api from "./api";
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
  Select,
  SelectItem,
  RadioButtonGroup,
  RadioButton,
  TextArea,
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
import { SecretsManager } from "./SecretsManager";
import type { ToolEntry } from "./types/tools";
import "./ManagePage.css";

export type { ToolEntry } from "./types/tools";

export interface HomescreenConfig {
  isOn?: boolean;
  greeting?: string;
  starters?: string[];
}

export interface AgentConfig {
  agent?: { name?: string; description?: string };
  llm?: {
    provider?: "groq" | "openai" | "litellm";
    api_key?: string;
    auth_type?: "api_key" | "auth_header";
    auth_header_name?: string;
    base_url?: string;
    model?: string;
    temperature?: number;
    disable_ssl?: boolean;
  };
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

const LLM_PROVIDERS = [
  { id: "groq", label: "Groq", defaultModel: "llama-3.3-70b-versatile", defaultBase: "" },
  { id: "openai", label: "OpenAI", defaultModel: "gpt-4o", defaultBase: "" },
  { id: "litellm", label: "LiteLLM", defaultModel: "", defaultBase: "http://localhost:4000" },
] as const;

const DEFAULT_CONFIG: AgentConfig = {
  llm: {
    provider: "openai",
    api_key: "",
    auth_type: "api_key",
    auth_header_name: "Authorization",
    base_url: "",
    model: "",
    temperature: 0.1,
    disable_ssl: false,
  },
  tools: [],
  feature_flags: { enable_todos: false, reflection: false, max_steps: 70, shortlisting_tool_threshold: 35 },
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

function isSecretRef(v: unknown): boolean {
  if (typeof v !== "string") return false;
  return v.startsWith("db://") || v.startsWith("vault://") || v.startsWith("aws://") || v.startsWith("env://");
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
      const isSensitiveField =
        lower === "api_key" ||
        (isAuth && (lower === "value" || lower === "key"));
      const shouldMask = isSensitiveField && typeof v === "string" && v.length > 0 && !isSecretRef(v);
      out[k] = shouldMask ? "••••••••" : maskSecrets(v);
    }
    return out;
  }
  return obj;
}

export function ManagePage() {
  const { agentId } = useParams<{ agentId: string }>();
  const location = useLocation();
  const search = location.search || "";
  const [llmConfig, setLlmConfig] = useState<NonNullable<AgentConfig["llm"]>>(DEFAULT_CONFIG.llm!);
  const [tools, setToolsState] = useState<ToolEntry[]>(DEFAULT_CONFIG.tools ?? []);
  const [featureFlags, setFeatureFlags] = useState(DEFAULT_CONFIG.feature_flags!);
  const [homescreen, setHomescreen] = useState<HomescreenConfig>(DEFAULT_CONFIG.homescreen ?? DEFAULT_HOMESCREEN);
  const [policies, setPolicies] = useState<NonNullable<AgentConfig["policies"]>>(DEFAULT_CONFIG.policies ?? { enablePolicies: true, policies: [] });
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
  const [agentName, setAgentName] = useState("");
  const [agentDescription, setAgentDescription] = useState("");
  const [secretsModalOpen, setSecretsModalOpen] = useState(false);
  const [llmUseSavedSecret, setLlmUseSavedSecret] = useState(false);
  const [llmSecretsList, setLlmSecretsList] = useState<{ id: string; description?: string; ref: string }[]>([]);
  const [llmForceEnv, setLlmForceEnv] = useState(false);
  const [llmSecretsMode, setLlmSecretsMode] = useState<string>("local");
  const [llmInlineCreate, setLlmInlineCreate] = useState(false);
  const [llmInlineCreateValue, setLlmInlineCreateValue] = useState("");
  const [llmInlineCreateKey, setLlmInlineCreateKey] = useState("");
  const [llmModelsLoading, setLlmModelsLoading] = useState(false);
  const [llmModelsError, setLlmModelsError] = useState<string | null>(null);
  const [llmModelsList, setLlmModelsList] = useState<string[]>([]);
  const skipDraftSaveRef = useRef(true);
  const draftSaveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const toolsSaveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const llmBlurSaveRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const llmConfigRef = useRef(llmConfig);
  llmConfigRef.current = llmConfig;

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

  const effectiveAgentId = agentId ?? "cuga-default";

  const loadLatest = useCallback(async () => {
    try {
      skipDraftSaveRef.current = true;
      const [draftRes, toolsListRes] = await Promise.all([
        api.getManageConfig(true, effectiveAgentId),
        api.getToolsList(true),
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
            if (out.policies !== undefined && out.policies && typeof out.policies === "object") {
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
            if (data.config.agent && typeof data.config.agent === "object") {
              const ag = data.config.agent as { name?: string; description?: string };
              setAgentName(ag.name ?? "");
              setAgentDescription(ag.description ?? "");
            }
            if (data.config.feature_flags && typeof data.config.feature_flags === "object") {
              out.feature_flags = { ...DEFAULT_CONFIG.feature_flags!, ...data.config.feature_flags };
            }
          }
          version = data.version === "draft" ? "draft" : (data.version ?? null);
        }
      }
      if (version === null) {
        const publishedRes = await api.getManageConfig(false, effectiveAgentId);
        if (publishedRes.ok) {
          const data = await publishedRes.json();
          if (data.config && Object.keys(data.config).length > 0) {
            Object.assign(out, data.config);
            if (Array.isArray(out.tools)) {
              out.tools = normalizeTools(out.tools);
            }
            if (out.policies !== undefined && out.policies && typeof out.policies === "object") {
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
            if (data.config.agent && typeof data.config.agent === "object") {
              const ag = data.config.agent as { name?: string; description?: string };
              setAgentName(ag.name ?? "");
              setAgentDescription(ag.description ?? "");
            }
            if (data.config.feature_flags && typeof data.config.feature_flags === "object") {
              out.feature_flags = { ...DEFAULT_CONFIG.feature_flags!, ...data.config.feature_flags };
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
      setLlmConfig(out.llm ?? DEFAULT_CONFIG.llm!);
      setToolsState(Array.isArray(out.tools) ? out.tools : []);
      setFeatureFlags(out.feature_flags ?? DEFAULT_CONFIG.feature_flags!);
      setHomescreen(out.homescreen ?? DEFAULT_HOMESCREEN);
      setPolicies(out.policies ?? { enablePolicies: true, policies: [] });
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
  }, [normalizeTools, addToast, effectiveAgentId]);

  const loadHistory = useCallback(async () => {
    try {
      const res = await api.getManageConfigHistory(effectiveAgentId);
      if (res.ok) {
        const data = await res.json();
        setHistory(data.versions || []);
      }
    } catch {
      setHistory([]);
    }
  }, [effectiveAgentId]);

  const refreshSecrets = useCallback(async () => {
    try {
      const [secretsRes, configRes] = await Promise.all([
        api.getSecrets(effectiveAgentId),
        api.getSecretsConfig(),
      ]);
      let mode = "local";
      if (configRes.ok) {
        const cfg = await configRes.json();
        setLlmForceEnv(!!cfg.force_env);
        mode = cfg.mode || "local";
      }
      setLlmSecretsMode(mode);
      if (secretsRes.ok) {
        const data = await secretsRes.json();
        const raw: { id: string; description?: string; source?: string }[] = data.secrets || data.overrides || [];
        setLlmSecretsList(raw.map((s) => ({
          id: s.id,
          description: s.description,
          ref: s.source === "vault" || mode === "vault"
            ? `vault://secret/${s.id}#value`
            : s.source === "env"
              ? s.id
              : s.source === "aws"
                ? `aws://${s.id}`
                : `db://${s.id}`,
        })));
      }
    } catch {}
  }, [effectiveAgentId]);

  useEffect(() => {
    refreshSecrets();
  }, [refreshSecrets]);

  useEffect(() => {
    const key = llmConfig?.api_key ?? "";
    setLlmUseSavedSecret(
      typeof key === "string" && (key.startsWith("db://") || key.startsWith("vault://") || key.startsWith("aws://"))
    );
  }, [llmConfig?.api_key]);

  useEffect(() => {
    loadLatest();
    loadHistory();
  }, [loadLatest, loadHistory]);

  const assembleConfig = useCallback(
    (overrides?: Partial<AgentConfig>): AgentConfig => {
      const c: AgentConfig = {
        agent: { name: agentName, description: agentDescription || undefined },
        llm: llmConfig,
        tools: tools,
        feature_flags: featureFlags,
        homescreen,
        policies,
      };
      return overrides ? { ...c, ...overrides } : c;
    },
    [agentName, agentDescription, llmConfig, tools, featureFlags, homescreen, policies]
  );

  const performDraftSave = useCallback(
    async (partial?: Partial<AgentConfig>) => {
      const toSave = partial ? { ...assembleConfig(), ...partial } : assembleConfig();
      setDraftSaving(true);
      try {
        const res = await api.postManageConfigDraft(toSave, effectiveAgentId);
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
    },
    [addToast, assembleConfig]
  );

  const saveLlmDraft = useCallback(async () => {
    setDraftSaving(true);
    try {
      const res = await api.patchManageConfigDraftLlm(llmConfigRef.current, effectiveAgentId);
      setDraftSaving(false);
      if (res.ok) {
        setCurrentVersion("draft");
        addToast("success", "Draft saved", "LLM settings saved to draft");
      } else {
        addToast("error", "Draft Save Failed", `Failed to save LLM (${res.status} ${res.statusText})`);
      }
    } catch (error) {
      setDraftSaving(false);
      addToast("error", "Draft Save Failed", error instanceof Error ? error.message : "Network error");
    }
  }, [addToast, effectiveAgentId]);

  const scheduleLlmDraftSave = useCallback(() => {
    if (llmBlurSaveRef.current) clearTimeout(llmBlurSaveRef.current);
    llmBlurSaveRef.current = setTimeout(() => {
      llmBlurSaveRef.current = null;
      saveLlmDraft();
    }, 100);
  }, [saveLlmDraft]);

  const saveAgentDraft = useCallback(async () => {
    setDraftSaving(true);
    try {
      const res = await api.patchManageConfigDraftAgent(
        { name: agentName.trim(), description: agentDescription.trim() || undefined },
        effectiveAgentId
      );
      setDraftSaving(false);
      if (res.ok) {
        setCurrentVersion("draft");
        addToast("success", "Draft saved", "Agent settings saved to draft");
      } else {
        addToast("error", "Draft Save Failed", `Failed to save agent (${res.status} ${res.statusText})`);
      }
    } catch (error) {
      setDraftSaving(false);
      addToast("error", "Draft Save Failed", error instanceof Error ? error.message : "Network error");
    }
  }, [agentName, agentDescription, addToast, effectiveAgentId]);

  useEffect(() => {
    if (skipDraftSaveRef.current) return;
    const t = setTimeout(() => {
      toolsSaveTimeoutRef.current = null;
      (async () => {
        setDraftSaving(true);
        try {
          const res = await api.patchManageConfigDraftTools(tools, effectiveAgentId);
          setDraftSaving(false);
          if (res.ok) {
            setCurrentVersion("draft");
            const data = await res.json().catch(() => ({}));
            if (data.status === "partial" && data.tool_errors) {
              Object.entries(data.tool_errors as Record<string, { error?: string; message?: string }>).forEach(
                ([toolName, err]) => addToast("warning", `Tool: ${toolName}`, err?.error || err?.message || "Unknown error")
              );
            } else {
              addToast("success", "Draft saved", "Tools saved to draft");
            }
          } else {
            addToast("error", "Draft Save Failed", `Failed to save tools (${res.status} ${res.statusText})`);
          }
        } catch (error) {
          setDraftSaving(false);
          addToast("error", "Draft Save Failed", error instanceof Error ? error.message : "Network error");
        }
      })();
    }, 500);
    toolsSaveTimeoutRef.current = t;
    return () => {
      if (toolsSaveTimeoutRef.current) clearTimeout(toolsSaveTimeoutRef.current);
    };
  }, [tools, effectiveAgentId, addToast]);


  useEffect(() => {
    if (importStatus === "ok") {
      performDraftSave();
    }
  }, [importStatus, performDraftSave]);

  const loadVersion = async (version: number) => {
    try {
      const res = await api.getManageConfigVersion(String(version), effectiveAgentId);
      if (res.ok) {
        const data = await res.json();
        const next = { ...DEFAULT_CONFIG, ...data.config };
        if (Array.isArray(next.tools)) {
          next.tools = normalizeTools(next.tools);
        }
        const ag = next.agent;
        setAgentName(ag?.name ?? "");
        setAgentDescription(ag?.description ?? "");
        setLlmConfig(next.llm ?? DEFAULT_CONFIG.llm!);
        setToolsState(Array.isArray(next.tools) ? next.tools : []);
        setFeatureFlags(next.feature_flags ?? DEFAULT_CONFIG.feature_flags!);
        setHomescreen(next.homescreen ?? DEFAULT_HOMESCREEN);
        setPolicies(next.policies ?? { enablePolicies: true, policies: [] });
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
    if (!agentName.trim()) {
      addToast("error", "Agent name required", "Please enter an agent name before publishing.");
      return;
    }
    setSaveStatus("saving");
    try {
      let toSave = assembleConfig();
      if (!toSave.policies) {
        toSave = { ...toSave, policies: { enablePolicies: true, policies: [] } };
      }
      const res = await api.postManageConfig(toSave, effectiveAgentId);
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
        setCurrentVersion(typeof data.version === "number" ? data.version : "draft");
        setSaveStatus("success");
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
          errorMsg = errorData.detail || errorData.error || errorData.message || errorMsg;
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

  const updateLlm = (field: keyof NonNullable<AgentConfig["llm"]>, value: string | number | boolean) => {
    setLlmConfig((c) => ({ ...(c ?? {}), [field]: value }));
  };
  const updateLlmTemperature = (value: number) => {
    setLlmConfig((c) => ({ ...(c ?? {}), temperature: value }));
  };

  const updateFeatureFlag = (field: "enable_todos" | "reflection", value: boolean) => {
    setFeatureFlags((c) => ({ ...(c ?? {}), [field]: value }));
  };

  const updateMaxSteps = (value: number) => {
    setFeatureFlags((c) => ({ ...(c ?? {}), max_steps: value }));
  };

  const updateShortlistingThreshold = (value: number) => {
    setFeatureFlags((c) => ({ ...(c ?? {}), shortlisting_tool_threshold: value }));
  };

  const setTools = useCallback((newTools: ToolEntry[]) => {
    setToolsState(newTools);
  }, []);

  const updateHomescreen = (field: "isOn" | "greeting", value: boolean | string) => {
    setHomescreen((c) => ({ ...(c ?? DEFAULT_HOMESCREEN), [field]: value }));
  };

  const updateStarter = (index: number, value: string) => {
    setHomescreen((c) => {
      const starters = [...(c?.starters ?? DEFAULT_HOMESCREEN.starters ?? [])];
      while (starters.length <= index) starters.push("");
      starters[index] = value;
      return { ...(c ?? DEFAULT_HOMESCREEN), starters: starters.slice(0, 4) };
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
          setLlmConfig(out.llm ?? DEFAULT_CONFIG.llm!);
          setToolsState(Array.isArray(out.tools) ? out.tools : []);
          setFeatureFlags(out.feature_flags ?? DEFAULT_CONFIG.feature_flags!);
          setHomescreen(out.homescreen ?? DEFAULT_HOMESCREEN);
          setPolicies(out.policies ?? { enablePolicies: true, policies: [] });
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

  const llm = llmConfig ?? {};
  const flags = featureFlags ?? {};
  const policiesList = policies?.policies ?? [];
  const summary = policiesSummary(policiesList);
  const policiesEnabled = policies?.enablePolicies ?? false;

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
        onOpenSecrets={() => setSecretsModalOpen(true)}
      />

      <div className="manage-layout">
        <div className="manage-config-panel">
          <div className="manage-config-scroll">
            <Layer withBackground>
            <Accordion align="start" size="md">
              <AccordionItem title="Agent" open>
                <VStack gap={5}>
                  <FormGroup legendText="Name (required)" className="manage-agent-name-group">
                    <TextInput
                      id="agent-name"
                      labelText=""
                      value={agentName}
                      onChange={(e) => setAgentName(e.target.value)}
                      onBlur={() => saveAgentDraft()}
                      placeholder="Enter agent name"
                      invalid={!agentName.trim()}
                      invalidText="Name is required"
                      required
                    />
                  </FormGroup>
                  <FormGroup legendText="Description">
                    <TextArea
                      id="agent-description"
                      labelText=""
                      value={agentDescription}
                      onChange={(e) => setAgentDescription(e.target.value)}
                      onBlur={() => saveAgentDraft()}
                      placeholder="Optional description"
                      rows={3}
                    />
                  </FormGroup>
                </VStack>
              </AccordionItem>
              <AccordionItem title="LLM Configuration" open>
                  {llmSecretsMode === "local" && llmForceEnv ? (
                    <InlineNotification
                      kind="info"
                      title="Managed via environment"
                      subtitle="LLM configuration is controlled by settings.toml and environment variables (mode=local + force_env=true). No UI configuration is needed."
                      lowContrast
                      hideCloseButton
                    />
                  ) : (
                  <VStack gap={5} className="manage-llm-fields">
                    <FormGroup legendText="Provider">
                      <Select
                        id="llm-provider"
                        value={llm.provider ?? "openai"}
                        onChange={(e) => {
                          const id = (e.target.value || "openai") as "groq" | "openai" | "litellm";
                          const prov = LLM_PROVIDERS.find((p) => p.id === id);
                          setLlmConfig((prev) => {
                            const next = { ...(prev ?? {}), provider: id };
                            if (id === "groq") {
                              next.base_url = "";
                            } else if (prov && (!prev?.model || !prev?.base_url) && (prov.defaultBase || prov.defaultModel)) {
                              if (!prev?.model && prov.defaultModel) next.model = prov.defaultModel;
                              if (!prev?.base_url && prov.defaultBase !== undefined) next.base_url = prov.defaultBase;
                            }
                            return next;
                          });
                          setTimeout(() => saveLlmDraft(), 0);
                        }}
                      >
                        {LLM_PROVIDERS.map((p) => (
                          <SelectItem key={p.id} value={p.id} text={p.label} />
                        ))}
                      </Select>
                    </FormGroup>
                    <FormGroup legendText="Auth type">
                      <RadioButtonGroup
                        name="llm-auth-type"
                        valueSelected={llm.auth_type ?? "api_key"}
                        onChange={(selection) => { updateLlm("auth_type", (selection ?? "api_key") as "api_key" | "auth_header"); setTimeout(saveLlmDraft, 0); }}
                        orientation="horizontal"
                      >
                        <RadioButton labelText="API Key" value="api_key" id="llm-auth-api-key" />
                        <RadioButton labelText="Auth header" value="auth_header" id="llm-auth-header" />
                      </RadioButtonGroup>
                      {(llm.auth_type ?? "api_key") === "auth_header" && (
                        <TextInput
                          id="llm-auth-header-name"
                          labelText="Header name"
                          value={llm.auth_header_name ?? "Authorization"}
                          onChange={(e) => updateLlm("auth_header_name", e.target.value)}
                          onBlur={scheduleLlmDraftSave}
                          placeholder="Authorization"
                          style={{ marginTop: "0.5rem" }}
                        />
                      )}
                    </FormGroup>
                    <FormGroup legendText="">
                      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                        <Checkbox
                          id="llm-use-saved-secret"
                          labelText="Use saved secret"
                          checked={llmUseSavedSecret}
                          onChange={(_e, { checked }) => {
                            setLlmUseSavedSecret(!!checked);
                            setLlmInlineCreate(false);
                          }}
                        />
                        <Button kind="ghost" size="sm" hasIconOnly iconDescription="Manage secrets" renderIcon={KeyIcon} onClick={() => setSecretsModalOpen(true)} />
                      </div>
                      {llmUseSavedSecret ? (
                        <>
                          <Select
                            id="llm-api-key-secret"
                            labelText={llm.auth_type === "auth_header" ? "Header value (saved secret)" : "API Key (saved secret)"}
                            value={llm.api_key ?? ""}
                            onChange={(e) => { updateLlm("api_key", e.target.value); setTimeout(saveLlmDraft, 0); }}
                          >
                            <SelectItem value="" text="Select a secret" />
                            {llmSecretsList.map((s) => (
                              <SelectItem
                                key={s.id}
                                value={s.ref}
                                text={s.description ? `${s.id} — ${s.description}` : s.id}
                              />
                            ))}
                          </Select>
                          <Button
                            kind="ghost"
                            size="sm"
                            renderIcon={KeyIcon}
                            style={{ marginTop: "0.5rem" }}
                            onClick={() => setLlmInlineCreate((v) => !v)}
                          >
                            {llmInlineCreate ? "Cancel" : "Create new secret"}
                          </Button>
                          {llmInlineCreate && (
                            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", marginTop: "0.5rem" }}>
                              <TextInput
                                id="llm-inline-secret-key"
                                type="text"
                                labelText="Key name"
                                value={llmInlineCreateKey}
                                onChange={(e) => setLlmInlineCreateKey(e.target.value)}
                                placeholder="e.g. llm-api-key"
                                helperText="Optional; leave empty to auto-generate"
                              />
                              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "flex-end" }}>
                                <TextInput
                                  id="llm-inline-secret-value"
                                  type="password"
                                  labelText="New secret value"
                                  value={llmInlineCreateValue}
                                  onChange={(e) => setLlmInlineCreateValue(e.target.value)}
                                  placeholder="sk-..."
                                  autoComplete="off"
                                />
                                <Button
                                  size="sm"
                                  style={{ marginTop: "auto" }}
                                  disabled={!llmInlineCreateValue.trim()}
                                  onClick={async () => {
                                    const slug = llmInlineCreateKey.trim().toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "-") || `llm-api-key-${Date.now()}`;
                                    const res = await api.createSecret(slug, llmInlineCreateValue.trim(), "LLM API Key", undefined, effectiveAgentId);
                                  if (res.ok) {
                                    const data = await res.json();
                                    const ref = data.ref || `db://${slug}`;
                                    setLlmInlineCreate(false);
                                    setLlmInlineCreateValue("");
                                    setLlmInlineCreateKey("");
                                    // Refresh list first so the new secret is available in the dropdown
                                    await refreshSecrets();
                                    // Then select it and persist
                                    updateLlm("api_key", ref);
                                    setTimeout(saveLlmDraft, 0);
                                  }
                                }}
                              >
                                Save
                              </Button>
                            </div>
                          </div>
                          )}
                        </>
                      ) : (
                        <TextInput
                          type="password"
                          id="llm-api-key"
                          labelText={llm.auth_type === "auth_header" ? "Header value" : "API Key"}
                          value={(llm.api_key ?? "").startsWith("db://") ? "" : (llm.api_key ?? "")}
                          onChange={(e) => updateLlm("api_key", e.target.value)}
                          onBlur={scheduleLlmDraftSave}
                          placeholder="sk-..."
                        />
                      )}
                    </FormGroup>
                    {/* Groq uses its own fixed endpoint — no base URL needed.
                        OpenAI defaults to api.openai.com but allow override if already set.
                        LiteLLM always requires one. */}
                    {(llm.provider === "litellm" || !["groq"].includes(llm.provider ?? "")) && (
                    <FormGroup legendText="">
                      <TextInput
                        type="text"
                        id="llm-base-url"
                        labelText="Base URL"
                        value={llm.base_url ?? ""}
                        onChange={(e) => updateLlm("base_url", e.target.value)}
                        onBlur={scheduleLlmDraftSave}
                        placeholder={llm.provider === "litellm" ? "http://localhost:4000" : "https://api.openai.com/v1"}
                        helperText={llm.provider === "litellm" ? "Required for LiteLLM proxy" : "Optional; leave empty for default"}
                      />
                    </FormGroup>
                    )}
                    <FormGroup legendText="">
                      <Checkbox
                        id="llm-disable-ssl"
                        labelText="Disable SSL verification"
                        checked={!!llm.disable_ssl}
                        onChange={(_e, { checked }) => { updateLlm("disable_ssl", !!checked); setTimeout(saveLlmDraft, 0); }}
                      />
                    </FormGroup>
                    <FormGroup legendText="">
                      <div style={{ display: "flex", alignItems: "flex-end", gap: "0.5rem", flexWrap: "wrap" }}>
                        <TextInput
                          type="text"
                          id="llm-model"
                          labelText="Model"
                          value={llm.model ?? ""}
                          onChange={(e) => updateLlm("model", e.target.value)}
                          onBlur={scheduleLlmDraftSave}
                          placeholder="gpt-4o"
                          style={{ flex: "1", minWidth: "12rem" }}
                        />
                        <Button
                          kind="ghost"
                          size="md"
                          disabled={llmModelsLoading}
                          onClick={async () => {
                            setLlmModelsError(null);
                            setLlmModelsList([]);
                            setLlmModelsLoading(true);
                            try {
                              const res = await api.getLlmModels(
                                llm.api_key ?? "",
                                !!llm.disable_ssl,
                                llm.provider
                              );
                              if (!res.ok) {
                                const err = await res.json().catch(() => ({}));
                                throw new Error(err.detail ?? err.message ?? `${res.status} ${res.statusText}`);
                              }
                              const data = await res.json();
                              setLlmModelsList(Array.isArray(data.models) ? data.models : []);
                            } catch (e) {
                              setLlmModelsError(e instanceof Error ? e.message : String(e));
                            } finally {
                              setLlmModelsLoading(false);
                            }
                          }}
                        >
                          {llmModelsLoading ? "Loading…" : "List models"}
                        </Button>
                      </div>
                      {llmModelsLoading && <InlineLoading description="Fetching models…" />}
                      {llmModelsError && (
                        <InlineNotification kind="error" title="Error" subtitle={llmModelsError} lowContrast hideCloseButton style={{ marginTop: "0.5rem" }} />
                      )}
                      {llmModelsList.length > 0 && (
                        <Select
                          id="llm-model-select"
                          labelText="Choose from list"
                          value={llm.model ?? ""}
                          onChange={(e) => { updateLlm("model", e.target.value); setTimeout(saveLlmDraft, 0); }}
                          style={{ marginTop: "0.5rem" }}
                        >
                          <SelectItem value="" text="—" />
                          {llmModelsList.map((id) => (
                            <SelectItem key={id} value={id} text={id} />
                          ))}
                        </Select>
                      )}
                    </FormGroup>
                    <FormGroup legendText="">
                      <NumberInput
                        id="llm-temperature"
                        label="Temperature"
                        min={0}
                        max={2}
                        step={0.1}
                        value={llm.temperature ?? 0.1}
                        onChange={(_e: unknown, { value }: { value: number | string }) =>
                          updateLlmTemperature(Number(value) || 0.1)
                        }
                        onBlur={scheduleLlmDraftSave}
                      />
                    </FormGroup>
                  </VStack>
                  )}
              </AccordionItem>

              <AccordionItem title="Tools" open>
                  <ToolsConfig
                    tools={tools}
                    onChange={setTools}
                    connectedApps={connectedApps}
                    connectedTools={connectedTools}
                    agentId={effectiveAgentId}
                    onError={(title, message) => addToast("error", title, message)}
                    onOpenSecrets={() => setSecretsModalOpen(true)}
                  />
              </AccordionItem>

              <AccordionItem title="Welcome Screen">
                  <VStack gap={5}>
                    <FormGroup legendText="">
                      <Checkbox
                        id="homescreen-isOn"
                        labelText="Show welcome screen"
                        checked={homescreen?.isOn ?? true}
                        onChange={(_e, { checked }) => {
                          updateHomescreen("isOn", !!checked);
                          setTimeout(() => performDraftSave(), 0);
                        }}
                      />
                    </FormGroup>
                    <FormGroup legendText="">
                      <TextInput
                        id="homescreen-greeting"
                        labelText="Greeting message"
                        value={homescreen?.greeting ?? DEFAULT_HOMESCREEN.greeting ?? ""}
                        onChange={(e) => updateHomescreen("greeting", e.target.value)}
                        onBlur={() => performDraftSave()}
                        placeholder="Hello, how can I help you today?"
                      />
                    </FormGroup>
                    <FormGroup legendText="Starter buttons (max 4)">
                      {[0, 1, 2, 3].map((i) => (
                        <TextInput
                          key={i}
                          id={`homescreen-starter-${i}`}
                          labelText={`Starter ${i + 1}`}
                          value={(homescreen?.starters ?? [])[i] ?? ""}
                          onChange={(e) => updateStarter(i, e.target.value)}
                          onBlur={() => performDraftSave()}
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
                        checked={flags.enable_todos ?? false}
                        onChange={(_e, { checked }) => {
                          updateFeatureFlag("enable_todos", !!checked);
                          setTimeout(() => performDraftSave(), 0);
                        }}
                      />
                    </FormGroup>
                    <FormGroup legendText="">
                      <Checkbox
                        id="reflection"
                        labelText="Reflection"
                        checked={flags.reflection ?? false}
                        onChange={(_e, { checked }) => {
                          updateFeatureFlag("reflection", !!checked);
                          setTimeout(() => performDraftSave(), 0);
                        }}
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
                        onBlur={() => performDraftSave()}
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
                        onBlur={() => performDraftSave()}
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
                                api.getManageConfigVersion(String(v.version), effectiveAgentId)
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
              homescreen={homescreen}
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

      <SecretsManager open={secretsModalOpen} onClose={() => { setSecretsModalOpen(false); refreshSecrets(); }} agentId={effectiveAgentId} />

      {showPoliciesModal && (
        <PoliciesConfig
          draftMode={true}
          onClose={() => setShowPoliciesModal(false)}
          onSave={(policies) => setPolicies(policies)}
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
                setLlmConfig(next.llm ?? DEFAULT_CONFIG.llm!);
                setToolsState(Array.isArray(next.tools) ? next.tools : []);
                setFeatureFlags(next.feature_flags ?? DEFAULT_CONFIG.feature_flags!);
                setHomescreen(next.homescreen ?? DEFAULT_HOMESCREEN);
                setPolicies(next.policies ?? { enablePolicies: true, policies: [] });
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
