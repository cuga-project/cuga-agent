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
  Tooltip,
} from "@carbon/react";
import { CugaHeader } from "./CugaHeader";
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
  SkillLevel as SkillIcon,
  Package as PackageIcon,
} from "@carbon/icons-react";
import Markdown from "@carbon/ai-chat-components/es/react/markdown.js";
import CarbonChat from "./carbon-chat/CarbonChat";
import PoliciesConfig from "agentic_chat/PoliciesConfig";
import KnowledgePanel from "agentic_chat/KnowledgePanel";
import VariablesSidebar from "agentic_chat/VariablesSidebar";
import { ToolsConfig, type ConnectedApp, type ConnectedTool } from "./ToolsConfig";
import { SecretsManager } from "./SecretsManager";
import type { ToolEntry } from "./types/tools";
import type { KnowledgeAttachmentSnapshot } from "./knowledge/useSessionKnowledgeAttachments";
import { useLlmDraftSave } from "./manage/hooks/useLlmDraftSave";
import { useAgentDraftSave } from "./manage/hooks/useAgentDraftSave";
import { useSpecialInstructionsDraftSave } from "./manage/hooks/useSpecialInstructionsDraftSave";
import { useToolsDraftSave } from "./manage/hooks/useToolsDraftSave";
import { useKnowledgeDraftSave, type AdaptationServerErrorShape } from "./manage/hooks/useKnowledgeDraftSave";
import { useFullDraftSave } from "./manage/hooks/useFullDraftSave";
import { usePublishConfig } from "./manage/hooks/usePublishConfig";
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
    enable_filesystem_tools?: boolean;
    max_steps?: number;
    shortlisting_tool_threshold?: number;
    builtin_tools?: string[];
  };
  special_instructions?: string;
  policies?: { enablePolicies: boolean; policies: unknown[] };
  homescreen?: HomescreenConfig;
  // Knowledge config shape MUST stay in sync with ``KnowledgeConfigValues``
  // in agentic_chat/src/KnowledgeConfig.tsx — they describe the same wire
  // payload (the PATCH /api/manage/config/draft/knowledge body).
  // Drifts have caused silent field-drop bugs on version-load + profile-
  // pick (Sami C3): if a field is in KnowledgeConfigValues but missing
  // here, the version-history hydrator drops it on the floor and the
  // profile-onClick writes-without-type-error escape ``tsc -b``.
  knowledge?: {
    enabled?: boolean;
    agent_level_enabled?: boolean;
    session_level_enabled?: boolean;
    rag_profile?: string;
    embedding_provider?: string;
    embedding_model?: string;
    embedding_api_key?: string;
    embedding_base_url?: string;
    embedding_extra_params?: Record<string, string | number | boolean>;
    embedding_batch_size?: number;
    embedding_concurrency?: number;
    use_gpu?: boolean;
    chunk_size?: number;
    chunk_overlap?: number;
    metric_type?: string;
    max_pending_tasks?: number;
    max_upload_size_mb?: number;
    max_url_download_size_mb?: number;
    max_files_per_request?: number;
    max_chunks_per_document?: number;
    // Engine-side knobs surfaced by profiles
    vector_insert_batch_size?: number;
    max_ingest_workers?: number;
    // Docling
    docling_pdf_mode?: string;
    docling_layout_engine?: string;
    docling_drop_page_chrome?: string;
    // Reranker
    rerank_enabled?: boolean;
    rerank_top_k_in?: number;
    rerank_model?: string;
    // Search-side
    search_hybrid_mode?: string;
    search_junk_filter?: string;
    search_query_transform?: string;
    max_search_attempts?: number;
    default_limit?: number;
    default_score_threshold?: number;
    // Client adaptation (prompt rules + glossary)
    client_adaptation_text?: string;
    client_adaptation_glossary?: { term: string; definition?: string }[];
  };
}

// Mirrors the dataclass defaults in src/cuga/backend/knowledge/config.py
// — SDK users constructing a bare ``KnowledgeConfig()`` get the same
// shape. Profile overrides (standard / balanced / max_quality) layer on
// top via the profile loader.
const DEFAULT_KNOWLEDGE_CONFIG: NonNullable<AgentConfig["knowledge"]> = {
  enabled: false,
  agent_level_enabled: true,
  session_level_enabled: true,
  rag_profile: "standard",
  embedding_provider: "huggingface",
  embedding_model: "",
  embedding_api_key: "",
  embedding_base_url: "",
  embedding_extra_params: {},
  embedding_batch_size: 64,
  embedding_concurrency: 4,
  use_gpu: true,
  chunk_size: 1000,
  chunk_overlap: 200,
  metric_type: "COSINE",
  max_pending_tasks: 10,
  max_upload_size_mb: 100,
  max_url_download_size_mb: 50,
  max_files_per_request: 10,
  max_chunks_per_document: 10000,
  vector_insert_batch_size: 200,
  max_ingest_workers: 2,
  docling_pdf_mode: "accurate",
  docling_layout_engine: "auto",
  docling_drop_page_chrome: "dry_run",
  rerank_enabled: false,
  rerank_top_k_in: 20,
  rerank_model: "BAAI/bge-reranker-base",
  search_hybrid_mode: "auto",
  search_junk_filter: "enforce",
  search_query_transform: "off",
  max_search_attempts: 3,
  default_limit: 10,
  default_score_threshold: 0.0,
  client_adaptation_text: "",
  client_adaptation_glossary: [],
};

// "Effective index equivalence" for the Re-index banner. Two configs are
// equivalent for the vector index when every field contributing to the
// engine's ``vector_config_hash`` resolves to the same effective value:
//
//   - embedding_provider, embedding_model, chunk_size, chunk_overlap, metric_type
//
// Special case: ``embedding_model = ""`` is the Provider Select's reset
// value, meaning "use this provider's default". On the SAME provider the
// engine resolves it to the same default the saved snapshot already
// captured — so empty current model = match. Without this normalisation,
// reverting via the Select keeps the Re-index banner stuck up forever.
//
// Other fields (numeric chunking, enum metric_type) never produce empty
// values via the UI today; if a future UI surface introduces one, add
// the same empty-means-default branch here. One place to extend.
function isIndexConfigEquivalent(
  current: NonNullable<AgentConfig["knowledge"]>,
  saved: NonNullable<AgentConfig["knowledge"]>,
): boolean {
  if (current.embedding_provider !== saved.embedding_provider) return false;
  // Empty current model = "use provider default". Treat as a match ONLY when
  // saved is also empty; if saved pinned a specific model, clearing it IS a
  // change (review) — the prior rule returned equivalent for empty-vs-anything
  // and silently hid the re-index banner + Live divergence.
  if (current.embedding_model) {
    if (current.embedding_model !== saved.embedding_model) return false;
  } else if (saved.embedding_model) {
    return false;
  }
  if (current.chunk_size !== saved.chunk_size) return false;
  if (current.chunk_overlap !== saved.chunk_overlap) return false;
  if (current.metric_type !== saved.metric_type) return false;
  return true;
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

const TEXT_EXTENSIONS = [".txt", ".md", ".json", ".csv", ".html", ".xml", ".yaml", ".yml", ".py"];

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
  const effectiveAgentId = agentId ?? "cuga-default";
  const location = useLocation();
  const search = location.search || "";
  const [llmConfig, setLlmConfig] = useState<NonNullable<AgentConfig["llm"]>>(DEFAULT_CONFIG.llm!);
  const [tools, setToolsState] = useState<ToolEntry[]>(DEFAULT_CONFIG.tools ?? []);
  const [featureFlags, setFeatureFlags] = useState(DEFAULT_CONFIG.feature_flags!);
  const [homescreen, setHomescreen] = useState<HomescreenConfig>(DEFAULT_CONFIG.homescreen ?? DEFAULT_HOMESCREEN);
  const [policies, setPolicies] = useState<NonNullable<AgentConfig["policies"]>>(DEFAULT_CONFIG.policies ?? { enablePolicies: true, policies: [] });
  const [history, setHistory] = useState<ConfigVersion[]>([]);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "success" | "error">("idle");
  // Live-config truth anchor. Sourced from GET /api/manage/config
  // (published=true) on mount and after every successful Publish — never
  // from optimistic client state. The pill in the header reads this so
  // the user ALWAYS knows what's actually serving production traffic,
  // independent of what the draft has been edited to. The 6-expert
  // synthesis identified this as the most important addition: without
  // it, no UI surface answers "what is actually running right now?"
  // without log-reading.
  const [liveKnowledge, setLiveKnowledge] = useState<
    {
      provider: string;
      model: string;
      version: number | null;
      // Published chunking/metric — so the Live pill's diverged check compares
      // draft against the ACTUAL published values, not against itself.
      chunk_size?: number;
      chunk_overlap?: number;
      metric_type?: string;
    } | null
  >(null);
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
  const [agentContext, setAgentContext] = useState<{
    agent_id: string;
    config_version: number | null;
    skills_enabled?: boolean;
    workspace_filesystem_root?: string;
    knowledge_enabled?: boolean;
    agent_level_knowledge_enabled?: boolean;
    session_level_knowledge_enabled?: boolean;
  } | null>(null);
  const [agentName, setAgentName] = useState("");
  const [agentDescription, setAgentDescription] = useState("");
  const [specialInstructions, setSpecialInstructions] = useState("");
  const [secretsModalOpen, setSecretsModalOpen] = useState(false);
  const [skills, setSkills] = useState<Array<{ name: string; description: string; requirements: string[]; source: string }>>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [expandedSkills, setExpandedSkills] = useState<Set<string>>(new Set());
  const [showKnowledgeModal, setShowKnowledgeModal] = useState(false);
  const [knowledgeHealthy, setKnowledgeHealthy] = useState<boolean | null>(null);
  const [knowledgeHealthStatus, setKnowledgeHealthStatus] = useState<string>("unknown");
  const [knowledgeDocCount, setKnowledgeDocCount] = useState(0);
  const [knowledgeDocsVersion, setKnowledgeDocsVersion] = useState(0);
  const [knowledgeConfig, setKnowledgeConfig] = useState<NonNullable<AgentConfig["knowledge"]>>({ ...DEFAULT_KNOWLEDGE_CONFIG });
  const [knowledgeSavedSnapshot, setKnowledgeSavedSnapshot] = useState<AgentConfig["knowledge"] | null>(null);
  const [knowledgeReindexNeeded, setKnowledgeReindexNeeded] = useState(false);
  const [knowledgeReindexing, setKnowledgeReindexing] = useState(false);
  // Self-healing autosave retry for the reindex_in_progress 409. When a
  // vector-affecting PATCH lands while a reindex the FE never armed is in
  // flight (engine-triggered boot/config-drift reindex, or another client's),
  // we can't rely on the child panel's onReindexFinished to release a
  // suppression flag — that callback only fires for reindexes the FE armed,
  // so a flag-based hold would wedge "saving" until a hard refresh. Instead
  // we re-attempt the PATCH on a bounded timer; it succeeds the instant the
  // reindex clears (Layer 2 stops raising). Nonce drives the effect re-run.
  const knowledgeSaveRetryRef = useRef(0);
  const knowledgeSaveRetryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [knowledgeSaveRetryNonce, setKnowledgeSaveRetryNonce] = useState(0);
  // When a knowledge draft PATCH triggers an auto-reindex on the server
  // (e.g. user picks a new profile and the embedding-dim changes), the
  // response carries task_ids in ``auto_reindex.collections[*].result``.
  // We bubble them down to KnowledgePanel so its reindex tile arms
  // automatically — without this prop the user has to click "Reindex"
  // manually to see ANY progress for a server-side migration they
  // didn't explicitly trigger. ``triggerKey`` is the task-IDs join so a
  // re-render with the same payload doesn't re-arm twice.
  const [autoReindexTrigger, setAutoReindexTrigger] = useState<{
    taskIds: string[];
    total: number;
    triggerKey: string;
  } | null>(null);
  // Adaptation 422 wiring (Sami #60): the autosave PATCH below may return
  // a 422 with the structured ClientAdaptationError.to_dict() body. We
  // surface it into the panel via the controlled-state contract so the
  // operator sees what they need to fix instead of a silent no-save.
  // Cleared on the next successful save.
  const [adaptationServerError, setAdaptationServerError] = useState<AdaptationServerErrorShape | null>(null);
  const [knowledgeStale, setKnowledgeStale] = useState(false);
  // Live availability of the active embedder (from /health). null = unknown
  // (don't alarm); false = unreachable → the indexed docs can't be searched.
  const [knowledgeEmbedderAvailable, setKnowledgeEmbedderAvailable] = useState<boolean | null>(null);
  const [knowledgeEmbedderModel, setKnowledgeEmbedderModel] = useState<string>("");
  const [knowledgeReindexDeferred, setKnowledgeReindexDeferred] = useState(false);
  const [ragProfiles, setRagProfiles] = useState<Record<string, any>>({});
  const [knowledgePreviewModal, setKnowledgePreviewModal] = useState<{
    attachment: KnowledgeAttachmentSnapshot;
    content?: string;
    downloadUrl: string;
    isPdf: boolean;
  } | null>(null);
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
  // Preset clicks (env-presets "Use" button) bypass the 800ms autosave
  // debounce — the debounce coalesces keystrokes, but a deliberate
  // button click should feel instant. Set true in onPresetApplied,
  // consumed + reset in the knowledge autosave hook on next run.
  const forceImmediateSaveRef = useRef<boolean>(false);
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
            skills_enabled: Boolean(data.skills_enabled),
            workspace_filesystem_root:
              typeof data.workspace_filesystem_root === "string"
                ? data.workspace_filesystem_root
                : undefined,
            knowledge_enabled: Boolean(data.knowledge_enabled),
            agent_level_knowledge_enabled: Boolean(data.agent_level_knowledge_enabled),
            session_level_knowledge_enabled: Boolean(data.session_level_knowledge_enabled),
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
      if (t.env != null && typeof t.env === "object" && !Array.isArray(t.env) && Object.keys(t.env as Record<string, unknown>).length > 0) {
        entry.env = t.env as Record<string, string>;
      }
      if (t.command != null && String(t.command).trim()) {
        entry.command = String(t.command).trim();
        entry.args = Array.isArray(t.args) ? (t.args as string[]) : [];
        if (t.env && typeof t.env === "object" && !Array.isArray(t.env)) {
          entry.env = Object.fromEntries(
            Object.entries(t.env as Record<string, unknown>).map(([k, v]) => [k, String(v ?? "")])
          );
        }
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
    setToastNotifications((prev: ToastNotification[]) => [...prev, { id, kind, title, subtitle }]);
    setTimeout(() => {
      setToastNotifications((prev: ToastNotification[]) => prev.filter((t: ToastNotification) => t.id !== id));
    }, 5000);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToastNotifications((prev: ToastNotification[]) => prev.filter((t: ToastNotification) => t.id !== id));
  }, []);

  const refreshKnowledgeDocCount = useCallback(async () => {
    try {
      const response = await api.listKnowledgeDocuments();
      if (!response.ok) {
        return;
      }
      const data = await response.json().catch(() => ({}));
      setKnowledgeDocCount(data.documents?.length ?? 0);
    } catch {
      // Count refresh is best-effort; keep existing count on transient failures.
    }
  }, []);

  const handleKnowledgeDocsChanged = useCallback((count?: number) => {
    if (typeof count === "number") {
      setKnowledgeDocCount(count);
    } else {
      void refreshKnowledgeDocCount();
    }
    setKnowledgeDocsVersion((current) => current + 1);
  }, [refreshKnowledgeDocCount]);

  const closeKnowledgePreviewModal = useCallback(() => {
    setKnowledgePreviewModal((current) => {
      if (current?.downloadUrl) {
        URL.revokeObjectURL(current.downloadUrl);
      }
      return null;
    });
  }, []);

  const handlePreviewKnowledgeAttachment = useCallback(async (attachment: KnowledgeAttachmentSnapshot) => {
    try {
      const response = await api.getKnowledgeDocumentFile(
        attachment.scope,
        attachment.knowledge_filename,
      );
      if (!response.ok) {
        addToast("error", "Preview unavailable", response.statusText || "Failed to load attachment.");
        return;
      }

      const blob = await response.blob();
      const lowerName = attachment.display_name.toLowerCase();
      const downloadUrl = URL.createObjectURL(blob);

      if (lowerName.endsWith(".pdf")) {
        setKnowledgePreviewModal({
          attachment,
          downloadUrl,
          isPdf: true,
        });
        return;
      }

      const isTextFile = TEXT_EXTENSIONS.some((ext) => lowerName.endsWith(ext));
      if (isTextFile) {
        const content = await blob.text();
        setKnowledgePreviewModal({
          attachment,
          content,
          downloadUrl,
          isPdf: false,
        });
        return;
      }

      const anchor = document.createElement("a");
      anchor.href = downloadUrl;
      anchor.download = attachment.display_name;
      anchor.click();
      URL.revokeObjectURL(downloadUrl);
    } catch (error) {
      addToast("error", "Preview unavailable", error instanceof Error ? error.message : "Unknown error");
    }
  }, [addToast]);

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
          // Stamp the Live truth anchor from the PUBLISHED knowledge config.
          // Independent of whatever the draft is — this is the pill the
          // header always reads. Refreshed after every successful Publish
          // via the same endpoint, never updated optimistically.
          const liveKn = data?.config?.knowledge;
          if (liveKn && typeof liveKn === "object") {
            setLiveKnowledge({
              provider: typeof liveKn.embedding_provider === "string" ? liveKn.embedding_provider : "fastembed",
              model: typeof liveKn.embedding_model === "string" ? liveKn.embedding_model : "(default)",
              version: typeof data.version === "number" ? data.version : null,
              chunk_size: typeof liveKn.chunk_size === "number" ? liveKn.chunk_size : undefined,
              chunk_overlap: typeof liveKn.chunk_overlap === "number" ? liveKn.chunk_overlap : undefined,
              metric_type: typeof liveKn.metric_type === "string" ? liveKn.metric_type : undefined,
            });
          }
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
      setSpecialInstructions(out.special_instructions ?? "");
      setPolicies(out.policies ?? { enablePolicies: true, policies: [] });
      if (out.knowledge) {
        setKnowledgeConfig({ ...DEFAULT_KNOWLEDGE_CONFIG, ...out.knowledge });
        setKnowledgeSavedSnapshot(out.knowledge);
      } else {
        // Fallback: load from knowledge engine
        api.getKnowledgeSettings()
          .then((res) => (res.ok ? res.json() : null))
          .then((sData) => {
            if (sData?.knowledge) {
              setKnowledgeConfig((prev) => ({ ...prev, ...sData.knowledge }));
              setKnowledgeSavedSnapshot(sData.knowledge);
            }
          })
          .catch(() => {});
      }
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

  useEffect(() => {
    if (agentContext === null) return;
    if (!agentContext.skills_enabled) {
      setSkills([]);
      setSkillsLoading(false);
      return;
    }
    setSkillsLoading(true);
    api.getSkills()
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => data && setSkills(data.skills ?? []))
      .catch(() => {})
      .finally(() => setSkillsLoading(false));
  }, [agentContext]);

  useEffect(() => {
    if (!(knowledgeConfig.enabled ?? true) || !(knowledgeConfig.agent_level_enabled ?? true)) {
      setKnowledgeDocCount(0);
    }
  }, [knowledgeConfig.agent_level_enabled, knowledgeConfig.enabled]);

  const refreshKnowledgeHealth = useCallback(async () => {
    try {
      const res = await api.getKnowledgeHealth();
      const data = res.ok ? await res.json() : null;
      if (!data) {
        setKnowledgeHealthy(false);
        setKnowledgeHealthStatus("failed");
        return null;
      }
      setKnowledgeHealthy(data.healthy ?? false);
      setKnowledgeHealthStatus(data.status ?? (data.healthy ? "ready" : "unknown"));
      setKnowledgeStale(data.stale ?? false);
      setKnowledgeReindexDeferred(data.reindex_deferred ?? false);
      setKnowledgeEmbedderAvailable(
        typeof data.embedder_available === "boolean" ? data.embedder_available : null,
      );
      setKnowledgeEmbedderModel(data.embedder_model ?? "");
      return data;
    } catch {
      setKnowledgeHealthy(false);
      setKnowledgeHealthStatus("failed");
      return null;
    }
  }, []);

  useEffect(() => {
    api.setKnowledgeAgentId(effectiveAgentId || "cuga-default");

    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const scheduleHealthRefresh = async (attempt = 0) => {
      const data = await refreshKnowledgeHealth();
      if (cancelled || !data) {
        return;
      }

      const stillStarting = data.enabled !== false && !data.healthy && data.status === "starting";
      if (stillStarting && attempt < 20) {
        retryTimer = setTimeout(() => {
          void scheduleHealthRefresh(attempt + 1);
        }, 1500);
      }
    };

    void scheduleHealthRefresh();

    api.listKnowledgeDocuments()
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data && !cancelled) {
          setKnowledgeDocCount(data.documents?.length ?? 0);
        }
      })
      .catch(() => {});

    api.getKnowledgeSettings()
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.rag_profiles && !cancelled) {
          setRagProfiles(data.rag_profiles);
        }
      })
      .catch(() => {});

    return () => {
      cancelled = true;
      if (retryTimer) {
        clearTimeout(retryTimer);
      }
    };
  }, [effectiveAgentId, refreshKnowledgeHealth]);

  const assembleConfig = useCallback(
    (overrides?: Partial<AgentConfig>): AgentConfig => {
      const c: AgentConfig = {
        agent: { name: agentName, description: agentDescription || undefined },
        llm: llmConfig,
        tools: tools,
        feature_flags: featureFlags,
        homescreen,
        special_instructions: specialInstructions || undefined,
        policies,
        knowledge: knowledgeConfig,
      };
      return overrides ? { ...c, ...overrides } : c;
    },
    [agentName, agentDescription, llmConfig, tools, featureFlags, homescreen, specialInstructions, policies, knowledgeConfig]
  );


  const { saveLlmDraft, scheduleLlmDraftSave } = useLlmDraftSave({
    llmConfigRef,
    effectiveAgentId,
    addToast,
    setDraftSaving,
    setCurrentVersion,
  });

  const { saveAgentDraft } = useAgentDraftSave({
    agentName,
    agentDescription,
    effectiveAgentId,
    addToast,
    setDraftSaving,
    setCurrentVersion,
  });

  const { saveSpecialInstructionsDraft, scheduleSpecialInstructionsDraftSave } = useSpecialInstructionsDraftSave({
    effectiveAgentId,
    addToast,
    setDraftSaving,
    setCurrentVersion,
  });

  useToolsDraftSave({
    tools,
    effectiveAgentId,
    addToast,
    skipDraftSaveRef,
    setDraftSaving,
    setCurrentVersion,
  });

  const { draftSaveStatus, setDraftSaveStatus } = useKnowledgeDraftSave({
    knowledgeConfig,
    effectiveAgentId,
    addToast,
    skipDraftSaveRef,
    forceImmediateSaveRef,
    knowledgeReindexing,
    knowledgeSaveRetryRef,
    knowledgeSaveRetryTimerRef,
    knowledgeSaveRetryNonce,
    setKnowledgeSaveRetryNonce,
    setCurrentVersion,
    setAdaptationServerError,
    setAutoReindexTrigger,
    setKnowledgeSavedSnapshot,
    setKnowledgeDocCount,
  });

  const { performDraftSave } = useFullDraftSave({
    assembleConfig,
    effectiveAgentId,
    addToast,
    setDraftSaving,
    setCurrentVersion,
    importStatus,
    refreshKnowledgeHealth,
  });

  const { showReindexConfirm, setShowReindexConfirm, handleSaveClick, saveConfig } = usePublishConfig({
    assembleConfig,
    agentName,
    knowledgeConfig,
    knowledgeReindexNeeded,
    knowledgeDocCount,
    effectiveAgentId,
    addToast,
    setSaveStatus,
    setCurrentVersion,
    setLiveKnowledge,
    setKnowledgeSavedSnapshot,
    setKnowledgeDocCount,
    setDraftSaveStatus,
    refreshKnowledgeHealth,
    loadHistory,
  });


  // Knowledge reindex detection — compare current config against the
  // last saved/published state. The principle: only flag "needs
  // re-index" when the CURRENT config would produce a different
  // vector index than the SAVED config. Two configs are equivalent
  // for the index when every field the engine's vector_config_hash
  // considers (provider, model, chunk_size, chunk_overlap, metric)
  // resolves to the same effective value.
  //
  // ``embedding_model = ""`` is the Provider Select's reset value
  // (means "use provider default"). On the SAME provider, an empty
  // current model is functionally equivalent to whatever specific
  // model the snapshot holds — the engine picks the same default at
  // embed time. The general pattern: for every field where the UI
  // can produce an empty/unset value that the engine resolves to the
  // saved value, treat empty current as a match.
  //
  // Only ``embedding_model`` needs this treatment today (numeric
  // chunking fields never empty after edits; metric_type comes from
  // a Select with concrete enum values). Adding more cases is a
  // one-line change in ``isIndexConfigEquivalent``.
  useEffect(() => {
    if (!knowledgeSavedSnapshot) return;
    const saved = { ...DEFAULT_KNOWLEDGE_CONFIG, ...knowledgeSavedSnapshot };
    const changed = !isIndexConfigEquivalent(knowledgeConfig, saved);
    setKnowledgeReindexNeeded(changed && knowledgeDocCount > 0);
  }, [knowledgeConfig, knowledgeSavedSnapshot, knowledgeDocCount]);

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
        setKnowledgeConfig(next.knowledge ? { ...DEFAULT_KNOWLEDGE_CONFIG, ...next.knowledge } : { ...DEFAULT_KNOWLEDGE_CONFIG });
        setKnowledgeSavedSnapshot(next.knowledge ?? null);
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

  const updateLlm = (field: keyof NonNullable<AgentConfig["llm"]>, value: string | number | boolean) => {
    setLlmConfig((c) => ({ ...(c ?? {}), [field]: value }));
  };
  const updateLlmTemperature = (value: number) => {
    setLlmConfig((c) => ({ ...(c ?? {}), temperature: value }));
  };

  const updateFeatureFlag = (field: "enable_todos" | "reflection" | "enable_filesystem_tools", value: boolean) => {
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
          if (raw.knowledge && typeof raw.knowledge === "object") {
            out.knowledge = { ...DEFAULT_KNOWLEDGE_CONFIG, ...(raw.knowledge as Record<string, unknown>) };
          }
          if (raw.agent && typeof raw.agent === "object") {
            const a = raw.agent as { name?: string; description?: string };
            if (a.name) setAgentName(a.name);
            if (a.description !== undefined) setAgentDescription(a.description);
          }
          setLlmConfig(out.llm ?? DEFAULT_CONFIG.llm!);
          setToolsState(Array.isArray(out.tools) ? out.tools : []);
          setFeatureFlags(out.feature_flags ?? DEFAULT_CONFIG.feature_flags!);
          setHomescreen(out.homescreen ?? DEFAULT_HOMESCREEN);
          setPolicies(out.policies ?? { enablePolicies: true, policies: [] });
          setKnowledgeConfig(out.knowledge ?? { ...DEFAULT_KNOWLEDGE_CONFIG });
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
              <AccordionItem title="Special Instructions">
                <VStack gap={4}>
                  <p style={{ fontSize: "0.875rem", color: "var(--cds-text-secondary)" }}>
                    Text injected directly into the agent&apos;s system prompt before every conversation. Use this to give the agent a persona, domain context, or standing rules.
                  </p>
                  <TextArea
                    id="special-instructions"
                    labelText=""
                    value={specialInstructions}
                    onChange={(e) => {
                      const v = e.target.value;
                      setSpecialInstructions(v);
                      if (!skipDraftSaveRef.current) scheduleSpecialInstructionsDraftSave(v);
                    }}
                    placeholder="e.g. You are a helpful sales assistant for Acme Corp. Always respond formally and focus on enterprise software solutions."
                    rows={6}
                  />
                  <div style={{ display: "flex", justifyContent: "flex-end" }}>
                    <Button
                      kind="secondary"
                      size="sm"
                      renderIcon={Save}
                      onClick={() => saveSpecialInstructionsDraft(specialInstructions, true)}
                    >
                      Save draft
                    </Button>
                  </div>
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
                    builtinTools={featureFlags.builtin_tools}
                    onError={(title, message) => addToast("error", title, message)}
                    onOpenSecrets={() => setSecretsModalOpen(true)}
                  />
              </AccordionItem>

              {agentContext?.skills_enabled ? (
              <AccordionItem title="Skills">
                {skillsLoading ? (
                  <InlineLoading description="Loading skills…" />
                ) : skills.length === 0 ? (
                  <p className="cds--type-body-compact-01" style={{ color: "var(--cds-text-secondary)" }}>
                    No skills found. Add SKILL.md files under <code>.cuga/skills/</code> (default) or set <code>[skills] root</code> in settings.toml.
                  </p>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                    {skills.map((skill) => {
                      const pipDeps = skill.requirements.filter((r) => !r.startsWith("npm:"));
                      const npmDeps = skill.requirements.filter((r) => r.startsWith("npm:")).map((r) => r.slice(4));
                      const LIMIT = 120;
                      const isLong = skill.description.length > LIMIT;
                      const isExpanded = expandedSkills.has(skill.name);
                      const displayDesc = isLong && !isExpanded
                        ? skill.description.slice(0, LIMIT).trimEnd() + "…"
                        : skill.description;
                      return (
                        <Tile key={skill.name} style={{ padding: "0.75rem 1rem" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.35rem" }}>
                            <SkillIcon size={16} />
                            <span style={{ fontWeight: 600, fontSize: "0.875rem" }}>{skill.name}</span>
                            <Tag type="green" size="sm" style={{ marginLeft: "auto" }}>active</Tag>
                          </div>
                          <p style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)", marginBottom: pipDeps.length || npmDeps.length ? "0.5rem" : 0, lineHeight: 1.4 }}>
                            {displayDesc}
                            {isLong && (
                              <button
                                onClick={() => setExpandedSkills((prev) => {
                                  const next = new Set(prev);
                                  isExpanded ? next.delete(skill.name) : next.add(skill.name);
                                  return next;
                                })}
                                style={{ background: "none", border: "none", padding: 0, marginLeft: "0.25rem", cursor: "pointer", fontSize: "0.75rem", color: "var(--cds-link-primary)" }}
                              >
                                {isExpanded ? "less" : "more"}
                              </button>
                            )}
                          </p>
                          {(pipDeps.length > 0 || npmDeps.length > 0) && (
                            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.25rem", alignItems: "center" }}>
                              <PackageIcon size={12} style={{ color: "var(--cds-text-secondary)", flexShrink: 0 }} />
                              {pipDeps.map((d) => (
                                <Tag key={d} type="gray" size="sm">{d}</Tag>
                              ))}
                              {npmDeps.map((d) => (
                                <Tag key={d} type="teal" size="sm">npm:{d}</Tag>
                              ))}
                            </div>
                          )}
                        </Tile>
                      );
                    })}
                  </div>
                )}
              </AccordionItem>
              ) : null}

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
                        }}
                      />
                    </FormGroup>
                    <FormGroup legendText="">
                      <Checkbox
                        id="enable_filesystem_tools"
                        labelText="Filesystem tools"
                        checked={flags.enable_filesystem_tools ?? false}
                        onChange={(_e, { checked }) => {
                          updateFeatureFlag("enable_filesystem_tools", !!checked);
                        }}
                      />
                      <p style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)", marginTop: "0.25rem" }}>
                        Gives the agent read, write, edit, list, and search access to the workspace filesystem.
                      </p>
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

              <AccordionItem title="Knowledge">
                  <Stack gap={3} orientation="vertical">
                    <div className="manage-knowledge-status">
                      <span
                        className="manage-knowledge-dot"
                        style={{
                          background:
                            knowledgeHealthStatus === "starting" || knowledgeHealthy === null
                              ? "#9ca3af"
                              : knowledgeHealthy
                              ? "#10b981"
                              : "#ef4444",
                        }}
                      />
                      <span className="cds--type-body-compact-01">
                        {knowledgeHealthStatus === "starting" || knowledgeHealthy === null
                          ? "Starting knowledge..."
                          : knowledgeHealthy
                          ? `Connected${knowledgeDocCount > 0 ? ` · ${knowledgeDocCount} document${knowledgeDocCount !== 1 ? "s" : ""} indexed` : ""}`
                          : knowledgeHealthStatus === "disabled"
                          ? "Disabled"
                          : "Disconnected"}
                      </span>
                    </div>
                    {/* Embedder-unavailable alert. Distinct from the removed
                        "re-index recommended" NAG below: this is a real error —
                        the documents exist but their embedder can't embed
                        queries (missing/invalid key, provider down), so search
                        returns nothing. Surfaced on the agent card (not just in
                        the modal) because it makes knowledge silently useless. */}
                    {knowledgeEmbedderAvailable === false && knowledgeDocCount > 0 && (
                      <InlineNotification
                        kind="error"
                        lowContrast
                        hideCloseButton
                        title="Embedder unavailable"
                        subtitle={
                          `Your ${knowledgeDocCount} indexed document${knowledgeDocCount !== 1 ? "s" : ""} can't be searched — ` +
                          `the active embedder${knowledgeEmbedderModel ? ` (${knowledgeEmbedderModel})` : ""} isn't reachable. ` +
                          `Open Configure knowledge base to check its API key / connection and run Test connection.`
                        }
                        style={{ maxInlineSize: "100%" }}
                      />
                    )}
                    {/* The "Re-index recommended" InlineNotification used to
                        live here as a call-to-action to open the modal. It's
                        gone now because (a) the Live pill above already
                        signals divergence via its dot color + tooltip, and
                        (b) the modal itself shows the actionable "Update
                        existing documents" notification when the user opens
                        it. Duplicating the warning on the agent card was the
                        most-cited noise source in the pre-client review. */}
                    <Button
                      kind="secondary"
                      size="sm"
                      renderIcon={DocumentIcon}
                      onClick={() => setShowKnowledgeModal(true)}
                    >
                      Configure knowledge base
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
                      onClick={handleSaveClick}
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
                      {/* Live truth anchor — what's actually serving production
                          traffic right now. Sourced from GET /api/manage/config
                          (published) on mount + after every successful Publish.
                          Dot color: green when draft matches Live (no pending
                          changes), yellow when the user has unpublished edits.
                          The synthesis identified this as the single most
                          important UX addition — without it, no surface
                          answers "what is actually running?" without log-reading. */}
                      {!loadError && liveKnowledge && (() => {
                        // "Diverged" means a Publish would change what's
                        // actually serving traffic. Reuses the same equivalence
                        // helper the Re-index banner uses, so both signals
                        // agree on what counts as a meaningful change. Avoids
                        // duplicating the empty-model-as-default rule.
                        const diverged = !isIndexConfigEquivalent(knowledgeConfig, {
                          ...DEFAULT_KNOWLEDGE_CONFIG,
                          embedding_provider: liveKnowledge.provider,
                          embedding_model: liveKnowledge.model,
                          // Compare against the PUBLISHED chunk/metric, not the
                          // draft's own values (Sami review) — otherwise a
                          // chunk-only draft edit compares against itself and
                          // never turns the pill yellow.
                          chunk_size: liveKnowledge.chunk_size ?? DEFAULT_KNOWLEDGE_CONFIG.chunk_size,
                          chunk_overlap: liveKnowledge.chunk_overlap ?? DEFAULT_KNOWLEDGE_CONFIG.chunk_overlap,
                          metric_type: liveKnowledge.metric_type ?? DEFAULT_KNOWLEDGE_CONFIG.metric_type,
                        });
                        const label = (
                          <>
                            Live: {liveKnowledge.provider} · {liveKnowledge.model}
                            {liveKnowledge.version != null && ` · v${liveKnowledge.version}`}
                          </>
                        );
                        // In-sync: plain Carbon Tag, green. No tooltip — the
                        // label is self-explanatory. Diverged: warm-gray Tag
                        // wrapped in Tooltip with the actionable hint. Tooltip
                        // child must be a single focusable element; a Tag with
                        // tabIndex satisfies that without inventing a button
                        // wrapper to mimic plain text.
                        if (!diverged) {
                          return (
                            <Tag type="green" size="sm" className="manage-save-bar-version">
                              {label}
                            </Tag>
                          );
                        }
                        return (
                          <Tooltip
                            label={`Draft differs from Live (now ${knowledgeConfig.embedding_provider} · ${knowledgeConfig.embedding_model || "(default)"}). Click Publish to apply.`}
                            align="bottom"
                          >
                            <button
                              type="button"
                              style={{ background: "none", border: "none", padding: 0, cursor: "help" }}
                              className="manage-save-bar-version"
                            >
                              <Tag type="warm-gray" size="sm">
                                {label}
                              </Tag>
                            </button>
                          </Tooltip>
                        );
                      })()}
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
              attachmentScope="agent"
              knowledgeEnabled={knowledgeConfig.enabled ?? true}
              agentKnowledgeEnabled={knowledgeConfig.agent_level_enabled ?? true}
              disableHistory={true}
              homescreen={homescreen}
              sessionDocsVersion={knowledgeDocsVersion}
              onSessionDocsChanged={() => handleKnowledgeDocsChanged()}
              onOpenKnowledge={() => setShowKnowledgeModal(true)}
              onPreviewKnowledgeAttachment={handlePreviewKnowledgeAttachment}
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
          onSave={(policies: any) => setPolicies(policies)}
        />
      )}

      {showKnowledgeModal && (
        <KnowledgePanel
          onClose={() => setShowKnowledgeModal(false)}
          onDocsChanged={handleKnowledgeDocsChanged}
          onHealthChanged={(healthy) => {
            setKnowledgeHealthy(healthy);
            setKnowledgeHealthStatus(healthy ? "ready" : "failed");
          }}
          onToast={(kind: "error" | "success" | "warning", title: string, message: string) => addToast(kind, title, message)}
          knowledgeConfig={knowledgeConfig}
          onKnowledgeConfigChange={setKnowledgeConfig}
          draftSaveStatus={draftSaveStatus}
          onRetryDraftSave={() => {
            // Retry: bump the same field with its current value to retrigger
            // the autosave useEffect. Cheap and reuses the existing PATCH
            // pipeline rather than maintaining a parallel retry path.
            // Reset the reindex_in_progress retry budget too, so a manual
            // Retry after the "still running" timeout re-arms the auto-retry.
            knowledgeSaveRetryRef.current = 0;
            setKnowledgeConfig((prev) => ({ ...prev }));
          }}
          onDismissDraftSave={() => {
            // Close (X) on the failure banner = dismiss only, no retry.
            setDraftSaveStatus({ kind: "idle" });
          }}
          onPresetApplied={() => {
            // The user just clicked an explicit "Use" button — bypass the
            // keystroke-coalesce debounce so "Saving…" appears immediately.
            forceImmediateSaveRef.current = true;
          }}
          knowledgeReindexNeeded={knowledgeReindexNeeded}
          knowledgeStale={knowledgeStale}
          knowledgeReindexDeferred={knowledgeReindexDeferred}
          knowledgeReindexing={knowledgeReindexing}
          ragProfiles={ragProfiles}
          adaptationServerError={adaptationServerError}
          onAdaptationServerError={setAdaptationServerError}
          autoReindexTrigger={autoReindexTrigger}
          onAutoReindexConsumed={() => setAutoReindexTrigger(null)}
          onAutoReindexComplete={() => {
            // The engine has finished re-embedding under the current
            // knowledgeConfig. Refresh the saved-config snapshot so the
            // "Reindex needed" banner (which compares snapshot vs.
            // current) clears. Previously the snapshot only updated on
            // Publish, leaving the banner stuck even after a successful
            // auto-reindex.
            setKnowledgeSavedSnapshot({ ...knowledgeConfig });
            setKnowledgeReindexNeeded(false);
          }}
          onReindex={async () => {
            setKnowledgeReindexing(true);
            try {
              // Route to the config-aware migration endpoint when the
              // user's edits changed vector-config fields (embedder /
              // chunking / metric). That endpoint handles the cross-
              // hash file migration in addition to the reindex. Plain
              // re-index of an already-correct collection still goes
              // through the original endpoint.
              const res = knowledgeReindexNeeded
                ? await api.triggerKnowledgeReindexForConfig(effectiveAgentId)
                : await api.triggerKnowledgeReindex();
              if (res.ok) {
                const data = await res.json();
                // #398 follow-up v2: do NOT clear ``knowledgeReindexing`` here.
                // The POST returns in <100ms with task_ids, but the actual
                // ingest workers run for 10-15s afterward — that's exactly
                // when the autosave-debounce PATCH lands and hits Layer 1's
                // 409. We clear ``knowledgeReindexing`` only when the child
                // panel reports its polling reached terminal state (via
                // ``onReindexFinished`` below). Failure branches clear it
                // explicitly per case.
                // triggered:false ⇒ structural failure (status 2xx, ``error`` field set).
                if (data?.triggered === false) {
                  setKnowledgeReindexing(false);
                  const ERR: Record<string, { title: string; kind: "warning" | "error"; msg: string }> = {
                    active_snapshot_missing: {
                      title: "Re-index didn't run",
                      kind: "error",
                      msg: "Your active document set isn't on disk. If you restored an older version, re-upload or migrate via CLI.",
                    },
                    copy_failed: {
                      title: "Re-index didn't run",
                      kind: "error",
                      msg: "Couldn't copy your documents to the new collection. Check disk space / permissions and retry.",
                    },
                    reindex_failed: {
                      title: "Re-index didn't run",
                      kind: "error",
                      msg: "Re-index ran but didn't embed anything. Check server logs and retry.",
                    },
                    // #398: distinguish "wait, uploads in progress" from
                    // a generic failure. Warning (not error) because it's
                    // recoverable just by retrying once uploads settle.
                    reindex_busy: {
                      title: "Re-index couldn't start",
                      kind: "warning",
                      msg: "Uploads or another re-index are still running. Wait a moment, then try again.",
                    },
                  };
                  const code = typeof data.error === "string" ? data.error : "unknown";
                  const spec = ERR[code];
                  // [#398] Asserts the FE branched into the busy-toast path
                  // (kind=warning) vs the failure path (kind=error). Pair
                  // with the backend's "[#398] reindex_busy" log: the two
                  // should fire together within one HTTP round-trip.
                  if (spec) {
                    addToast(spec.kind, spec.title, spec.msg);
                  } else {
                    addToast("error", "Re-index didn't run", `Re-index couldn't run (${code}).`);
                  }
                  return null;
                }
                // #3: do NOT advance the saved-config snapshot here. The POST
                // only STARTS the reindex (returns task_ids in <100ms); workers
                // run for 10-15s afterward and the strict deferred flip may
                // REFUSE promotion on partial failure — in which case the OLD
                // embedder stays active. Advancing the snapshot now would clear
                // the "Re-index needed" banner permanently and present the new
                // config as active even after a strict-refuse. The snapshot is
                // advanced ONLY on full success, via onAutoReindexComplete
                // (fired from the child poll when failed===0).
                // /reindex_for_config returns {collections: [{result: {task_ids, count, tasks}}]};
                // /reindex returns {task_ids, count, tasks} flat. Normalize.
                // ``tasks`` is a new field (#402 production sweep) carrying
                // [{task_id, filename}] so the FE can render the tile with
                // real filenames from the first render — no ``task_xxx``
                // flicker waiting for the first /tasks GET to complete.
                if (Array.isArray(data?.collections)) {
                  const allTaskIds: string[] = data.collections
                    .flatMap((c: { result?: { task_ids?: string[] } }) => c?.result?.task_ids ?? []);
                  const allTasks: { task_id: string; filename: string }[] = data.collections
                    .flatMap((c: { result?: { tasks?: { task_id: string; filename: string }[] } }) => c?.result?.tasks ?? []);
                  const total = data.collections.reduce(
                    (sum: number, c: { result?: { count?: number } }) => sum + (c?.result?.count ?? 0),
                    0,
                  );
                  return {
                    count: total || allTaskIds.length,
                    task_ids: allTaskIds,
                    tasks: allTasks,
                  };
                }
                return {
                  count: data.count ?? 0,
                  task_ids: data.task_ids ?? [],
                  tasks: data.tasks ?? [],
                };
              } else if (res.status === 409) {
                addToast("warning", "Cannot re-index", "Uploads in progress. Try again later.");
                setKnowledgeReindexing(false);
              } else {
                addToast("error", "Re-index failed", `Error ${res.status}`);
                setKnowledgeReindexing(false);
              }
            } catch {
              addToast("error", "Re-index failed", "Network error");
              setKnowledgeReindexing(false);
            }
            return null;
          }}
          onReindexFinished={() => {
            // #398 follow-up v2: child panel reports its task polling
            // reached terminal state (success OR partial failure — both
            // signal "workers stopped, autosave PATCHes are safe again").
            // Pair with the early-return in the autosave effect that
            // checks ``knowledgeReindexing`` — the suppression window now
            // covers the full ingest duration, not just the POST RTT.
            setKnowledgeReindexing(false);
          }}
        />
      )}

      {showReindexConfirm && (
        <ComposedModal
          open
          onClose={() => setShowReindexConfirm(false)}
          size="sm"
        >
          <ModalHeader title="Re-index required" />
          <ModalBody>
            <p>
              Embedding or chunking settings changed. Publishing will re-index{" "}
              {knowledgeDocCount} document{knowledgeDocCount !== 1 ? "s" : ""} in the background.
              Search may return incomplete results during re-indexing.
            </p>
          </ModalBody>
          <ModalFooter>
            <Button kind="secondary" onClick={() => setShowReindexConfirm(false)}>
              Cancel
            </Button>
            <Button kind="danger" onClick={() => saveConfig()}>
              Publish &amp; Re-index
            </Button>
          </ModalFooter>
        </ComposedModal>
      )}

      <ComposedModal
        open={!!knowledgePreviewModal}
        onClose={closeKnowledgePreviewModal}
        size="lg"
        isFullWidth
      >
        <ModalHeader
          title={knowledgePreviewModal?.attachment.display_name ?? ""}
          buttonOnClick={closeKnowledgePreviewModal}
        />
        <ModalBody hasScrollingContent>
          {knowledgePreviewModal && (
            knowledgePreviewModal.isPdf ? (
              <iframe
                title={knowledgePreviewModal.attachment.display_name}
                src={knowledgePreviewModal.downloadUrl}
                style={{ width: "100%", minHeight: "70vh", border: "none" }}
              />
            ) : (
              <div className="manage-json-viewer-markdown">
                <Markdown>
                  {knowledgePreviewModal.attachment.display_name.toLowerCase().endsWith(".md")
                    ? knowledgePreviewModal.content ?? ""
                    : `\`\`\`\n${knowledgePreviewModal.content ?? ""}\n\`\`\``}
                </Markdown>
              </div>
            )
          )}
        </ModalBody>
        {knowledgePreviewModal && (
          <ModalFooter>
            <Button
              kind="secondary"
              renderIcon={Download}
              onClick={() => {
                const anchor = document.createElement("a");
                anchor.href = knowledgePreviewModal.downloadUrl;
                anchor.download = knowledgePreviewModal.attachment.display_name;
                anchor.click();
              }}
            >
              Download
            </Button>
            <Button kind="primary" onClick={closeKnowledgePreviewModal}>
              Close
            </Button>
          </ModalFooter>
        )}
      </ComposedModal>

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
                {"```json\n" + JSON.stringify(maskSecrets((() => { const { knowledge_state: _ks, ...rest } = viewVersion.config as Record<string, unknown>; return rest; })()), null, 2) + "\n```"}
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
                setKnowledgeConfig(next.knowledge ? { ...DEFAULT_KNOWLEDGE_CONFIG, ...next.knowledge } : { ...DEFAULT_KNOWLEDGE_CONFIG });
                setKnowledgeSavedSnapshot(next.knowledge ?? null);
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
        {toastNotifications.map((toast: { id: string; kind: "error" | "info" | "success" | "warning"; title: string; subtitle: string }) => (
            <ToastNotification
              key={toast.id}
              kind={toast.kind}
              title={toast.title}
              subtitle={toast.subtitle}
              timeout={5000}
              onClose={() => removeToast(toast.id)}
              lowContrast
            />
        ))}
      </div>
    </div>
  );
}
