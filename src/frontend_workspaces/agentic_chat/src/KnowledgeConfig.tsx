// eslint-disable-next-line @typescript-eslint/no-unused-vars
import React, { useState, useEffect, useRef, useCallback } from "react";
import ClientAdaptationPanel, {
  CLIENT_ADAPTATION_MAX_CHARS as CLIENT_ADAPTATION_MAX_CHARS_RE,
  AdaptationServerError,
  GlossaryEntry,
} from "./ClientAdaptationPanel";
import {
  ComposedModal,
  ModalHeader,
  ModalBody,
  ModalFooter,
  Button,
  TextInput,
  NumberInput,
  Select,
  SelectItem,
  ActionableNotification,
  InlineNotification,
  InlineLoading,
  Theme,
  Tabs,
  TabList,
  Tab,
  TabPanels,
  TabPanel,
  Stack,
  Tile,
  Tag,
  Toggle,
  Accordion,
  AccordionItem,
  AILabel,
  AILabelContent,
} from "@carbon/react";
import { Upload, TrashCan, Search, Renew, Document, Checkmark, ErrorFilled, Reset, Close } from "@carbon/icons-react";
import { apiFetch } from "../../frontend/src/api";
import * as api from "../../frontend/src/api";
import { EnvPresetsPanel } from "./EnvPresetsPanel";
import "./ConfigModal.css";

// ---------------------------------------------------------------------------
// Reindex progress types
// ---------------------------------------------------------------------------
interface ReindexTask {
  task_id: string;
  filename?: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  file_tasks?: Record<
    string,
    {
      filename?: string;
      status?: string;
      error?: string;
      // Granular progress emitted by the engine during ingest (issue #183).
      // Backend writes per-stage events between "processing" and "completed";
      // we render "<stage> (done/total)" when present.
      stage?: string;
      progress?: { done?: number; total?: number };
    }
  >;
}

function getReindexTaskProgressLabel(task: ReindexTask): string | undefined {
  if (!task.file_tasks) {
    return undefined;
  }
  const firstEntry = Object.values(task.file_tasks)[0];
  if (!firstEntry?.stage) {
    return undefined;
  }
  const { stage, progress } = firstEntry;
  const done = progress?.done;
  const total = progress?.total;
  // Map raw backend stage names to friendly labels.
  const friendly: Record<string, string> = {
    parsed: "Parsed",
    embed: "Embedding",
    insert_start: "Saving",
    insert: "Saving",
  };
  const label = friendly[stage] ?? stage;
  if (typeof done === "number" && typeof total === "number" && total > 0) {
    return `${label} (${done}/${total})`;
  }
  return label;
}

interface ReindexProgress {
  taskIds: string[];
  tasks: ReindexTask[];
  total: number;
  completed: number;
  failed: number;
  done: boolean;
}

function getReindexTaskFilename(task: ReindexTask): string | undefined {
  if (task.filename) {
    return task.filename;
  }
  if (!task.file_tasks) {
    return undefined;
  }
  const firstEntry = Object.values(task.file_tasks)[0];
  if (!firstEntry) {
    return undefined;
  }
  return firstEntry.filename;
}

function getReindexTaskError(task: ReindexTask): string | undefined {
  if (!task.file_tasks) {
    return undefined;
  }
  const firstEntry = Object.values(task.file_tasks)[0];
  if (!firstEntry?.error) {
    return undefined;
  }
  return firstEntry.error;
}

function getReindexStatusLabel(status: ReindexTask["status"]): string {
  if (status === "running") {
    return "Indexing";
  }
  if (status === "completed") {
    return "Completed";
  }
  if (status === "failed") {
    return "Failed";
  }
  return "Pending";
}

// Narrative phase headline for the reindex tile. Replaces the bare
// "Re-indexing documents..." string with what's actually happening
// RIGHT NOW so a 90-second model download stops reading as "stuck on
// 0 of 1". Driven by the per-task ``stage`` field the engine emits
// (parsed / embed / insert / insert_start); no stage yet = still
// preparing. ``parsed`` / ``embed`` and the fallback all collapse to
// "Re-reading" — only the insert phase changes the headline.
function getReindexPhaseHeadline(tasks: ReindexTask[]): string {
  // "Preparing" only while EVERY task is still pending. Once any task
  // flips to ``running`` we know the worker has picked it up and is
  // doing real work (model load, parse, embed) even if no per-file
  // ``stage`` event has been emitted yet — keeping the headline on
  // "Preparing" past that point reads as "stuck on the loading screen".
  if (tasks.length === 0 || tasks.every((t) => t.status === "pending")) {
    return "Preparing your new reading model";
  }
  const stages = tasks.flatMap((t) => Object.values(t.file_tasks ?? {}).map((ft) => ft.stage)).filter(Boolean);
  if (stages.some((s) => s === "insert" || s === "insert_start")) return "Filing everything in its new place";
  return "Re-reading your documents";
}

// Honest elapsed timer — "Running for 47s" beats a fake countdown. Bands
// switch at 60s / 5min to keep the unit readable.
function formatElapsedSeconds(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 300) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 60)}m`;
}

// ---------------------------------------------------------------------------
// In-flight upload persistence
// ---------------------------------------------------------------------------
// Survives modal close + page reload so the user sees their bar continue
// rather than vanish. Cleared whenever a task lands in a terminal state.
const ACTIVE_UPLOADS_LS_KEY = "cuga.knowledge.activeUploads";
// Drop entries older than 1h to avoid resurrecting tasks the server has
// long since GC'd. The server's recover_stale_tasks already handles its
// side; this is the client's belt-and-suspenders.
const ACTIVE_UPLOADS_TTL_MS = 60 * 60 * 1000;

interface PersistedUpload {
  taskId: string;
  name: string;
  createdAt: number;
}

function loadActiveUploads(): PersistedUpload[] {
  try {
    const raw = localStorage.getItem(ACTIVE_UPLOADS_LS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    const now = Date.now();
    return parsed.filter((entry): entry is PersistedUpload => {
      if (!entry || typeof entry !== "object") return false;
      const e = entry as Record<string, unknown>;
      return (
        typeof e.taskId === "string" &&
        typeof e.name === "string" &&
        typeof e.createdAt === "number" &&
        now - e.createdAt < ACTIVE_UPLOADS_TTL_MS
      );
    });
  } catch {
    // Quota errors, private-mode storage, etc. — degrade silently;
    // resume just won't work, uploads still do.
    return [];
  }
}

function saveActiveUploads(entries: PersistedUpload[]): void {
  try {
    if (entries.length === 0) {
      localStorage.removeItem(ACTIVE_UPLOADS_LS_KEY);
    } else {
      localStorage.setItem(ACTIVE_UPLOADS_LS_KEY, JSON.stringify(entries));
    }
  } catch {
    // No-op: persistence is best-effort.
  }
}

function addActiveUpload(entry: PersistedUpload): void {
  const current = loadActiveUploads().filter((e) => e.taskId !== entry.taskId);
  current.push(entry);
  saveActiveUploads(current);
}

function removeActiveUpload(taskId: string): void {
  const current = loadActiveUploads().filter((e) => e.taskId !== taskId);
  saveActiveUploads(current);
}

// Sleep that rejects with an AbortError as soon as ``signal`` aborts, so
// closing the modal mid-poll doesn't make the user wait the full interval
// for the loop to notice.
function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function isAbortError(e: unknown): boolean {
  return (
    typeof e === "object" &&
    e !== null &&
    "name" in e &&
    (e as { name: unknown }).name === "AbortError"
  );
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface KnowledgeDocument {
  filename: string;
  ingested_at?: string;
  task_id?: string;
}

interface SearchResult {
  filename: string;
  page?: number;
  text?: string;
  content?: string;
  score: number;
  metadata?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------
interface KnowledgeConfigValues {
  enabled?: boolean;
  agent_level_enabled?: boolean;
  session_level_enabled?: boolean;
  rag_profile?: string;
  embedding_provider?: string;
  embedding_model?: string;
  // For OpenAI-compatible providers (Groq, Together, Fireworks, OpenRouter, ...)
  // set embedding_provider="openai" and override these two fields.
  embedding_api_key?: string;
  embedding_base_url?: string;
  embedding_extra_params?: Record<string, string | number | boolean>;
  use_gpu?: boolean;
  chunk_size?: number;
  chunk_overlap?: number;
  metric_type?: string;
  max_pending_tasks?: number;
  max_upload_size_mb?: number;
  max_url_download_size_mb?: number;
  max_files_per_request?: number;
  max_chunks_per_document?: number;
  // Adapter batching (issue #183). Saved with the config snapshot when published.
  embedding_batch_size?: number;
  embedding_concurrency?: number;
  vector_insert_batch_size?: number;
  // Docling PDF parsing level: "fast" | "balanced" | "accurate".
  // Trades parse speed for extraction fidelity. Saved with the snapshot.
  docling_pdf_mode?: string;
  docling_layout_engine?: string;
  // Docling page-chrome filter: "off" | "dry_run" | "enforce". Profile-
  // driven; drops repetitive headers/footers when "enforce". Sami review.
  docling_drop_page_chrome?: string;
  // Reranker knobs — driven by the active profile (e.g. balanced &
  // max_quality enable; standard/speed disable). Profile-onClick MUST
  // be able to read/write these without a TS error (Sami C3 root cause).
  rerank_enabled?: boolean;
  rerank_top_k_in?: number;
  rerank_model?: string;
  // Search-side knobs — same story as rerank.
  search_hybrid_mode?: string;
  search_junk_filter?: string;
  max_search_attempts?: number;
  default_limit?: number;
  default_score_threshold?: number;
  // Ingest-side knob: how many concurrent Docling parses the engine
  // permits (max_quality bumps this for laptops with headroom).
  max_ingest_workers?: number;
  // Query transformation (LLM): "off" | "multi_query" | "hyde". Operator-only
  // (Advanced). Search-only — not part of vector_config_hash, no re-index.
  search_query_transform?: string;
  // Client adaptation — operator-supplied prompt rules + glossary.
  // Saved with the snapshot; never part of vector_config_hash.
  client_adaptation_text?: string;
  client_adaptation_glossary?: GlossaryEntry[];
}

// Re-export from the panel for any downstream consumers; the canonical
// constant lives in ClientAdaptationPanel.tsx.
const CLIENT_ADAPTATION_MAX_CHARS = CLIENT_ADAPTATION_MAX_CHARS_RE;
void CLIENT_ADAPTATION_MAX_CHARS;

// String-union name for each top-level tab in this modal. Used by parents
// (e.g. ManagePage) to deep-link the modal to a specific tab without
// reaching for raw indices. TAB_INDEX is the single source of truth and is
// also exported so that test code can assert ordering invariants.
export type KnowledgeTabName = "documents" | "search" | "behavior" | "settings";
export const TAB_INDEX: Record<KnowledgeTabName, number> = {
  documents: 0,
  search: 1,
  behavior: 2,
  settings: 3,
};

interface RagProfileMeta {
  name: string;
  description: string;
  search: {
    max_search_attempts?: number;
    default_limit?: number;
    default_score_threshold?: number;
    hybrid_mode?: string;
    junk_filter?: string;
    query_transform?: string;
  };
  chunking: { chunk_size?: number; chunk_overlap?: number };
  // Added so profile-click can fully populate the config — without these
  // the autosave POSTed stale embedding_model / docling_* / rerank_*
  // values that the backend's "incoming wins" merge then reverted onto
  // the profile loader's output, making profile switches a no-op.
  embeddings?: { model?: string; batch_size?: number; concurrency?: number };
  docling?: { pdf_mode?: string; layout_engine?: string; drop_page_chrome?: string };
  rerank?: { enabled?: boolean; top_k_in?: number; model?: string };
  engine?: { max_ingest_workers?: number; vector_insert_batch_size?: number };
}

interface KnowledgePanelProps {
  onClose: () => void;
  onDocsChanged?: (count: number) => void;
  onHealthChanged?: (healthy: boolean) => void;
  onToast?: (kind: "error" | "success" | "warning", title: string, message: string) => void;
  knowledgeConfig?: KnowledgeConfigValues;
  onKnowledgeConfigChange?: (config: KnowledgeConfigValues) => void;
  knowledgeReindexNeeded?: boolean;
  knowledgeStale?: boolean;
  knowledgeReindexDeferred?: boolean;
  // ``tasks`` is the new field carrying [{task_id, filename}] pairs so
  // the tile can render real filenames from millisecond 0 — no
  // task_xxx flicker waiting for the first /tasks GET to complete.
  // Backwards-compatible: missing/undefined falls back to placeholder
  // rows that get enriched by polling, same as before.
  onReindex?: () => Promise<{
    count: number;
    task_ids: string[];
    tasks?: { task_id: string; filename: string }[];
  } | null>;
  knowledgeReindexing?: boolean;
  ragProfiles?: Record<string, RagProfileMeta>;
  // Auto-reindex bubbled down from ManagePage: when a draft PATCH
  // server-triggers a reindex (e.g. profile switch with embedding-dim
  // change), the response carries task IDs the user never explicitly
  // asked for. Mirror those into the reindex tile so the user sees
  // progress immediately instead of the empty "documents vanished"
  // window. ``triggerKey`` dedupes re-renders. ``onAutoReindexConsumed``
  // tells the parent we've subscribed and it can clear the trigger.
  autoReindexTrigger?: { taskIds: string[]; total: number; triggerKey: string } | null;
  onAutoReindexConsumed?: () => void;
  // Fired when an in-flight reindex (auto-triggered OR manual) finishes
  // without failures. ManagePage uses it to refresh the
  // ``knowledgeSavedSnapshot`` so the "Reindex needed" banner clears —
  // without this, the snapshot only updates on Publish, and the banner
  // persists indefinitely after a successful auto-reindex.
  onAutoReindexComplete?: () => void;
  // Fired when polling reaches terminal state — regardless of success
  // OR partial failure. Distinct from ``onAutoReindexComplete``:
  //   - onAutoReindexComplete fires ONLY on full success (5/5 done, 0 failed)
  //     → ManagePage refreshes snapshot
  //   - onReindexFinished fires on ANY terminal state (done=true)
  //     → ManagePage clears ``knowledgeReindexing`` so autosave can resume
  // The split exists because the snapshot-refresh side-effect is unsafe
  // on partial-failure (snapshot would claim success that didn't happen),
  // but the autosave-unblock side-effect is safe either way (workers are
  // demonstrably no longer running).
  // Workflow w5i1mbchd / #398 follow-up v2.
  onReindexFinished?: () => void;
  // Which tab to open the modal on. Uncontrolled-with-initial-value: the
  // parent seeds the initial index via this prop, but the user owns tab
  // navigation from frame 1. Defaults to "documents" (back-compat with
  // every existing call site).
  initialTab?: KnowledgeTabName;
  // Lift adaptation-server-error state to the parent so the autosave
  // path (which lives in ManagePage, not here) can push a 422 response
  // body into the panel. Optional + falls back to local state for
  // back-compat with parents that don't yet implement it. When BOTH
  // ``adaptationServerError`` and ``onAdaptationServerError`` are
  // supplied, the panel is fully controlled by the parent; when neither
  // is supplied, the panel manages its own local state (clears reset
  // affordance only — never sees a real 422 today).
  adaptationServerError?: AdaptationServerError | null;
  onAdaptationServerError?: (error: AdaptationServerError | null) => void;
  // Draft autosave status driven by the actual PATCH lifecycle (NOT a
  // setTimeout). The prior implementation here used a 1500ms timer to
  // claim "Saved" whether or not the network call actually returned;
  // the user couldn't distinguish a real save from a silent network
  // failure (the literal bug they hit). Now sourced from ManagePage's
  // PATCH .then/.catch handlers — every state corresponds to a real
  // network event. ``onRetryDraftSave`` re-fires the PATCH from the
  // "failed" state.
  draftSaveStatus?:
    | { kind: "idle" }
    | { kind: "saving" }
    | { kind: "saving-slow" }
    | { kind: "saved" }
    | { kind: "failed"; error: string };
  onRetryDraftSave?: () => void;
  // Dismiss the failure banner WITHOUT retrying (parent resets draftSaveStatus
  // to idle). The banner's close button uses this; the explicit "Retry" button
  // uses onRetryDraftSave.
  onDismissDraftSave?: () => void;
  // Fired on an explicit env-preset "Use" click so the parent can bypass
  // the keystroke-coalesce debounce on the autosave PATCH. No payload —
  // the config change itself goes through onKnowledgeConfigChange.
  onPresetApplied?: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export default function KnowledgePanel({
  onClose,
  onDocsChanged,
  onHealthChanged,
  onToast,
  knowledgeConfig,
  onKnowledgeConfigChange,
  knowledgeReindexNeeded,
  knowledgeStale,
  knowledgeReindexDeferred,
  onReindex,
  knowledgeReindexing,
  ragProfiles,
  autoReindexTrigger,
  onAutoReindexConsumed,
  onAutoReindexComplete,
  onReindexFinished,
  initialTab,
  adaptationServerError: adaptationServerErrorProp,
  onAdaptationServerError,
  draftSaveStatus,
  onRetryDraftSave,
  onDismissDraftSave,
  onPresetApplied,
}: KnowledgePanelProps) {
  // Uncontrolled-with-initial-value: seed from the prop on first render
  // (the modal is unmounted on close so the prop is always fresh on next
  // mount), then the user owns navigation. Do NOT lift to a controlled
  // prop — that would fight the user every time they click another tab.
  const [tabIndex, setTabIndex] = useState(TAB_INDEX[initialTab ?? "documents"]);
  // 422 from PATCH /api/manage/config/draft/knowledge with structured
  // ClientAdaptationError.to_dict() body. Surfaced inside the panel as
  // per-failure-mode notifications (phrase / control / etc).
  //
  // Hybrid controlled/uncontrolled: when the parent passes a value
  // (``adaptationServerError`` prop), the panel is fully controlled —
  // the parent's autosave path (in ManagePage) catches the 422 and
  // pushes the error in. When the prop is undefined, we fall back to
  // local state so the panel still works in standalone test scenarios
  // and back-compat with parents that haven't been updated yet.
  const [adaptationServerErrorLocal, setAdaptationServerErrorLocal] = useState<AdaptationServerError | null>(null);
  const adaptationServerError =
    adaptationServerErrorProp !== undefined ? adaptationServerErrorProp : adaptationServerErrorLocal;
  const setAdaptationServerError = onAdaptationServerError ?? setAdaptationServerErrorLocal;
  void setAdaptationServerError; // exported via the controlled-state contract; called from inside the panel when we wire dismiss UX.
  const knowledgeEnabled = knowledgeConfig?.enabled ?? true;
  const agentLevelEnabled = knowledgeEnabled && (knowledgeConfig?.agent_level_enabled ?? true);
  const sessionLevelEnabled = knowledgeEnabled && (knowledgeConfig?.session_level_enabled ?? true);

  // Documents tab state
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Per-file upload status shown inline in the document list.
  // `displayName` = original browser name (shown in UI), `backendName` = sanitized name (for matching).
  // `progress` is the 0..1 overall fraction sourced from the backend's
  // weighted_pct (parse/embed/insert collapsed into a single bar).
  const [uploadingFiles, setUploadingFiles] = useState<
    {
      name: string;
      backendName?: string;
      status: "uploading" | "success" | "error" | "cancelled";
      error?: string;
      taskId?: string;
      progress?: number;
      // ``queued`` is set when the backend reports ui_phase=queued (file is
      // waiting on the per-collection ingest lock). Lets the UI render a
      // distinct "Queued" state rather than a frozen 3% bar.
      queued?: boolean;
    }[]
  >([]);

  // Per-upload AbortControllers keyed by the backend task_id (or the local
  // entryId before the backend assigns one). Lets us tear down in-flight
  // fetches + polls on modal close or user cancel without leaking timers.
  // A ref instead of state because we never render from this map and we
  // don't want a re-render every time an upload starts or ends.
  const uploadControllersRef = useRef<Map<string, AbortController>>(new Map());

  // Search tab state
  const [searchQuery, setSearchQuery] = useState("");
  const [searchLimit, setSearchLimit] = useState(10);
  const [searchThreshold, setSearchThreshold] = useState(0);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searchTime, setSearchTime] = useState<number | null>(null);
  const [searching, setSearching] = useState(false);
  const [expandedResult, setExpandedResult] = useState<number | null>(null);

  // Health state (used by search tab for status display)
  const [healthy, setHealthy] = useState<boolean | null>(null);

  // Hardware acceleration status (what the live engine actually loaded).
  const [accel, setAccel] = useState<{
    device_label: string;
    fallback_to_cpu: boolean;
    embedding_relevant: boolean;
    key_source?: { required: boolean; source: string };
  } | null>(null);

  // Inline hint when the user tries to put a reserved key (like embedding_model)
  // into the Extra-params JSON. Surfaces a clear "put it in the field above"
  // message right next to the input, instead of letting them save garbage.
  const [extraParamsHint, setExtraParamsHint] = useState<string | null>(null);

  // Test-connection state. result is null=untested, true=ok, false=failed.
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    ok: boolean;
    dim?: number;
    latency_ms?: number;
    error?: string;
  } | null>(null);

  // === Section-level UI helpers (production polish pass) ===

  // Show / hide expert fields. Persisted in localStorage so the user's mode
  // sticks across reloads. Default: simple ("Just work") to avoid drowning
  // first-time users in tuning knobs.
  const [showAdvanced, setShowAdvanced] = useState<boolean>(() => {
    try {
      return localStorage.getItem("cuga.knowledge.advanced") === "1";
    } catch {
      return false;
    }
  });
  const persistShowAdvanced = useCallback((v: boolean) => {
    setShowAdvanced(v);
    try {
      localStorage.setItem("cuga.knowledge.advanced", v ? "1" : "0");
    } catch {
      /* localStorage might be disabled — non-fatal */
    }
  }, []);

  // Live URL validation — conservative: empty is OK (optional fields),
  // anything non-empty must start with http:// or https://. Catches the
  // "pasted a hostname without protocol" mistake before save fires.
  const validateBaseUrl = useCallback((url: string | undefined): string | null => {
    const trimmed = (url ?? "").trim();
    if (!trimmed) return null;
    if (!/^https?:\/\/.+/i.test(trimmed)) {
      return "Must start with http:// or https://";
    }
    return null;
  }, []);
  const baseUrlError = validateBaseUrl(knowledgeConfig?.embedding_base_url);

  // Per-section error/warning state — surfaced as a Tag in the accordion
  // header so the user can see which sections need attention WITHOUT opening
  // them. Errors block save (engine would reject); warnings are advisory.
  type SectionStatus = { kind: "error" | "warning"; reason: string } | null;
  const embeddingsStatus: SectionStatus = (() => {
    const p = knowledgeConfig?.embedding_provider;
    const model = (knowledgeConfig?.embedding_model || "").trim();
    const apiKey = (knowledgeConfig?.embedding_api_key || "").trim();
    if (baseUrlError) return { kind: "error", reason: `Base URL: ${baseUrlError}` };
    if ((p === "litellm" || p === "openrouter") && !model) {
      return { kind: "error", reason: "Model is required" };
    }
    if (p === "openrouter" && !apiKey) {
      return { kind: "error", reason: "API Key is required" };
    }
    if (extraParamsHint) return { kind: "error", reason: extraParamsHint };
    if (testResult && !testResult.ok) return { kind: "warning", reason: "Test connection failed" };
    return null;
  })();
  const chunkingStatus: SectionStatus = (() => {
    const cs = knowledgeConfig?.chunk_size ?? 1000;
    const co = knowledgeConfig?.chunk_overlap ?? 200;
    if (co >= cs) return { kind: "error", reason: "Overlap must be less than chunk size" };
    return null;
  })();

  // Helper to render an accordion title with optional status badge.
  const sectionTitle = useCallback((label: string, status: SectionStatus) => {
    if (!status) return label;
    return (
      <span style={{ display: "inline-flex", alignItems: "center", gap: "0.5rem" }}>
        {label}
        <Tag
          type={status.kind === "error" ? "red" : "magenta"}
          size="sm"
          renderIcon={status.kind === "error" ? ErrorFilled : undefined}
          title={status.reason}
        >
          {status.kind === "error" ? "Needs attention" : "Warning"}
        </Tag>
      </span>
    );
  }, []);

  // Reset state — confirmation modal target. null = no modal open.
  const [resetTarget, setResetTarget] = useState<null | {
    section: string;
    fields: string[];
  }>(null);
  const [defaultsCache, setDefaultsCache] = useState<Record<string, any> | null>(null);
  const performReset = useCallback(async () => {
    if (!resetTarget) return;
    try {
      let defaults = defaultsCache;
      if (!defaults) {
        const res = await api.getKnowledgeDefaults();
        if (!res.ok) {
          onToast?.("error", "Reset failed", `Server returned ${res.status} ${res.statusText}`);
          setResetTarget(null);
          return;
        }
        const j = await res.json();
        defaults = (j.defaults || {}) as Record<string, any>;
        setDefaultsCache(defaults);
      }
      const updates: Record<string, any> = {};
      for (const f of resetTarget.fields) {
        if (f in defaults) updates[f] = defaults[f];
      }
      onKnowledgeConfigChange?.({ ...(knowledgeConfig as any), ...updates });
      onToast?.("info", "Section reset", `Restored ${resetTarget.section} to factory defaults.`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "unknown error";
      onToast?.("error", "Reset failed", msg);
    } finally {
      setResetTarget(null);
    }
  }, [resetTarget, defaultsCache, knowledgeConfig, onKnowledgeConfigChange, onToast]);

  // Save-state indicator: driven by the parent's ``draftSaveStatus`` prop,
  // which is sourced from the actual PATCH lifecycle in ManagePage
  // (.then/.catch handlers). The prior implementation here used a 1500ms
  // setTimeout to claim "Saved" whether or not the network call had
  // returned — the literal bug behind the user's "I changed to Watsonx
  // but logs show bge-small still" report. Now every visible state
  // corresponds to a real network event. The "saved" pill auto-hides
  // after 3s of quiescence via the local ``recentlySaved`` flag below
  // so the user doesn't see a permanent "Saved" sticker.
  const saveState: "idle" | "saving" | "saving-slow" | "saved" | "failed" = (() => {
    if (!draftSaveStatus || draftSaveStatus.kind === "idle") return "idle";
    if (draftSaveStatus.kind === "saving") return "saving";
    if (draftSaveStatus.kind === "saving-slow") return "saving-slow";
    if (draftSaveStatus.kind === "saved") return "saved";
    return "failed";
  })();
  const [recentlySaved, setRecentlySaved] = useState(false);
  useEffect(() => {
    if (saveState === "saved") {
      setRecentlySaved(true);
      const t = setTimeout(() => setRecentlySaved(false), 3000);
      return () => clearTimeout(t);
    }
    setRecentlySaved(false);
  }, [saveState, draftSaveStatus]);

  // Reindex progress state
  const [reindexProgress, setReindexProgress] = useState<{
    taskIds: string[];
    total: number;
    completed: number;
    failed: number;
    tasks: ReindexTask[];
    done: boolean;
    // Wall-clock start for the honest elapsed timer ("Running for 47s").
    // Set once when the operation kicks off; never re-stamped on poll.
    startedAt: number;
  } | null>(null);
  // Ticks once per second so the elapsed-time label re-renders without
  // burning a poll cycle. Cheap setState — guarded by the reindex tile's
  // ``!reindexProgress.done`` so the interval auto-stops on completion.
  const [, setElapsedTick] = useState(0);
  const reindexPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Drive the 1s tick for the elapsed-time label. Only runs while an
  // in-flight reindex exists; auto-clears on completion.
  useEffect(() => {
    if (!reindexProgress || reindexProgress.done) return;
    const id = setInterval(() => setElapsedTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [reindexProgress?.startedAt, reindexProgress?.done]);

  // Auto-dismiss the success notification ~3.5s after a clean reindex.
  // Why: the "Re-index complete" green InlineNotification used to stay
  // up until the user clicked X. If the user changed profile right
  // after a successful reindex, the new "Re-index to apply your
  // changes" banner was suppressed by the still-visible success
  // (the banner trigger gates on ``!reindexProgress``, so the next
  // need-reindex signal couldn't surface until the success was
  // dismissed). Auto-dismiss on success only — on failure (failed > 0)
  // we keep the banner up because the user needs to see what failed
  // and the close-X is the way they acknowledge the warning.
  //
  // Edge cases this handles:
  //   - User changes profile mid-celebration: success dismisses on its
  //     own, the new "Re-index to apply" banner takes its place.
  //   - User clicks X during the 3.5s window: cleanup clears the timer,
  //     no late setState.
  //   - User closes + reopens modal: reindexProgress is local state,
  //     resets to null on remount, so the success can't re-fire.
  //   - User starts another reindex during the window: setReindexProgress
  //     for the new run resets ``done`` to false; the auto-dismiss
  //     effect re-evaluates and finds ``!done`` so it doesn't fire.
  useEffect(() => {
    if (!reindexProgress?.done) return;
    if (reindexProgress.failed > 0) return;
    const t = setTimeout(() => setReindexProgress(null), 3500);
    return () => clearTimeout(t);
  }, [reindexProgress?.done, reindexProgress?.failed]);

  // Snapshot the documents at reindex-start so the list keeps rendering
  // the user's files (with their ingest dates) throughout the upgrade —
  // not just during the "Preparing your new reading model" window when
  // ``documents`` from the server is briefly empty (the new-hash
  // collection has no rows yet). Cleared on completion; the next poll
  // refresh of ``documents`` then takes over.
  const [documentsBeforeReindex, setDocumentsBeforeReindex] = useState<KnowledgeDocument[] | null>(null);
  useEffect(() => {
    if (reindexProgress && !reindexProgress.done) {
      // First transition into a running reindex — capture the current
      // documents once, then leave the snapshot alone until completion.
      if (documentsBeforeReindex === null && documents.length > 0) {
        setDocumentsBeforeReindex(documents);
      }
    } else if (documentsBeforeReindex !== null) {
      // Reindex finished or cleared — drop the snapshot so the live
      // ``documents`` array drives subsequent renders.
      setDocumentsBeforeReindex(null);
    }
  }, [reindexProgress?.done, reindexProgress?.taskIds.length, documents.length]);

  // Stabilize callback props with refs to avoid re-fetch loops when parent re-renders
  const onDocsChangedRef = useRef(onDocsChanged);
  onDocsChangedRef.current = onDocsChanged;
  const onHealthChangedRef = useRef(onHealthChanged);
  onHealthChangedRef.current = onHealthChanged;

  // -------------------------------------------------------------------------
  // Data fetching
  // -------------------------------------------------------------------------
  const loadDocuments = useCallback(async () => {
    if (!agentLevelEnabled) {
      setDocuments([]);
      onDocsChangedRef.current?.(0);
      return;
    }
    try {
      const res = await api.listKnowledgeDocuments();
      if (res.ok) {
        const data = await res.json();
        const docs = data.documents || [];
        setDocuments(docs);
        onDocsChangedRef.current?.(docs.length);
      }
    } catch (e) {
      console.error("Failed to load documents:", e);
    }
  }, [agentLevelEnabled]);

  const checkHealth = useCallback(async () => {
    try {
      const res = await api.getKnowledgeHealth();
      if (res.ok) {
        const data = await res.json();
        setHealthy(data.healthy);
        onHealthChangedRef.current?.(data.healthy);
      }
    } catch {
      setHealthy(false);
      onHealthChangedRef.current?.(false);
    }
  }, []);

  // Start the knowledge engine on-demand (called when user toggles ON while disconnected)
  const ensureEngineStarted = useCallback(async () => {
    try {
      setHealthy(null); // show "Checking" state
      const res = await api.enableKnowledge();
      if (res.ok) {
        // Poll health until ready (engine needs time for warmup)
        const poll = setInterval(async () => {
          try {
            const hRes = await api.getKnowledgeHealth();
            if (hRes.ok) {
              const hData = await hRes.json();
              if (hData.healthy) {
                clearInterval(poll);
                setHealthy(true);
                onHealthChangedRef.current?.(true);
                loadDocuments();
              }
            }
          } catch { /* keep polling */ }
        }, 2000);
        // Stop polling after 60s
        setTimeout(() => clearInterval(poll), 60000);
      }
    } catch {
      setHealthy(false);
      onHealthChangedRef.current?.(false);
    }
  }, [loadDocuments]);

  // Initial load
  useEffect(() => {
    loadDocuments();
    checkHealth();
  }, [loadDocuments, checkHealth]);

  // Detected embedding-provider presets from the host's environment
  // (.env / shell). Drives the "Quick setup from environment" panel
  // so a user with WATSONX_APIKEY + WATSONX_URL + WATSONX_PROJECT_ID
  // already set sees Watsonx as ready-to-apply with one click. The
  // endpoint returns booleans + suggested config only — never the
  // raw env values. Fetched once on mount.
  interface EnvPreset {
    id: string;
    label: string;
    default_provider: string;
    default_model: string;
    ready: boolean;
    env_vars: Record<string, boolean>;
    missing: string[];
  }
  const [envPresets, setEnvPresets] = useState<EnvPreset[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getKnowledgeEnvPresets();
        if (!res.ok || cancelled) return;
        const j = await res.json();
        if (cancelled) return;
        setEnvPresets(Array.isArray(j?.presets) ? j.presets : []);
      } catch {
        // Fail-quiet: the panel just hides — user can still configure manually.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Fetch hardware acceleration status. Re-fetches when the user toggles
  // use_gpu or switches embedding provider — both can change what the
  // live engine actually loaded.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getKnowledgeAccelerator();
        if (!res.ok) return;
        const j = await res.json();
        if (cancelled || !j.available) {
          if (!cancelled) setAccel(null);
          return;
        }
        setAccel({
          device_label: j.device_label,
          fallback_to_cpu: !!j.fallback_to_cpu,
          embedding_relevant: !!j.embedding_relevant,
          key_source: j.key_source,
        });
      } catch {
        if (!cancelled) setAccel(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    knowledgeConfig?.use_gpu,
    knowledgeConfig?.embedding_provider,
    knowledgeConfig?.embedding_model,
    knowledgeConfig?.embedding_api_key,
  ]);

  // Reset test result when any embedding-related field changes (stale result
  // would be misleading — a green check on yesterday's key isn't trustworthy
  // after the user edits something).
  useEffect(() => {
    setTestResult(null);
  }, [
    knowledgeConfig?.embedding_provider,
    knowledgeConfig?.embedding_model,
    knowledgeConfig?.embedding_api_key,
    knowledgeConfig?.embedding_base_url,
    knowledgeConfig?.embedding_extra_params,
  ]);

  // Run a single embed call against the configured provider — surfaces auth /
  // network / model failures BEFORE the user uploads anything.
  const handleTestConnection = useCallback(async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.testEmbeddingsConnection({
        provider: knowledgeConfig?.embedding_provider || "",
        model: knowledgeConfig?.embedding_model || "",
        api_key: knowledgeConfig?.embedding_api_key || "",
        base_url: knowledgeConfig?.embedding_base_url || "",
        extra_params: knowledgeConfig?.embedding_extra_params || {},
      });
      const j = await res.json();
      setTestResult({
        ok: !!j.ok,
        dim: j.dim,
        latency_ms: j.latency_ms,
        error: j.error,
      });
    } catch (e: any) {
      setTestResult({ ok: false, error: String(e?.message || e) });
    } finally {
      setTesting(false);
    }
  }, [knowledgeConfig]);

  // Cleanup reindex polling on unmount
  useEffect(() => {
    return () => {
      if (reindexPollRef.current) clearInterval(reindexPollRef.current);
    };
  }, []);

  // -------------------------------------------------------------------------
  // Reindex with progress tracking
  // -------------------------------------------------------------------------
  // Arms the in-flight reindex tile + 2s polling for the given task IDs.
  // Used by both the manual Reindex button (via ``startReindexWithProgress``,
  // which POSTs first and then calls this with the returned task IDs) AND
  // the server-side auto-reindex path (via ``autoReindexTrigger`` from the
  // PATCH /draft response — the tasks already exist server-side so we just
  // subscribe). Extracted from the POST flow so a profile-switch migration
  // gets identical progress UI without the user having to click Reindex
  // a second time.
  // #402: merge backend tasks by task_id over the placeholder list so a
  // partial poll (some tasks not yet visible in metadata) doesn't drop
  // placeholder rows — the tile keeps showing all N rows from the moment
  // Re-index fires, enriched with filename + status as they arrive.
  const mergeTasksById = useCallback(
    (taskIds: string[], placeholders: ReindexTask[], backendTasks: ReindexTask[]): ReindexTask[] => {
      const byId = new Map<string, ReindexTask>(placeholders.map((t) => [t.task_id, t]));
      for (const t of backendTasks) {
        const enriched = { ...t, filename: getReindexTaskFilename(t) };
        const prev = byId.get(t.task_id);
        byId.set(t.task_id, prev ? { ...prev, ...enriched } : enriched);
      }
      return taskIds.map(
        (id) => byId.get(id) ?? { task_id: id, status: "pending" as const },
      );
    },
    [],
  );

  const armReindexFromTaskIds = useCallback(
    async (
      taskIds: string[],
      total: number,
      seedTasks?: { task_id: string; filename: string }[],
    ) => {
    if (!taskIds.length) return;
    // Seed placeholders with filenames if the POST response carried
    // them (#402 production sweep). The previous code rendered N rows
    // of ``task_xxx`` for up to one full polling interval (~2s) before
    // the first /tasks GET landed and supplied the filenames; with
    // the seed, the tile shows real names from frame 0.
    const seedById = new Map<string, string>(
      (seedTasks ?? []).map((t) => [t.task_id, t.filename]),
    );
    const placeholders: ReindexTask[] = taskIds.map(
      (id): ReindexTask => {
        const seedFilename = seedById.get(id);
        return seedFilename
          ? { task_id: id, status: "pending" as const, filename: seedFilename }
          : { task_id: id, status: "pending" as const };
      },
    );
    let initialTasks: ReindexTask[] = placeholders;
    try {
      const res = await api.getKnowledgeTasks();
      if (res.ok) {
        const data = await res.json();
        const allTasks: ReindexTask[] = data.tasks ?? [];
        const relevantTasks = allTasks.filter((t: ReindexTask) => taskIds.includes(t.task_id));
        initialTasks = mergeTasksById(taskIds, placeholders, relevantTasks);
      }
    } catch {
      // Fall back to placeholders; polling will enrich as filenames load.
    }
    setReindexProgress({
      taskIds,
      total: total || taskIds.length,
      completed: 0,
      failed: 0,
      tasks: initialTasks,
      done: false,
      startedAt: Date.now(),
    });

    // Poll task statuses every 2s
    if (reindexPollRef.current) clearInterval(reindexPollRef.current);
    reindexPollRef.current = setInterval(async () => {
      try {
        const res = await api.getKnowledgeTasks();
        if (!res.ok) return;
        const data = await res.json();
        const allTasks: ReindexTask[] = data.tasks ?? [];
        const relevantTasks = allTasks.filter((t: ReindexTask) => taskIds.includes(t.task_id));
        const completed = relevantTasks.filter((t: ReindexTask) => t.status === "completed").length;
        // Count "cancelled" as terminal/failed (#4). A superseded worker writes
        // status="cancelled" (engine ReindexSupersededError); if the tile only
        // counted "failed", completed+failed could never reach taskIds.length,
        // the poll would never reach done, onReindexFinished would never fire,
        // and the tile would spin forever with knowledgeReindexing stuck true.
        // The backend deferred flip already treats cancelled as failed.
        const failed = relevantTasks.filter(
          (t: ReindexTask) => t.status === "failed" || t.status === "cancelled",
        ).length;
        const done = completed + failed >= taskIds.length;

        setReindexProgress((prev) => {
          const mergedTasks = mergeTasksById(taskIds, prev?.tasks ?? placeholders, relevantTasks);
          // [#402] Per-poll proof of life. If ``rowsRendered`` ever drops
          // below ``taskIds`` length, the merge regressed. If
          // ``rowsWithFilename`` stays below taskIds.length past ~1s
          // after tile-arm, the backend isn't populating ``file_tasks``
          // — different bug.
          return {
            taskIds,
            total: taskIds.length,
            completed,
            failed,
            // Merge by id so transient polls that don't see every task
            // (e.g. backend just created task N+1 between our HTTP call
            // and its insert) don't shrink the tile to fewer rows.
            tasks: mergedTasks,
            done,
            startedAt: prev?.startedAt ?? Date.now(),
          };
        });

        if (done) {
          if (reindexPollRef.current) clearInterval(reindexPollRef.current);
          reindexPollRef.current = null;
          // Refresh document list after reindex completes
          loadDocuments();
          checkHealth();
          // #398 follow-up v2 (workflow w5i1mbchd): fire onReindexFinished
          // ALWAYS at terminal state — both success AND partial-failure
          // mean "workers stopped, parent can resume autosave PATCHes".
          // Distinct from onAutoReindexComplete which is success-only.
          onReindexFinished?.();
          if (failed === 0) {
            onToast?.("success", "Re-index complete", `${completed} document(s) re-indexed successfully.`);
            // Tell the parent to refresh its saved-config snapshot so the
            // "Reindex needed" banner (driven by snapshot-vs-current diff)
            // clears. Without this, an auto-reindex that fully succeeded
            // would still leave the banner up until the next Publish —
            // confusing because the engine HAS already re-embedded.
            onAutoReindexComplete?.();
          } else {
            // Partial-failure toast: be loud about the data-integrity story.
            // Production sweep (workflow w5i1mbchd): the deferred flip is
            // now STRICT (refuses promotion unless ALL tasks succeeded), so
            // partial failure means the user's previous embedder is STILL
            // active. Search keeps working on the old vectors. The user
            // must fix the failing files and Re-index again, or revert.
            // Crucially: DO NOT call onAutoReindexComplete — that would
            // refresh the saved-config snapshot to the NEW (unapplied)
            // config and clear the "Re-index needed" banner, both of
            // which would lie about the engine's actual state.
            const failedNames = relevantTasks
              .filter((t) => t.status === "failed" || t.status === "cancelled")
              .map((t) => t.filename || t.task_id)
              .slice(0, 3)
              .join(", ");
            const more = failed > 3 ? ` (+${failed - 3} more)` : "";
            onToast?.(
              "error",
              "Re-index didn't complete",
              `${completed}/${taskIds.length} succeeded; ${failed} failed: ${failedNames}${more}. ` +
                `Your previous embedder configuration is still active. ` +
                `Fix the failing file(s) and Re-index again, or revert your config.`,
            );
          }
        }
      } catch {
        // Polling failure is transient, keep trying
      }
    }, 2000);
  }, [loadDocuments, checkHealth, onToast]);

  // Manual Reindex button path: POST first to get task IDs, then arm.
  // Thread ``result.tasks`` (POST response carries [{task_id, filename}]
  // pairs after the #402 production sweep) so the tile renders real
  // filenames from frame 0 — no task_xxx placeholder window.
  const startReindexWithProgress = useCallback(async () => {
    if (!onReindex) return;
    const result = await onReindex();
    if (!result || !result.task_ids?.length) return;
    await armReindexFromTaskIds(result.task_ids, result.count, result.tasks);
  }, [onReindex, armReindexFromTaskIds]);

  // Server-side auto-reindex path: ManagePage extracts task IDs from a
  // PATCH /draft/knowledge response and pushes them down here. We arm
  // the tile + polling without an extra POST (the tasks already exist
  // on the engine — duplicating the POST would drop their vectors
  // again and re-spawn workers). ``triggerKey`` dedupes; arming once
  // per distinct payload and telling the parent we consumed it so the
  // prop can be cleared.
  const consumedTriggerKeyRef = useRef<string | null>(null);
  useEffect(() => {
    const t = autoReindexTrigger;
    if (!t || t.taskIds.length === 0) return;
    if (consumedTriggerKeyRef.current === t.triggerKey) return;
    consumedTriggerKeyRef.current = t.triggerKey;
    armReindexFromTaskIds(t.taskIds, t.total);
    onAutoReindexConsumed?.();
  }, [autoReindexTrigger?.triggerKey, armReindexFromTaskIds, onAutoReindexConsumed]);

  // -------------------------------------------------------------------------
  // Upload handlers
  // -------------------------------------------------------------------------
  const handleUpload = async (files: FileList | File[]) => {
    if (!agentLevelEnabled) {
      onToast?.("warning", "Agent knowledge is disabled", "Enable agent-level knowledge in Settings to upload permanent documents.");
      return;
    }
    const fileArray = Array.from(files);
    if (fileArray.length === 0) return;

    // Create a unique ID per file entry
    const entries = fileArray.map((f) => ({
      name: f.name,
      status: "uploading" as const,
      taskId: `upload_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      backendName: undefined as string | undefined,
    }));
    setUploadingFiles((prev) => [...prev.filter((f) => f.status !== "success"), ...entries]);

    // Refresh the doc list immediately on each completion. The 500ms
    // trailing-edge debounce previously here made every fresh upload
    // feel laggy ("file processed but not yet visible") for the common
    // single-file case; the resumePoll path already calls
    // loadDocuments() inline, this matches. listKnowledgeDocuments is a
    // sub-50ms sqlite read so N near-simultaneous completions just fire
    // N GETs — no batching needed.
    const scheduleRefresh = () => {
      loadDocuments();
    };

    // Upload one file: POST with wait=false (202 + task_id), then poll the
    // task endpoint until terminal. The poll response's weighted_pct (0..1)
    // drives the inline progress bar — stage internals (parse/embed/insert)
    // are deliberately collapsed server-side so the UI shows just one bar.
    //
    // The AbortController is owned by the controllers map so modal-close /
    // user-cancel can tear down the upload + poll cleanly. We re-key the
    // map from the temporary entryId to the backend task_id as soon as we
    // learn it, so cancel-by-task-id works for both fresh uploads and
    // resumed-on-mount uploads.
    const uploadOne = async (file: File, entryId: string) => {
      const controller = new AbortController();
      uploadControllersRef.current.set(entryId, controller);
      const signal = controller.signal;

      // Guarded setState: every late callback must check signal.aborted so
      // we never write to state after the modal unmounts or after a cancel.
      const safeSet = (
        updater: (prev: typeof uploadingFiles) => typeof uploadingFiles,
      ): void => {
        if (signal.aborted) return;
        setUploadingFiles(updater);
      };

      let backendTaskId: string | undefined;
      try {
        const res = await api.uploadKnowledgeDocument(file, true, false, signal);
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }));
          safeSet((prev) =>
            prev.map((f) => f.taskId === entryId
              ? { ...f, status: "error" as const, error: err.detail || "Failed" }
              : f)
          );
          return;
        }
        const queued = await res.json();
        const queuedTask = queued.tasks ? queued.tasks[0] : queued;
        backendTaskId = queuedTask?.task_id;
        const backendName: string | undefined = queuedTask?.filename;
        if (!backendTaskId) {
          // Server returned a synchronous result (wait was ignored or legacy build).
          // Treat as final to stay compatible.
          const ok = queuedTask?.status === "completed";
          safeSet((prev) =>
            prev.map((f) => f.taskId === entryId
              ? ok
                ? { ...f, backendName, status: "success" as const, progress: 1 }
                : { ...f, backendName, status: "error" as const, error: "Ingestion failed" }
              : f)
          );
          if (ok) {
            scheduleRefresh();
            setTimeout(() => {
              safeSet((prev) => prev.filter((f) => f.taskId !== entryId));
            }, 3000);
          }
          return;
        }

        // Re-key the controllers map from entryId to the canonical backend
        // task_id so the cancel button (which only knows the task_id) can
        // find the controller.
        uploadControllersRef.current.delete(entryId);
        uploadControllersRef.current.set(backendTaskId, controller);

        // Persist for resume-across-reload. We only enter the registry once
        // we have a real backend task_id — there's nothing to resume before.
        addActiveUpload({
          taskId: backendTaskId,
          name: file.name,
          createdAt: Date.now(),
        });

        // Seed the entry with the backend task_id + a tiny visible progress
        // so the bar starts moving immediately on slow networks.
        safeSet((prev) =>
          prev.map((f) => f.taskId === entryId
            ? { ...f, taskId: backendTaskId, backendName, progress: Math.max(f.progress ?? 0, 0.02) }
            : f)
        );

        await pollUntilTerminal(backendTaskId, signal, scheduleRefresh);
      } catch (e: unknown) {
        if (isAbortError(e) || signal.aborted) {
          // Either the modal unmounted (no state writes wanted — safeSet is a
          // no-op now) or the user clicked cancel (the cancel handler already
          // wrote the cancelled state). Nothing to do.
          return;
        }
        const message = e instanceof Error ? e.message : "Upload failed";
        safeSet((prev) =>
          prev.map((f) => f.taskId === entryId || f.taskId === backendTaskId
            ? { ...f, status: "error" as const, error: message }
            : f)
        );
      } finally {
        // Clean both possible keys — entryId before re-keying, task_id after.
        // Persist-clear only if we actually finished (terminal). Cancel /
        // unmount paths leave the entry for later resume.
        const key = backendTaskId ?? entryId;
        if (uploadControllersRef.current.get(key) === controller) {
          uploadControllersRef.current.delete(key);
        }
      }
    };

    // Extracted so resume-on-mount can share the same poll loop without
    // duplicating logic. ``backendTaskId`` is known up front; the local
    // entry must already exist with that taskId before this is called.
    const pollUntilTerminal = async (
      backendTaskId: string,
      signal: AbortSignal,
      onCompleted: () => void,
    ): Promise<void> => {
      const safeSet = (
        updater: (prev: typeof uploadingFiles) => typeof uploadingFiles,
      ): void => {
        if (signal.aborted) return;
        setUploadingFiles(updater);
      };

      // 600ms is below the human-perception threshold for "live"; backend
      // does no work on these calls beyond a sqlite SELECT + weighted_pct.
      const POLL_INTERVAL_MS = 600;
      const MAX_POLLS = 10 * 60; // 10 min hard cap

      for (let i = 0; i < MAX_POLLS; i++) {
        try {
          await abortableSleep(POLL_INTERVAL_MS, signal);
        } catch {
          // Aborted — modal closed or user cancelled. Bail without touching state.
          return;
        }

        let taskResp: Response;
        try {
          taskResp = await api.getKnowledgeTaskStatus(backendTaskId, signal);
        } catch (e) {
          if (isAbortError(e) || signal.aborted) return;
          continue; // transient network blip — keep polling
        }
        if (!taskResp.ok) {
          if (taskResp.status === 404) {
            removeActiveUpload(backendTaskId);
            safeSet((prev) =>
              prev.map((f) => f.taskId === backendTaskId
                ? { ...f, status: "error" as const, error: "Task not found" }
                : f)
            );
            return;
          }
          continue;
        }
        const task = await taskResp.json().catch(() => null);
        if (!task) continue;

        const pct: number = typeof task.weighted_pct === "number" ? task.weighted_pct : 0;
        const status: string = task.status;
        const uiPhase: string | undefined = task.ui_phase;

        if (status === "completed") {
          removeActiveUpload(backendTaskId);
          safeSet((prev) =>
            prev.map((f) => f.taskId === backendTaskId
              ? { ...f, status: "success" as const, progress: 1, backendName: task.filename ?? f.backendName }
              : f)
          );
          onCompleted();
          setTimeout(() => {
            safeSet((prev) => prev.filter((f) => f.taskId !== backendTaskId));
          }, 3000);
          return;
        }
        if (status === "failed" || status === "cancelled") {
          removeActiveUpload(backendTaskId);
          const fileInfo = Object.values(task.file_tasks || {})[0] as { error?: string } | undefined;
          safeSet((prev) =>
            prev.map((f) => f.taskId === backendTaskId
              ? status === "cancelled"
                ? { ...f, status: "cancelled" as const, error: undefined }
                : { ...f, status: "error" as const, error: fileInfo?.error || "Ingestion failed" }
              : f)
          );
          return;
        }

        // Still in flight — only advance progress forward (never go backwards
        // because of a stale-read race with a later write). Carry through
        // the ui_phase so the row can render "Queued" vs "Processing"
        // distinctly even though both are non-terminal statuses.
        const isQueued = uiPhase === "queued";
        safeSet((prev) =>
          prev.map((f) => f.taskId === backendTaskId
            ? { ...f, progress: Math.max(f.progress ?? 0, pct), queued: isQueued }
            : f)
        );
      }

      // Timed out without a terminal status — surface clearly. The task may
      // still be running server-side; we leave the localStorage entry so a
      // future reload can re-attach.
      safeSet((prev) =>
        prev.map((f) => f.taskId === backendTaskId
          ? { ...f, status: "error" as const, error: "Timed out waiting for ingest" }
          : f)
      );
    };

    // Fire all uploads in parallel — each resolves independently
    await Promise.allSettled(entries.map((entry, i) => uploadOne(fileArray[i], entry.taskId)));
  };

  // Reusable poll loop for resume-on-mount. Defined inside the component
  // so it closes over setUploadingFiles. Mirrors ``pollUntilTerminal``
  // inside ``handleUpload`` — kept as a thin shim because resume doesn't
  // have a ``scheduleRefresh`` debouncer in scope.
  const resumePoll = useCallback(
    async (backendTaskId: string, signal: AbortSignal): Promise<void> => {
      const safeSet = (
        updater: (prev: typeof uploadingFiles) => typeof uploadingFiles,
      ): void => {
        if (signal.aborted) return;
        setUploadingFiles(updater);
      };
      const POLL_INTERVAL_MS = 600;
      const MAX_POLLS = 10 * 60;

      for (let i = 0; i < MAX_POLLS; i++) {
        try {
          await abortableSleep(POLL_INTERVAL_MS, signal);
        } catch {
          return;
        }
        let resp: Response;
        try {
          resp = await api.getKnowledgeTaskStatus(backendTaskId, signal);
        } catch (e) {
          if (isAbortError(e) || signal.aborted) return;
          continue;
        }
        if (!resp.ok) {
          if (resp.status === 404) {
            removeActiveUpload(backendTaskId);
            safeSet((prev) =>
              prev.map((f) => f.taskId === backendTaskId
                ? { ...f, status: "error" as const, error: "Task not found" }
                : f)
            );
            return;
          }
          continue;
        }
        const task = await resp.json().catch(() => null);
        if (!task) continue;
        const pct: number = typeof task.weighted_pct === "number" ? task.weighted_pct : 0;
        const status: string = task.status;
        const uiPhase: string | undefined = task.ui_phase;

        if (status === "completed") {
          removeActiveUpload(backendTaskId);
          safeSet((prev) =>
            prev.map((f) => f.taskId === backendTaskId
              ? { ...f, status: "success" as const, progress: 1, backendName: task.filename ?? f.backendName }
              : f)
          );
          loadDocuments();
          setTimeout(() => {
            safeSet((prev) => prev.filter((f) => f.taskId !== backendTaskId));
          }, 3000);
          return;
        }
        if (status === "failed" || status === "cancelled") {
          removeActiveUpload(backendTaskId);
          const fileInfo = Object.values(task.file_tasks || {})[0] as { error?: string } | undefined;
          safeSet((prev) =>
            prev.map((f) => f.taskId === backendTaskId
              ? status === "cancelled"
                ? { ...f, status: "cancelled" as const, error: undefined }
                : { ...f, status: "error" as const, error: fileInfo?.error || "Ingestion failed" }
              : f)
          );
          return;
        }
        const isQueued = uiPhase === "queued";
        safeSet((prev) =>
          prev.map((f) => f.taskId === backendTaskId
            ? { ...f, progress: Math.max(f.progress ?? 0, pct), queued: isQueued }
            : f)
        );
      }
      safeSet((prev) =>
        prev.map((f) => f.taskId === backendTaskId
          ? { ...f, status: "error" as const, error: "Timed out waiting for ingest" }
          : f)
      );
    },
    [loadDocuments],
  );

  // Cancel an in-flight upload. Best-effort by design: the backend's
  // cancel_event is only checked between stages, so an embed in progress
  // may complete anyway. We always flip the row to "cancelled" locally —
  // if the ingest happens to finish, a duplicate-upload will 409 cleanly.
  const handleCancelUpload = useCallback(
    async (taskId: string | undefined): Promise<void> => {
      if (!taskId) return;
      // Abort first so the poll loop stops immediately and stops fighting
      // us for setUploadingFiles ownership.
      const controller = uploadControllersRef.current.get(taskId);
      if (controller) {
        controller.abort();
        uploadControllersRef.current.delete(taskId);
      }
      setUploadingFiles((prev) =>
        prev.map((f) => f.taskId === taskId
          ? { ...f, status: "cancelled" as const, error: undefined }
          : f)
      );
      removeActiveUpload(taskId);
      // Fire-and-forget the server cancel — non-blocking by design.
      try {
        await api.cancelKnowledgeTask(taskId);
      } catch {
        // Server-side cancel is best-effort. If the network ate the
        // request the row is already cancelled locally; the next file
        // ingest of the same name will 409 if needed.
      }
    },
    [],
  );

  // --- Resume on mount + teardown on unmount ----------------------------
  // Resume: read localStorage, validate each entry against the server, and
  // re-attach a poll for any task still in flight. Done once on mount.
  // Teardown: abort every controller so closing the modal stops timers,
  // sleeps, and in-flight fetches in one synchronous turn.
  useEffect(() => {
    const persisted = loadActiveUploads();
    if (persisted.length === 0) {
      // Still register the cleanup even with no persisted entries — fresh
      // uploads created during the modal session must abort on close.
      return () => {
        uploadControllersRef.current.forEach((c) => c.abort());
        uploadControllersRef.current.clear();
      };
    }

    let cancelled = false;
    (async () => {
      for (const entry of persisted) {
        if (cancelled) return;
        let task: { task_id?: string; status?: string; filename?: string; weighted_pct?: number; ui_phase?: string } | null = null;
        try {
          const resp = await api.getKnowledgeTaskStatus(entry.taskId);
          if (resp.status === 404) {
            removeActiveUpload(entry.taskId);
            continue;
          }
          if (!resp.ok) continue;
          task = await resp.json().catch(() => null);
        } catch {
          // Network issue on initial check — leave the entry alone so a
          // later mount can try again.
          continue;
        }
        if (!task || cancelled) continue;
        const status = task.status;
        if (status === "completed" || status === "failed" || status === "cancelled") {
          // Terminal — clean up the registry; the indexed-documents list
          // will already show successful ones.
          removeActiveUpload(entry.taskId);
          continue;
        }
        // Still in flight — surface the row and start polling. Merge by
        // taskId so a concurrent fresh upload of the same task (extremely
        // unlikely but defended) doesn't get duplicated.
        const controller = new AbortController();
        uploadControllersRef.current.set(entry.taskId, controller);
        setUploadingFiles((prev) => {
          if (prev.some((f) => f.taskId === entry.taskId)) return prev;
          return [
            ...prev,
            {
              name: entry.name,
              backendName: task?.filename,
              taskId: entry.taskId,
              status: "uploading" as const,
              progress: typeof task?.weighted_pct === "number" ? task.weighted_pct : 0,
              queued: task?.ui_phase === "queued",
            },
          ];
        });
        // Don't await — let all resumed polls run in parallel.
        void resumePoll(entry.taskId, controller.signal);
      }
    })();

    return () => {
      cancelled = true;
      uploadControllersRef.current.forEach((c) => c.abort());
      uploadControllersRef.current.clear();
    };
    // Intentionally mount-only: resuming on every render would re-fire
    // server validations for every state change. resumePoll is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    if (e.dataTransfer.files.length > 0) {
      handleUpload(e.dataTransfer.files);
    }
  };

  // -------------------------------------------------------------------------
  // Delete handler
  // -------------------------------------------------------------------------
  const handleDelete = async (filename: string) => {
    if (!agentLevelEnabled) {
      onToast?.("warning", "Agent knowledge is disabled", "Enable agent-level knowledge in Settings to manage permanent documents.");
      return;
    }
    try {
      const res = await api.deleteKnowledgeDocument(filename);
      if (res.ok) {
        onToast?.("success", "Document deleted", filename);
        setDeleteConfirm(null);
        loadDocuments();
      } else {
        const err = await res.json().catch(() => ({ detail: "Delete failed" }));
        onToast?.("error", "Delete failed", err.detail || err.error || "Unknown error");
      }
    } catch (e: any) {
      onToast?.("error", "Delete failed", e.message || "Network error");
    }
  };

  // -------------------------------------------------------------------------
  // Search handler
  // -------------------------------------------------------------------------
  const handleSearch = async () => {
    if (!agentLevelEnabled) {
      onToast?.("warning", "Agent knowledge is disabled", "Enable agent-level knowledge in Settings to search permanent documents.");
      return;
    }
    if (!searchQuery.trim()) return;
    setSearching(true);
    setSearchResults([]);
    setSearchTime(null);
    setExpandedResult(null);
    try {
      const res = await api.searchKnowledge(searchQuery, searchLimit, searchThreshold);
      if (res.ok) {
        const data = await res.json();
        setSearchResults(data.results || []);
        setSearchTime(data.query_time_ms ?? null);
      } else {
        onToast?.("error", "Search failed", "Could not search knowledge base");
      }
    } catch (e: any) {
      onToast?.("error", "Search failed", e.message || "Network error");
    } finally {
      setSearching(false);
    }
  };

  // -------------------------------------------------------------------------
  // Score tag type helper
  // -------------------------------------------------------------------------
  const scoreTagType = (score: number): "green" | "warm-gray" | "red" => {
    if (score > 0.7) return "green";
    if (score > 0.4) return "warm-gray";
    return "red";
  };
  const scoreColor = (score: number): string => {
    if (score > 0.7) return "#24a148";
    if (score > 0.4) return "#f1c21b";
    return "#da1e28";
  };

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------
  return (
    <>
      <ComposedModal
        open
        onClose={onClose}
        size="lg"
        isFullWidth
        preventCloseOnClickOutside
        onSubmit={(e: React.FormEvent) => e.preventDefault()}
        ref={(node: HTMLElement | null) => {
          // Carbon's ComposedModal renders an inner <form>. Intercept submit
          // so that no button / NumberInput stepper / Enter key causes page
          // navigation.
          if (node) {
            const form = node.querySelector("form");
            if (form && !form.dataset.patched) {
              form.addEventListener("submit", (ev) => ev.preventDefault());
              form.dataset.patched = "1";
            }
          }
        }}
      >
        <ModalHeader title="Knowledge Base" buttonOnClick={onClose} />

        <ModalBody hasScrollingContent>
          <Theme theme="white">
            <Stack gap={6} style={{ paddingBottom: "2rem" }}>
              <Tabs selectedIndex={tabIndex} onChange={({ selectedIndex }) => setTabIndex(selectedIndex)}>
                <TabList aria-label="Knowledge sections">
                  <Tab>Documents ({documents.length})</Tab>
                  <Tab>Search Test</Tab>
                  <Tab>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: "0.375rem" }}>
                      Knowledge harness
                      {/* Carbon-for-AI: AILabel slug marks this tab as AI-affecting */}
                      <AILabel autoAlign size="mini" aiText="AI" textLabel="Knowledge harness">
                        <AILabelContent>
                          <h6 style={{ marginTop: 0 }}>Knowledge harness</h6>
                          <p style={{ fontSize: "0.8125rem", margin: "0.5rem 0 0" }}>
                            Operator rules + glossary appended to the knowledge-agent system prompt
                            for every search. Steers phrasing, hedging, citation, and synonym handling
                            without changing retrieval.
                          </p>
                        </AILabelContent>
                      </AILabel>
                      {(knowledgeConfig?.client_adaptation_text ?? "").trim().length > 0 && (
                        <Tag type="green" size="sm">Active</Tag>
                      )}
                    </span>
                  </Tab>
                  <Tab>Settings</Tab>
                </TabList>
                <TabPanels>
                  {/* ======================================================= */}
                  {/* DOCUMENTS TAB */}
                  {/* ======================================================= */}
                  <TabPanel>
                    <Stack gap={5} style={{ paddingTop: "1rem" }}>
                      {!agentLevelEnabled && (
                        <Tile>
                          <Stack gap={2}>
                            <h4 style={{ fontSize: "0.875rem", fontWeight: 600, margin: 0 }}>
                              Agent-level knowledge is disabled
                            </h4>
                            <p style={{ color: "var(--cds-text-secondary)", margin: 0, fontSize: "0.8125rem", lineHeight: 1.5 }}>
                              Permanent documents are unavailable while agent-level knowledge is off. Re-enable it in Settings to upload, index, and search documents for this agent.
                            </p>
                          </Stack>
                        </Tile>
                      )}

                      {agentLevelEnabled && (
                        <>
                      {/* Upload zone */}
                      <Tile
                        style={{
                          border: `2px dashed ${isDragOver ? "var(--cds-interactive)" : "var(--cds-border-strong)"}`,
                          textAlign: "center" as const,
                          padding: "1.5rem",
                          cursor: "pointer",
                          background: isDragOver ? "var(--cds-layer-selected)" : "var(--cds-layer-01)",
                          transition: "border-color 0.2s, background 0.2s",
                        }}
                        onDragOver={(e: React.DragEvent) => { e.preventDefault(); setIsDragOver(true); }}
                        onDragLeave={() => setIsDragOver(false)}
                        onDrop={handleDrop}
                        onClick={() => fileInputRef.current?.click()}
                      >
                        <Stack gap={3} style={{ alignItems: "center" }}>
                          <Upload size={24} />
                          <p style={{ margin: 0, fontWeight: 500, color: "var(--cds-text-primary)" }}>
                            {isDragOver ? "Drop files here" : "Drop files here or click to upload"}
                          </p>
                          <p style={{ margin: 0, fontSize: "0.75rem", color: "var(--cds-text-secondary)" }}>
                            PDF, DOCX, TXT, MD, HTML, CSV, JSON
                          </p>
                        </Stack>
                        <input
                          ref={fileInputRef}
                          type="file"
                          multiple
                          style={{ display: "none" }}
                          accept=".pdf,.docx,.txt,.md,.html,.csv,.json,.xml"
                          onChange={(e) => {
                            if (e.target.files) handleUpload(e.target.files);
                            e.target.value = "";
                          }}
                        />
                      </Tile>

                      {/* Document list */}
                      <Stack gap={3}>
                        <Stack orientation="horizontal" style={{ justifyContent: "space-between", alignItems: "center" }}>
                          <h4 style={{ fontSize: "0.875rem", fontWeight: 600, margin: 0 }}>
                            Indexed Documents ({documents.length})
                          </h4>
                          <Button
                            type="button"
                            kind="ghost"
                            size="sm"
                            hasIconOnly
                            renderIcon={Renew}
                            iconDescription="Refresh"
                            onClick={loadDocuments}
                          />
                        </Stack>

                        {/* Upload progress — the row itself fills left-to-right
                            as ingest advances. Single horizontal bar driven by
                            backend ``weighted_pct``; stages are deliberately
                            opaque to the user. */}
                        {uploadingFiles.length > 0 && (
                          <Stack gap={1}>
                            {uploadingFiles.map((f, idx) => {
                              const isUploading = f.status === "uploading";
                              const isSuccess = f.status === "success";
                              const isError = f.status === "error";
                              const isCancelled = f.status === "cancelled";
                              const isQueued = isUploading && f.queued === true;
                              const isInFlight = isUploading && !isQueued;
                              const isDismissable = isError || isCancelled;
                              // Border-left accent is the at-a-glance scan
                              // signal — color encodes state, not progress.
                              // No bar, no %, no false-precision: an
                              // always-moving spinner is honest because the
                              // parse stage genuinely emits no granular
                              // signal until it completes.
                              const accent = isError
                                ? "#da1e28"
                                : isSuccess
                                  ? "#24a148"
                                  : isQueued || isCancelled
                                    ? "#8d8d8d"
                                    : "#4589ff";
                              // Carbon InlineLoading covers the three cases
                              // it was designed for (active/finished/error).
                              // Queued is rendered separately — it's not
                              // "loading", it's "waiting in line".
                              const loadingStatus: "active" | "finished" | "error" | undefined =
                                isInFlight ? "active"
                                : isSuccess ? "finished"
                                : isError ? "error"
                                : undefined;
                              const stateLabel = isSuccess
                                ? "Indexed"
                                : isError
                                  ? "Failed"
                                  : isCancelled
                                    ? "Cancelled"
                                    : isQueued
                                      ? "Queued"
                                      : "Processing";
                              // Visual fill driven by backend weighted_pct.
                              // Clamped 0..1, monotonic forward via the poll
                              // loop. No numeric label — the strip's WIDTH
                              // gives the user a sense of progress without
                              // the precision anxiety of "3%".
                              const stripPct = isSuccess
                                ? 1
                                : isError || isCancelled || isQueued
                                  ? 0
                                  : Math.max(0, Math.min(1, f.progress ?? 0));
                              return (
                                <Tile
                                  key={f.taskId || f.name}
                                  className="cuga-upload-row"
                                  style={{
                                    position: "relative",
                                    // NOTE: no overflow:hidden on the Tile.
                                    // Carbon's hasIconOnly Button renders
                                    // its tooltip as an absolute descendant,
                                    // which gets clipped by ancestor
                                    // overflow:hidden. The progress strip
                                    // below is already bounded by its own
                                    // left:0/right:0 positioning, so no
                                    // clipping is needed here.
                                    borderLeft: `3px solid ${accent}`,
                                    transition: "border-color 200ms ease",
                                    // Stagger entrance for multi-file batches.
                                    // Bounded at 8 rows × 30ms = 240ms so a
                                    // 20-file drop still feels snappy.
                                    animationDelay: `${Math.min(idx, 8) * 30}ms`,
                                  }}
                                >
                                  <Stack
                                    orientation="horizontal"
                                    gap={4}
                                    style={{ alignItems: "center" }}
                                  >
                                    {/* Status indicator replaces the file
                                        icon during processing — single
                                        visual focus, no competing icons.
                                        Wrapped in a transition container so
                                        spinner → checkmark scales in rather
                                        than pops. */}
                                    <span
                                      className={
                                        isSuccess ? "cuga-status-icon cuga-status-icon--success"
                                        : "cuga-status-icon"
                                      }
                                      style={{
                                        display: "inline-flex",
                                        alignItems: "center",
                                        justifyContent: "center",
                                        width: "1rem",
                                        height: "1rem",
                                      }}
                                    >
                                      {loadingStatus ? (
                                        <InlineLoading
                                          status={loadingStatus}
                                          description=""
                                          iconDescription={stateLabel}
                                          style={{ width: "1rem", height: "1rem", minHeight: 0 }}
                                        />
                                      ) : isQueued ? (
                                        <span
                                          className="cuga-queued-dot"
                                          aria-label="Queued"
                                        />
                                      ) : (
                                        <Document size={16} />
                                      )}
                                    </span>
                                    <span style={{ flex: 1, fontSize: "0.875rem" }}>{f.name}</span>
                                    <span
                                      role="status"
                                      aria-live="polite"
                                      style={{
                                        fontSize: "0.75rem",
                                        color: "var(--cds-text-secondary)",
                                        minWidth: "4rem",
                                        textAlign: "right",
                                      }}
                                    >
                                      {stateLabel}
                                    </span>
                                    {isUploading && f.taskId && (
                                      <span className="cuga-row-action">
                                        <Button
                                          type="button"
                                          kind="ghost"
                                          size="sm"
                                          hasIconOnly
                                          renderIcon={Close}
                                          iconDescription={isQueued ? "Remove from queue" : "Cancel upload"}
                                          onClick={() => handleCancelUpload(f.taskId)}
                                        />
                                      </span>
                                    )}
                                    {isDismissable && (
                                      <Button
                                        type="button"
                                        kind="ghost"
                                        size="sm"
                                        hasIconOnly
                                        renderIcon={TrashCan}
                                        iconDescription="Dismiss"
                                        onClick={() =>
                                          setUploadingFiles((prev) =>
                                            prev.filter((x) => x.taskId !== f.taskId),
                                          )
                                        }
                                      />
                                    )}
                                  </Stack>
                                  {f.error && (
                                    <p
                                      style={{
                                        fontSize: "0.75rem",
                                        color: "#da1e28",
                                        margin: "0.25rem 0 0 1.5rem",
                                      }}
                                    >
                                      {f.error}
                                    </p>
                                  )}
                                  {/* Thin progress strip — auxiliary signal
                                      anchored to the bottom of the row.
                                      Width-driven by weighted_pct; smooth
                                      ease handles the parse→embed jump.
                                      Hidden when no work is in flight. */}
                                  {(isInFlight || isSuccess) && (
                                    <div
                                      aria-hidden="true"
                                      style={{
                                        position: "absolute",
                                        bottom: 0,
                                        left: 0,
                                        right: 0,
                                        height: "3px",
                                        background: "rgba(141, 141, 141, 0.18)",
                                        pointerEvents: "none",
                                      }}
                                    >
                                      <div
                                        style={{
                                          height: "100%",
                                          width: "100%",
                                          background: accent,
                                          transformOrigin: "left center",
                                          transform: `scaleX(${stripPct})`,
                                          transition: "transform 350ms cubic-bezier(0.22, 1, 0.36, 1), background-color 200ms ease",
                                        }}
                                      />
                                    </div>
                                  )}
                                </Tile>
                              );
                            })}
                            <style>{`
                              /* Row enter animation — slide-up + fade so the
                                 row feels like it arrived deliberately rather
                                 than popped in. */
                              .cuga-upload-row {
                                animation: cuga-row-enter 220ms cubic-bezier(0.22, 1, 0.36, 1);
                              }
                              @keyframes cuga-row-enter {
                                from { opacity: 0; transform: translateY(4px); }
                                to   { opacity: 1; transform: translateY(0); }
                              }
                              /* Queued state — slow opacity pulse signals
                                 "standing by". 1.5s feels patient, not anxious. */
                              .cuga-queued-dot {
                                width: 0.5rem;
                                height: 0.5rem;
                                border-radius: 50%;
                                background: #8d8d8d;
                                display: inline-block;
                                animation: cuga-queued-pulse 1.5s ease-in-out infinite;
                              }
                              @keyframes cuga-queued-pulse {
                                0%, 100% { opacity: 0.35; }
                                50%      { opacity: 1; }
                              }
                              /* Success → checkmark grows with a small
                                 overshoot, making completion feel earned
                                 rather than abrupt. */
                              .cuga-status-icon--success {
                                animation: cuga-success-pop 280ms cubic-bezier(0.34, 1.56, 0.64, 1);
                              }
                              @keyframes cuga-success-pop {
                                0%   { transform: scale(0.6); opacity: 0; }
                                60%  { transform: scale(1.12); opacity: 1; }
                                100% { transform: scale(1); opacity: 1; }
                              }
                              /* Cancel button hover-reveal: present but
                                 receded by default so it doesn't compete
                                 with the spinner; surfaces on row hover
                                 or keyboard focus-within (for a11y). */
                              .cuga-row-action {
                                opacity: 0.35;
                                transition: opacity 150ms ease;
                              }
                              .cuga-upload-row:hover .cuga-row-action,
                              .cuga-upload-row:focus-within .cuga-row-action {
                                opacity: 1;
                              }
                              /* Honor users who've opted out of motion. */
                              @media (prefers-reduced-motion: reduce) {
                                .cuga-upload-row,
                                .cuga-queued-dot,
                                .cuga-status-icon--success {
                                  animation: none !important;
                                }
                                .cuga-row-action {
                                  opacity: 1;
                                }
                              }
                            `}</style>
                          </Stack>
                        )}


                        {/* Render the user's documents straight through a profile-
                            switch upgrade. ``documents`` from the server is briefly
                            empty during the migration (new-hash collection has no
                            rows yet); ``documentsBeforeReindex`` snapshots the
                            previous list at reindex-start so the UI keeps showing
                            the same Tile rows, ingest dates and all, with an
                            ``Upgrading…`` pill overlaid per row as it runs. */}
                        {(() => {
                          const reindexActive = !!(reindexProgress && !reindexProgress.done);
                          const effectiveDocs = reindexActive && documentsBeforeReindex
                            ? documentsBeforeReindex
                            : documents;
                          const TAG_BY_STATUS = {
                            completed: ["green", "Ready"],
                            failed: ["red", "Failed"],
                            running: ["blue", "Upgrading…"],
                            pending: ["gray", "Queued"],
                          } as const;
                          const taskByName = new Map<string, ReindexTask>();
                          if (reindexActive && reindexProgress) {
                            for (const t of reindexProgress.tasks) {
                              const n = t.filename || Object.values(t.file_tasks ?? {})[0]?.filename;
                              if (n) taskByName.set(n, t);
                            }
                          }
                          if (effectiveDocs.length === 0 && uploadingFiles.length === 0) {
                            return (
                              <Tile>
                                <p style={{ color: "var(--cds-text-secondary)", margin: 0 }}>
                                  No documents indexed yet. Upload files to get started.
                                </p>
                              </Tile>
                            );
                          }
                          return (
                            <Stack gap={2}>
                              {effectiveDocs
                                .filter((doc) => !uploadingFiles.some((f) => (f.backendName || f.name) === doc.filename && f.status !== "error"))
                                .map((doc) => {
                                  const task = taskByName.get(doc.filename);
                                  // ``as const`` in the else branch keeps the literal
                                  // type "gray" instead of widening to ``string``, so Tag's
                                  // strict ``type`` prop accepts it (Sami C3 review).
                                  const [tagType, tagLabel] = task
                                    ? TAG_BY_STATUS[task.status] ?? TAG_BY_STATUS.pending
                                    : (["gray", ""] as const);
                                  return (
                                    <Tile key={doc.filename} style={{ borderLeft: `3px solid ${task ? "#0f62fe" : "#24a148"}` }}>
                                      <Stack orientation="horizontal" gap={4} style={{ alignItems: "center" }}>
                                        <Document size={16} />
                                        <span style={{ flex: 1, color: "var(--cds-text-primary)", fontSize: "0.875rem" }}>
                                          {doc.filename}
                                        </span>
                                        {task ? (
                                          <Tag size="sm" type={tagType}>{tagLabel}</Tag>
                                        ) : (
                                          <>
                                            {doc.ingested_at && (
                                              <span style={{ fontSize: "0.6875rem", color: "var(--cds-text-secondary)" }}>
                                                {new Date(doc.ingested_at).toLocaleDateString()}
                                              </span>
                                            )}
                                            <Button
                                              type="button"
                                              kind="danger--ghost"
                                              size="sm"
                                              hasIconOnly
                                              renderIcon={TrashCan}
                                              iconDescription="Delete document"
                                              onClick={() => setDeleteConfirm(doc.filename)}
                                            />
                                          </>
                                        )}
                                      </Stack>
                                    </Tile>
                                  );
                                })}
                            </Stack>
                          );
                        })()}
                      </Stack>
                        </>
                      )}
                    </Stack>
                  </TabPanel>

                  {/* ======================================================= */}
                  {/* SEARCH TEST TAB */}
                  {/* ======================================================= */}
                  <TabPanel>
                    <Stack gap={5} style={{ paddingTop: "1rem" }}>
                      {!agentLevelEnabled && (
                        <Tile>
                          <Stack gap={2}>
                            <h4 style={{ fontSize: "0.875rem", fontWeight: 600, margin: 0 }}>
                              Agent-level knowledge search is disabled
                            </h4>
                            <p style={{ color: "var(--cds-text-secondary)", margin: 0, fontSize: "0.8125rem", lineHeight: 1.5 }}>
                              Search testing in Manage only applies to permanent agent documents. Re-enable agent-level knowledge in Settings to test retrieval here.
                            </p>
                          </Stack>
                        </Tile>
                      )}

                      {agentLevelEnabled && (
                        <>
                      <Stack orientation="horizontal" gap={4} style={{ alignItems: "flex-end" }}>
                        <div style={{ flex: 1 }}>
                          <TextInput
                            id="knowledge-search-query"
                            labelText="Search query"
                            hideLabel
                            placeholder="Search your knowledge base..."
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleSearch(); } }}
                          />
                        </div>
                        <Button
                          type="button"
                          kind="primary"
                          size="md"
                          renderIcon={Search}
                          onClick={handleSearch}
                          disabled={searching || !searchQuery.trim()}
                        >
                          {searching ? "Searching..." : "Search"}
                        </Button>
                      </Stack>

                      <Stack orientation="horizontal" gap={4}>
                        <NumberInput
                          id="knowledge-search-limit"
                          label="Limit"
                          value={searchLimit}
                          min={1}
                          max={100}
                          onChange={(_e: any, { value }: { value: number }) => setSearchLimit(value)}
                          size="md"
                        />
                        <NumberInput
                          id="knowledge-search-threshold"
                          label="Score threshold"
                          value={searchThreshold}
                          min={0}
                          max={1}
                          step={0.1}
                          onChange={(_e: any, { value }: { value: number }) => setSearchThreshold(value)}
                          size="md"
                        />
                      </Stack>

                      {searchResults.length > 0 && (
                        <Stack gap={3}>
                          <Stack orientation="horizontal" style={{ justifyContent: "space-between", alignItems: "center" }}>
                            <h4 style={{ fontSize: "0.875rem", fontWeight: 600, margin: 0 }}>
                              Results ({searchResults.length})
                            </h4>
                            {searchTime !== null && (
                              <span style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)" }}>{searchTime}ms</span>
                            )}
                          </Stack>
                          {searchResults.map((r, i) => {
                            const passage = r.text || r.content || "";
                            const isExpanded = expandedResult === i;
                            // Limit preview to 3 lines max (handles badly parsed text with many short lines)
                            const lines = passage.split("\n");
                            const previewLines = lines.slice(0, 3).join("\n");
                            const preview = previewLines.length > 150 ? previewLines.slice(0, 150) + "..." : (lines.length > 3 ? previewLines + "..." : previewLines);
                            const displayText = isExpanded ? passage : preview;

                            // Client-side highlight: wrap query terms in <mark>
                            const highlightText = (text: string, query: string) => {
                              if (!query.trim()) return text;
                              const words = query.trim().split(/\s+/).filter((w) => w.length > 2);
                              if (words.length === 0) return text;
                              const regex = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
                              const parts = text.split(regex);
                              return parts;
                            };
                            const highlighted = highlightText(displayText, searchQuery);

                            return (
                              <Tile
                                key={i}
                                style={{ cursor: "pointer", transition: "box-shadow 0.15s", borderLeft: `3px solid ${scoreColor(r.score)}` }}
                                onClick={() => setExpandedResult(isExpanded ? null : i)}
                              >
                                <Stack gap={2}>
                                  <Stack orientation="horizontal" style={{ justifyContent: "space-between", alignItems: "center" }}>
                                    <span style={{ fontWeight: 500, color: "var(--cds-text-primary)", fontSize: "0.875rem" }}>
                                      <Document size={14} style={{ marginRight: 4, verticalAlign: "middle" }} />
                                      {r.filename}
                                      {r.page != null && (
                                        <Tag size="sm" type="gray" style={{ marginLeft: "0.5rem" }}>p.{r.page}</Tag>
                                      )}
                                    </span>
                                    <Tag type={scoreTagType(r.score)} size="sm">
                                      {r.score.toFixed(2)}
                                    </Tag>
                                  </Stack>
                                  <p style={{ margin: 0, fontSize: "0.8125rem", color: "var(--cds-text-secondary)", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
                                    {Array.isArray(highlighted)
                                      ? highlighted.map((part, j) => {
                                          const isMatch = searchQuery.trim().split(/\s+/).some(
                                            (w) => w.length > 2 && part.toLowerCase() === w.toLowerCase()
                                          );
                                          return isMatch
                                            ? <mark key={j} style={{ background: "#ffd54f", padding: "0 2px", borderRadius: 2 }}>{part}</mark>
                                            : <span key={j}>{part}</span>;
                                        })
                                      : highlighted
                                    }
                                  </p>
                                  {(lines.length > 3 || passage.length > 150) && (
                                    <span style={{ fontSize: "0.75rem", color: "var(--cds-link-primary)", cursor: "pointer" }}>
                                      {isExpanded ? "Show less" : "Show full passage"}
                                    </span>
                                  )}
                                </Stack>
                              </Tile>
                            );
                          })}
                        </Stack>
                      )}

                      {searchResults.length === 0 && !searching && searchQuery && (
                        <Tile>
                          <p style={{ color: "var(--cds-text-secondary)", margin: 0 }}>No results found. Try a different query.</p>
                        </Tile>
                      )}
                        </>
                      )}
                    </Stack>
                  </TabPanel>

                  {/* ======================================================= */}
                  {/* BEHAVIOR TAB — client adaptation                         */}
                  {/* ======================================================= */}
                  <TabPanel>
                    {knowledgeConfig && onKnowledgeConfigChange ? (
                      <ClientAdaptationPanel
                        value={knowledgeConfig.client_adaptation_text ?? ""}
                        onChange={(next: string) =>
                          onKnowledgeConfigChange({ ...knowledgeConfig, client_adaptation_text: next })
                        }
                        glossary={knowledgeConfig.client_adaptation_glossary ?? []}
                        onGlossaryChange={(next: GlossaryEntry[]) =>
                          onKnowledgeConfigChange({ ...knowledgeConfig, client_adaptation_glossary: next })
                        }
                        // Atomic reset: clear text AND glossary in one
                        // ``onKnowledgeConfigChange`` call. Sequencing
                        // two separate callbacks (onChange + onGlossaryChange)
                        // hits the stale-closure problem — each closes
                        // over the same ``knowledgeConfig`` snapshot, so
                        // the second clobbers the first. One spread =>
                        // both deltas land.
                        onReset={() =>
                          onKnowledgeConfigChange({
                            ...knowledgeConfig,
                            client_adaptation_text: "",
                            client_adaptation_glossary: [],
                          })
                        }
                        // Save state + drift + serverError are wired by the
                        // parent (ManagePage) when those signals exist; in the
                        // standalone modal context this is intentionally bare.
                        serverError={adaptationServerError ?? null}
                      />
                    ) : (
                      <Tile>
                        <p style={{ color: "var(--cds-text-secondary)", margin: 0 }}>
                          Knowledge settings are managed from the Manage page.
                        </p>
                      </Tile>
                    )}
                  </TabPanel>

                  {/* ======================================================= */}
                  {/* SETTINGS TAB */}
                  {/* ======================================================= */}
                  <TabPanel>
                    {knowledgeConfig && onKnowledgeConfigChange ? (
                    <Stack gap={5} style={{ paddingTop: "1rem" }}>

                      {/* ── 1. Health status ── */}
                      <Tile style={{ padding: "0.625rem 0.75rem" }}>
                        <Stack orientation="horizontal" gap={3} style={{ alignItems: "center", justifyContent: "space-between" }}>
                          <Stack orientation="horizontal" gap={2} style={{ alignItems: "center", minWidth: 0 }}>
                            <span
                              style={{
                                width: 8,
                                height: 8,
                                borderRadius: "50%",
                                display: "inline-block",
                                flexShrink: 0,
                                background:
                                  healthy === null
                                    ? "var(--cds-text-disabled)"
                                    : healthy
                                      ? "var(--cds-support-success)"
                                      : "var(--cds-support-error)",
                              }}
                            />
                            <span
                              style={{
                                fontSize: "0.75rem",
                                fontWeight: 500,
                                color: "var(--cds-text-primary)",
                                whiteSpace: "nowrap",
                              }}
                            >
                              Service
                            </span>
                            <Tag
                              size="sm"
                              type={healthy === null ? "gray" : healthy ? "green" : "red"}
                            >
                              {healthy === null ? "Checking" : healthy ? "Connected" : "Disconnected"}
                            </Tag>
                          </Stack>
                          <Button
                            type="button"
                            kind="ghost"
                            size="sm"
                            hasIconOnly
                            renderIcon={Renew}
                            iconDescription="Refresh status"
                            onClick={checkHealth}
                          />
                        </Stack>
                      </Tile>

                      {/* ── 2. Enable / Disable toggle ── */}
                      <Tile>
                        <Toggle
                          id="knowledge-enabled"
                          labelText="Knowledge Base"
                          labelA="Off"
                          labelB="On"
                          toggled={knowledgeConfig.enabled ?? true}
                          onToggle={(checked: boolean) => {
                            onKnowledgeConfigChange({ ...knowledgeConfig, enabled: checked });
                            if (checked && !healthy) {
                              ensureEngineStarted();
                            }
                          }}
                          size="sm"
                        />
                        {!knowledgeEnabled && (
                          <p style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)", margin: "0.5rem 0 0 0" }}>
                            Knowledge base is disabled. Enable it to configure retrieval settings.
                          </p>
                        )}
                      </Tile>

                      {knowledgeEnabled && (
                        <Stack gap={4}>
                          {/* Agent-level knowledge card */}
                          <Tile
                            style={{
                              borderLeft: agentLevelEnabled
                                ? "3px solid var(--cds-support-success)"
                                : "3px solid var(--cds-border-subtle)",
                              transition: "border-color 0.15s ease",
                            }}
                          >
                            <Stack gap={3}>
                              <Stack orientation="horizontal" gap={4} style={{ alignItems: "center", justifyContent: "space-between" }}>
                                <Stack orientation="horizontal" gap={3} style={{ alignItems: "center" }}>
                                  <Document size={20} style={{ color: agentLevelEnabled ? "var(--cds-support-success)" : "var(--cds-text-disabled)", flexShrink: 0 }} />
                                  <div>
                                    <p style={{ fontSize: "0.875rem", fontWeight: 600, color: "var(--cds-text-primary)", margin: 0 }}>
                                      Agent-level knowledge
                                    </p>
                                    <p style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)", margin: "0.125rem 0 0 0" }}>
                                      Permanent documents shared across all conversations
                                    </p>
                                  </div>
                                </Stack>
                                <Toggle
                                  id="knowledge-agent-level-enabled"
                                  labelText=""
                                  hideLabel
                                  labelA="Off"
                                  labelB="On"
                                  toggled={knowledgeConfig.agent_level_enabled ?? true}
                                  onToggle={(checked: boolean) => onKnowledgeConfigChange({ ...knowledgeConfig, agent_level_enabled: checked })}
                                  size="sm"
                                />
                              </Stack>
                            </Stack>
                          </Tile>

                          {/* Session-level knowledge card */}
                          <Tile
                            style={{
                              borderLeft: sessionLevelEnabled
                                ? "3px solid var(--cds-support-success)"
                                : "3px solid var(--cds-border-subtle)",
                              transition: "border-color 0.15s ease",
                            }}
                          >
                            <Stack gap={3}>
                              <Stack orientation="horizontal" gap={4} style={{ alignItems: "center", justifyContent: "space-between" }}>
                                <Stack orientation="horizontal" gap={3} style={{ alignItems: "center" }}>
                                  <Search size={20} style={{ color: sessionLevelEnabled ? "var(--cds-support-success)" : "var(--cds-text-disabled)", flexShrink: 0 }} />
                                  <div>
                                    <p style={{ fontSize: "0.875rem", fontWeight: 600, color: "var(--cds-text-primary)", margin: 0 }}>
                                      Session-level knowledge
                                    </p>
                                    <p style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)", margin: "0.125rem 0 0 0" }}>
                                      Per-conversation uploads and scoped search
                                    </p>
                                  </div>
                                </Stack>
                                <Toggle
                                  id="knowledge-session-level-enabled"
                                  labelText=""
                                  hideLabel
                                  labelA="Off"
                                  labelB="On"
                                  toggled={knowledgeConfig.session_level_enabled ?? true}
                                  onToggle={(checked: boolean) => onKnowledgeConfigChange({ ...knowledgeConfig, session_level_enabled: checked })}
                                  size="sm"
                                />
                              </Stack>
                            </Stack>
                          </Tile>
                        </Stack>
                      )}

                      {/* ── Everything below is gated on enabled ── */}
                      {knowledgeEnabled && (
                        <>
                          {!agentLevelEnabled && (
                            <InlineNotification
                              kind="info"
                              title="Agent-level knowledge is off"
                              subtitle="Permanent documents, indexing, and Manage search are unavailable until you turn it back on."
                              lowContrast
                              hideCloseButton
                            />
                          )}

                          {!sessionLevelEnabled && (
                            <InlineNotification
                              kind="info"
                              title="Session-level knowledge is off"
                              subtitle="Conversation uploads and session-scoped knowledge search are unavailable in chat."
                              lowContrast
                              hideCloseButton
                            />
                          )}

                          {/* ── 3. Retrieval Profile selector ── */}
                          {ragProfiles && Object.keys(ragProfiles).length > 0 && (
                            <Stack gap={3}>
                              <Stack gap={1}>
                                <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                                  <h4 style={{ margin: 0, fontSize: "0.875rem", fontWeight: 600 }}>Retrieval Profile</h4>
                                  {/* Save-status pill at the surface the user is
                                      actually editing. The previous location
                                      (inside the Advanced accordion, next to
                                      Test connection) was invisible during the
                                      most common edit path (clicking profile
                                      tiles). Reviewer's "second pill" call.
                                      ``recentlySaved`` keeps "Saved" up only
                                      for 3s so it doesn't become a permanent
                                      sticker. */}
                                  {saveState === "saving" && (
                                    <Tag type="gray" size="sm" renderIcon={Renew}>
                                      Saving…
                                    </Tag>
                                  )}
                                  {saveState === "saving-slow" && (
                                    <Tag type="gray" size="sm" renderIcon={Renew}>
                                      Still saving
                                    </Tag>
                                  )}
                                  {saveState === "saved" && recentlySaved && (
                                    <Tag type="green" size="sm" renderIcon={Checkmark}>
                                      Saved
                                    </Tag>
                                  )}
                                  {saveState === "failed" && (
                                    <Tag type="red" size="sm" renderIcon={ErrorFilled}>
                                      Couldn&apos;t save
                                    </Tag>
                                  )}
                                </span>
                                <p style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)", margin: 0 }}>
                                  Balance retrieval accuracy against response speed and cost.
                                </p>
                              </Stack>
                              <Stack gap={2}>
                                {Object.entries(ragProfiles).map(([key, profile]) => {
                                  const isNamedProfile = (knowledgeConfig.rag_profile ?? "standard") === key;
                                  // Re-index is required when ANY field in the backend's
                                  // vector_config_hash would change — the embedding model AND
                                  // chunk size/overlap, not chunking alone. Keying only on
                                  // chunking hid a silent index-stale when a profile swaps the
                                  // embedder (e.g. standard→balanced: bge-small→bge-base) and
                                  // mislabelled speed↔standard as free even though their
                                  // chunk_size differs (1000 vs 800). metric_type is global
                                  // (COSINE-only, not profile-owned) so it never changes here.
                                  const vectorConfigMatches =
                                    (profile.embeddings?.model ?? knowledgeConfig.embedding_model) ===
                                      knowledgeConfig.embedding_model &&
                                    profile.chunking.chunk_size === knowledgeConfig.chunk_size &&
                                    profile.chunking.chunk_overlap === knowledgeConfig.chunk_overlap;
                                  // Selected by USER INTENT (the profile name in config), not by
                                  // exact field-for-field equality. The named profile is the baseline;
                                  // edits to advanced settings are overrides on top of it, not a
                                  // reason to drop the profile from the selected state. ``Modified``
                                  // tag inside the selected tile signals the drift (see below).
                                  const isSelected = isNamedProfile;
                                  // True when this is the selected profile AND the user has edited
                                  // a vector-config field — drives the "Modified" Tag + the in-tile
                                  // reindex hint. The non-selected reindex hint (offered to OTHER
                                  // profiles to inform their decision) still uses ``willReindex`` below.
                                  const isModified = isNamedProfile && !vectorConfigMatches;
                                  const willReindex = !vectorConfigMatches;
                                  return (
                                    <Tile
                                      key={key}
                                      style={{
                                        cursor: "pointer",
                                        borderLeft: `3px solid ${isSelected ? "var(--cds-interactive)" : "transparent"}`,
                                        background: isSelected ? "var(--cds-layer-selected)" : "var(--cds-layer-01)",
                                        transition: "background 0.15s, border-color 0.15s",
                                        padding: "0.75rem 1rem",
                                      }}
                                      onClick={() => {
                                        // Populate EVERY field the profile owns from the
                                        // profile metadata (not just chunking). Without this,
                                        // the autosave POST re-sent the prior values for
                                        // embedding_model / docling_* / rerank_* and the
                                        // backend's "incoming wins" merge clobbered the
                                        // profile loader — so picking max_quality stayed on
                                        // bge-small at ingest time. Fall back to the previous
                                        // config value when a profile section omits a key so
                                        // edits to fields the profile doesn't own survive.
                                        onKnowledgeConfigChange({
                                          ...knowledgeConfig,
                                          rag_profile: key,
                                          chunk_size: profile.chunking?.chunk_size ?? knowledgeConfig.chunk_size,
                                          chunk_overlap: profile.chunking?.chunk_overlap ?? knowledgeConfig.chunk_overlap,
                                          embedding_model: profile.embeddings?.model ?? knowledgeConfig.embedding_model,
                                          embedding_batch_size: profile.embeddings?.batch_size ?? knowledgeConfig.embedding_batch_size,
                                          embedding_concurrency: profile.embeddings?.concurrency ?? knowledgeConfig.embedding_concurrency,
                                          docling_pdf_mode: profile.docling?.pdf_mode ?? knowledgeConfig.docling_pdf_mode,
                                          docling_layout_engine: profile.docling?.layout_engine ?? knowledgeConfig.docling_layout_engine,
                                          docling_drop_page_chrome: profile.docling?.drop_page_chrome ?? knowledgeConfig.docling_drop_page_chrome,
                                          rerank_enabled: profile.rerank?.enabled ?? knowledgeConfig.rerank_enabled,
                                          rerank_top_k_in: profile.rerank?.top_k_in ?? knowledgeConfig.rerank_top_k_in,
                                          rerank_model: profile.rerank?.model ?? knowledgeConfig.rerank_model,
                                          search_hybrid_mode: profile.search?.hybrid_mode ?? knowledgeConfig.search_hybrid_mode,
                                          search_query_transform: profile.search?.query_transform ?? knowledgeConfig.search_query_transform,
                                          search_junk_filter: profile.search?.junk_filter ?? knowledgeConfig.search_junk_filter,
                                          max_search_attempts: profile.search?.max_search_attempts ?? knowledgeConfig.max_search_attempts,
                                          default_limit: profile.search?.default_limit ?? knowledgeConfig.default_limit,
                                          default_score_threshold: profile.search?.default_score_threshold ?? knowledgeConfig.default_score_threshold,
                                          max_ingest_workers: profile.engine?.max_ingest_workers ?? knowledgeConfig.max_ingest_workers,
                                          vector_insert_batch_size: profile.engine?.vector_insert_batch_size ?? knowledgeConfig.vector_insert_batch_size,
                                        });
                                      }}
                                    >
                                      <Stack orientation="horizontal" gap={3} style={{ alignItems: "flex-start" }}>
                                        <span
                                          style={{
                                            width: 16, height: 16, borderRadius: "50%", flexShrink: 0, marginTop: 1,
                                            border: isSelected ? "5px solid var(--cds-interactive)" : "2px solid var(--cds-icon-secondary)",
                                            background: isSelected ? "var(--cds-layer-01)" : "transparent",
                                            transition: "all 0.15s",
                                          }}
                                        />
                                        <Stack gap={1} style={{ flex: 1 }}>
                                          <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                                            <span style={{ fontWeight: 600, fontSize: "0.875rem", color: "var(--cds-text-primary)" }}>
                                              {profile.name}
                                            </span>
                                            {isModified && (
                                              <Tag type="cool-gray" size="sm">Modified</Tag>
                                            )}
                                          </span>
                                          <p style={{ margin: 0, fontSize: "0.75rem", color: "var(--cds-text-secondary)", lineHeight: 1.5 }}>
                                            {profile.description}
                                          </p>
                                          {!isSelected && willReindex && (
                                            <p style={{ margin: "0.25rem 0 0 0", fontSize: "0.6875rem", color: "var(--cds-support-warning)" }}>
                                              Requires re-indexing existing documents.
                                            </p>
                                          )}
                                          {isModified && (
                                            <p style={{ margin: "0.25rem 0 0 0", fontSize: "0.6875rem", color: "var(--cds-support-warning)" }}>
                                              Your edits override profile defaults — re-indexing will run on Publish.
                                            </p>
                                          )}
                                        </Stack>
                                      </Stack>
                                    </Tile>
                                  );
                                })}
                              </Stack>
                            </Stack>
                          )}

                          {/* ── 4. Re-index: warning, progress, or completion ── */}
                          {agentLevelEnabled && reindexProgress && !reindexProgress.done && (
                            <Tile>
                              <Stack gap={4}>
                                <Stack gap={1}>
                                  {/* Phase-narrative headline replaces the bare "Re-indexing
                                      documents..." string. During the model-download phase the
                                      backend hasn't started per-file work yet, so a "0 of N
                                      processed" subline reads as broken; we lead with what's
                                      actually happening ("Preparing your new reading model") and
                                      surface the file-counter only once embedding starts. */}
                                  <h4 style={{ margin: 0, fontSize: "0.875rem", fontWeight: 600 }}>
                                    {getReindexPhaseHeadline(reindexProgress.tasks)}
                                  </h4>
                                  <p style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)", margin: 0 }}>
                                    {reindexProgress.completed + reindexProgress.failed} of {reindexProgress.total} document
                                    {reindexProgress.total === 1 ? "" : "s"} ready
                                    {" · "}
                                    Running for {formatElapsedSeconds(Math.floor((Date.now() - reindexProgress.startedAt) / 1000))}
                                  </p>
                                </Stack>
                                {/* Progress bar */}
                                <div style={{
                                  width: "100%", height: 8, borderRadius: 4,
                                  background: "var(--cds-layer-accent-01, #e0e0e0)",
                                  overflow: "hidden",
                                }}>
                                  <div style={{
                                    height: "100%", borderRadius: 4,
                                    width: `${reindexProgress.total > 0 ? ((reindexProgress.completed + reindexProgress.failed) / reindexProgress.total) * 100 : 0}%`,
                                    background: reindexProgress.failed > 0 ? "var(--cds-support-warning)" : "var(--cds-interactive)",
                                    transition: "width 0.4s ease",
                                  }} />
                                </div>
                                {/* Per-file status list */}
                                <div className="knowledge-reindex-list">
                                  <div className="knowledge-reindex-list__header">
                                    <span>Document</span>
                                    <span>Status</span>
                                  </div>
                                  {reindexProgress.tasks.map((task) => {
                                    const taskError = getReindexTaskError(task);
                                    const progressLabel = getReindexTaskProgressLabel(task);
                                    return (
                                      <div
                                        key={task.task_id}
                                        className={`knowledge-reindex-item knowledge-reindex-item--${task.status}`}
                                      >
                                        <div className="knowledge-reindex-item__icon" aria-hidden="true">
                                          {task.status === "completed" && (
                                            <Checkmark size={14} style={{ color: "var(--cds-support-success)" }} />
                                          )}
                                          {task.status === "failed" && (
                                            <ErrorFilled size={14} style={{ color: "var(--cds-support-error)" }} />
                                          )}
                                          {(task.status === "pending" || task.status === "running") && (
                                            <span
                                              className={`knowledge-reindex-item__spinner knowledge-reindex-item__spinner--${task.status}`}
                                            />
                                          )}
                                        </div>
                                        <div className="knowledge-reindex-item__body">
                                          <span className="knowledge-reindex-item__filename">
                                            {task.filename || task.task_id}
                                          </span>
                                          {task.status === "running" && progressLabel && (
                                            <span
                                              className="knowledge-reindex-item__progress"
                                              style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)" }}
                                            >
                                              {progressLabel}
                                            </span>
                                          )}
                                          {task.status === "failed" && taskError && (
                                            <span className="knowledge-reindex-item__error">
                                              {taskError}
                                            </span>
                                          )}
                                        </div>
                                        <div className="knowledge-reindex-item__status">
                                          <Tag
                                            size="sm"
                                            type={
                                              task.status === "completed" ? "green" :
                                              task.status === "failed" ? "red" :
                                              task.status === "running" ? "blue" : "gray"
                                            }
                                          >
                                            {getReindexStatusLabel(task.status)}
                                          </Tag>
                                        </div>
                                      </div>
                                    );
                                  })}
                                </div>
                              </Stack>
                              <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
                            </Tile>
                          )}

                          {agentLevelEnabled && reindexProgress?.done && (
                            <InlineNotification
                              kind={reindexProgress.failed > 0 ? "warning" : "success"}
                              title={reindexProgress.failed > 0 ? "Re-index finished with errors" : "Re-index complete"}
                              subtitle={`${reindexProgress.completed} succeeded${reindexProgress.failed > 0 ? `, ${reindexProgress.failed} failed` : ""}.`}
                              lowContrast
                              onClose={() => setReindexProgress(null)}
                              // Hide the close button on success — the
                              // notification auto-dismisses via the effect
                              // below. On failure, keep X so the user can
                              // dismiss manually after reading the count.
                              hideCloseButton={reindexProgress.failed === 0}
                            />
                          )}

                          {/* Re-index recommended notice. Trigger restored to
                              include ``knowledgeReindexNeeded`` (snapshot-vs-
                              current diff): we no longer auto-reindex on PATCH,
                              so this banner is how the user knows their config
                              change requires re-embedding. The save-status pill
                              only confirms "draft persisted"; it does NOT mean
                              "vectors are current". Two genuinely different
                              states now. ``knowledgeStale`` /
                              ``knowledgeReindexDeferred`` keep their roles for
                              the persistent stale cases. */}
                          {agentLevelEnabled && !reindexProgress && (knowledgeReindexNeeded || knowledgeStale || knowledgeReindexDeferred) && (
                            <Stack gap={3}>
                              {/* Two passes on this notice:
                                    1. Softened from kind="warning" +
                                       "danger--tertiary" (alarming for a
                                       routine change).
                                    2. Then sharpened — "Update existing
                                       documents" was too soft, sounded
                                       optional. The action is in fact
                                       mandatory if the user wants their
                                       config change to take effect on
                                       already-indexed documents (new
                                       uploads use the new config; existing
                                       ones don't until re-indexed). Title
                                       leads with that. */}
                              <InlineNotification
                                kind="info"
                                title="Re-index to apply your changes"
                                subtitle="Existing documents still use the previous embedder. New uploads will use the new configuration, but already-indexed documents need a re-index to switch."
                                lowContrast
                                hideCloseButton
                              />
                              {onReindex && (
                                <Button
                                  type="button"
                                  kind="primary"
                                  size="sm"
                                  disabled={knowledgeReindexing}
                                  onClick={startReindexWithProgress}
                                >
                                  {knowledgeReindexing ? "Re-indexing…" : "Re-index now"}
                                </Button>
                              )}
                            </Stack>
                          )}

                          {/* ── 4. Advanced settings toggle ──
                              UX rationale: the basic view is just the
                              ON/OFF gate, agent/session scopes, and the
                              Retrieval Profile cards — that's the 90%
                              decision surface. Embeddings/chunking/parsing
                              /score/limits are operator-grade knobs and
                              shouldn't compete for attention. The toggle
                              sits right after Retrieval Profile so its
                              affordance is discoverable without scrolling
                              past the whole accordion stack. Choice
                              persists in localStorage. */}
                          <Stack
                            orientation="horizontal"
                            gap={3}
                            style={{ alignItems: "center", marginTop: "0.5rem" }}
                          >
                            <Toggle
                              id="knowledge-show-advanced"
                              labelText="Advanced settings"
                              labelA="Off"
                              labelB="On"
                              toggled={showAdvanced}
                              onToggle={(v: boolean) => persistShowAdvanced(v)}
                              size="sm"
                            />
                            <span style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)" }}>
                              Embeddings, chunking, parsing, retrieval behavior, score & metric, and limits.
                            </span>
                          </Stack>

                          {/* ── 5. Advanced configuration (only when toggle on) ── */}
                          {showAdvanced && (
                          <Accordion align="start" size="md">
                            <AccordionItem title={sectionTitle("Embeddings", embeddingsStatus)}>
                              <Stack gap={4} style={{ paddingTop: "0.5rem" }}>
                                {/* "Detected in your environment" panel lives here
                                    inside the Embeddings section — same scope as
                                    the manual Provider Select + API key + base URL
                                    fields below. Picking a preset here populates
                                    the manual fields; typing in the manual fields
                                    overrides the preset. One conceptual surface,
                                    two modes of entry. */}
                                {envPresets && envPresets.length > 0 && (
                                  <EnvPresetsPanel
                                    presets={envPresets}
                                    currentProvider={knowledgeConfig.embedding_provider ?? "auto"}
                                    currentModel={knowledgeConfig.embedding_model ?? ""}
                                    onApply={(preset) => {
                                      onPresetApplied?.();
                                      onKnowledgeConfigChange({
                                        ...knowledgeConfig,
                                        embedding_provider: preset.default_provider,
                                        embedding_model: preset.default_model,
                                        embedding_api_key: "",
                                        embedding_base_url: "",
                                        embedding_extra_params: {},
                                      });
                                      onToast?.(
                                        "success",
                                        `${preset.label} applied`,
                                        `Provider set to ${preset.default_provider}; model set to ${preset.default_model}. The engine will read credentials from the environment.`,
                                      );
                                    }}
                                    onFocusProviderSelect={() => {
                                      const el = document.getElementById("knowledge-embedding-provider");
                                      el?.focus();
                                      el?.scrollIntoView({ behavior: "smooth", block: "center" });
                                    }}
                                  />
                                )}
                                <Stack orientation="horizontal" gap={4}>
                                  <Select
                                    id="knowledge-embedding-provider"
                                    labelText="Provider"
                                    value={knowledgeConfig.embedding_provider ?? "auto"}
                                    onChange={(e: any) => {
                                      const newProvider = e.target.value;
                                      // CRITICAL: when switching providers, RESET fields that are
                                      // provider-specific. Otherwise a previously-typed base_url
                                      // (e.g. an IBM LiteLLM proxy URL) silently bleeds into the
                                      // next provider's request (e.g. OpenRouter), sending your
                                      // OpenRouter key to the wrong server. Each provider's
                                      // base_url / api_key / extra_params live in their own world.
                                      const prev = knowledgeConfig.embedding_provider;
                                      const isCredentialedProvider = (p: string | undefined) =>
                                        p === "openai" || p === "openrouter" || p === "litellm" || p === "ollama";
                                      const needsReset = isCredentialedProvider(prev) || isCredentialedProvider(newProvider);
                                      onKnowledgeConfigChange({
                                        ...knowledgeConfig,
                                        embedding_provider: newProvider,
                                        ...(needsReset
                                          ? {
                                              embedding_base_url: "",
                                              embedding_api_key: "",
                                              embedding_extra_params: {},
                                              embedding_model: "",
                                            }
                                          : {}),
                                      });
                                    }}
                                  >
                                    <SelectItem value="auto" text="Auto-detect" />
                                    <SelectItem value="fastembed" text="FastEmbed (local)" />
                                    <SelectItem value="openai" text="OpenAI / OpenAI-compatible (Together, Fireworks, custom proxy)" />
                                    <SelectItem value="openrouter" text="OpenRouter (single key for many models)" />
                                    <SelectItem value="litellm" text="LiteLLM (unified — openai/cohere/azure/bedrock/...)" />
                                    <SelectItem value="huggingface" text="HuggingFace local (PyTorch — Mac GPU / NVIDIA CUDA via GPU Acceleration toggle)" />
                                    <SelectItem value="ollama" text="Ollama" />
                                  </Select>
                                  <TextInput
                                    id="knowledge-embedding-model"
                                    labelText="Model"
                                    value={knowledgeConfig.embedding_model ?? ""}
                                    onChange={(e: any) => onKnowledgeConfigChange({ ...knowledgeConfig, embedding_model: e.target.value })}
                                    placeholder={
                                      knowledgeConfig.embedding_provider === "openrouter"
                                        ? "REQUIRED — e.g. openai/text-embedding-3-small"
                                        : knowledgeConfig.embedding_provider === "litellm"
                                          ? "REQUIRED — e.g. openai/text-embedding-3-small, cohere/embed-english-v3.0"
                                          : "Auto-detect per provider"
                                    }
                                  />
                                </Stack>
                                {knowledgeConfig.embedding_provider === "openrouter" && (
                                  <>
                                    <ActionableNotification
                                      kind="info"
                                      lowContrast
                                      hideCloseButton
                                      title="OpenRouter"
                                      subtitle="Paste any embeddings model id (e.g. openai/text-embedding-3-small). Both Model and API Key are required."
                                      actionButtonLabel="Browse models"
                                      onActionButtonClick={() =>
                                        window.open(
                                          "https://openrouter.ai/models?output_modalities=embeddings",
                                          "_blank",
                                          "noopener,noreferrer",
                                        )
                                      }
                                    />
                                    <TextInput
                                      id="knowledge-embedding-api-key-openrouter"
                                      type="password"
                                      labelText="OpenRouter API Key"
                                      required
                                      value={knowledgeConfig.embedding_api_key ?? ""}
                                      onChange={(e: any) => onKnowledgeConfigChange({ ...knowledgeConfig, embedding_api_key: e.target.value })}
                                      placeholder="Paste OPENROUTER_API_KEY"
                                    />
                                  </>
                                )}
                                {knowledgeConfig.embedding_provider === "litellm" && (
                                  <>
                                    {!((knowledgeConfig.embedding_model || "").trim()) ? (
                                      <InlineNotification
                                        kind="warning"
                                        lowContrast
                                        hideCloseButton
                                        title="Model required"
                                        subtitle={
                                          <>
                                            LiteLLM needs a model name with a provider prefix in the <strong>Model</strong> field above
                                            (e.g. <code>openai/text-embedding-3-small</code>, <code>azure/text-embedding-3-small-1</code>,
                                            <code>cohere/embed-english-v3.0</code>). Settings won't save until this is filled in.
                                          </>
                                        }
                                      />
                                    ) : (
                                      <ActionableNotification
                                        kind="success"
                                        lowContrast
                                        hideCloseButton
                                        title="LiteLLM ready"
                                        subtitle={`${knowledgeConfig.embedding_model} will be routed via LiteLLM. API key falls back to env var if empty. Base URL is for self-hosted proxies.`}
                                        actionButtonLabel="Supported models"
                                        onActionButtonClick={() =>
                                          window.open(
                                            "https://docs.litellm.ai/docs/embedding/supported_embedding",
                                            "_blank",
                                            "noopener,noreferrer",
                                          )
                                        }
                                      />
                                    )}
                                    <Stack orientation="horizontal" gap={4}>
                                      <TextInput
                                        id="knowledge-embedding-base-url-litellm"
                                        labelText="Base URL (optional, for self-hosted LiteLLM proxy)"
                                        value={knowledgeConfig.embedding_base_url ?? ""}
                                        onChange={(e: any) => onKnowledgeConfigChange({ ...knowledgeConfig, embedding_base_url: e.target.value })}
                                        placeholder="e.g. http://localhost:4000"
                                        invalid={!!baseUrlError}
                                        invalidText={baseUrlError ?? undefined}
                                      />
                                      <TextInput
                                        id="knowledge-embedding-api-key-litellm"
                                        type="password"
                                        labelText="API Key (optional — falls back to env var)"
                                        value={knowledgeConfig.embedding_api_key ?? ""}
                                        onChange={(e: any) => onKnowledgeConfigChange({ ...knowledgeConfig, embedding_api_key: e.target.value })}
                                        placeholder="Leave empty to use provider env var"
                                      />
                                    </Stack>
                                  </>
                                )}
                                {(knowledgeConfig.embedding_provider === "openai" || knowledgeConfig.embedding_provider === "ollama") && (
                                  <>
                                    <InlineNotification
                                      kind="info"
                                      lowContrast
                                      hideCloseButton
                                      title={knowledgeConfig.embedding_provider === "openai" ? "OpenAI / OpenAI-compatible" : "Ollama"}
                                      subtitle={
                                        knowledgeConfig.embedding_provider === "openai" ? (
                                          <>
                                            Works for OpenAI direct and any OpenAI-compatible endpoint (Together, Fireworks, IBM LiteLLM proxy).
                                            Remember to append <code>/v1</code> to the Base URL for most proxies. For OpenRouter use its dedicated provider.
                                          </>
                                        ) : (
                                          <>
                                            Local Ollama server. Base URL defaults to <code>http://localhost:11434</code>. Model is optional —
                                            defaults to <code>nomic-embed-text</code>.
                                          </>
                                        )
                                      }
                                    />
                                    <Stack orientation="horizontal" gap={4}>
                                      <TextInput
                                        id="knowledge-embedding-base-url"
                                        labelText="Base URL"
                                        value={knowledgeConfig.embedding_base_url ?? ""}
                                        onChange={(e: any) => onKnowledgeConfigChange({ ...knowledgeConfig, embedding_base_url: e.target.value })}
                                        placeholder={knowledgeConfig.embedding_provider === "openai" ? "e.g. https://api.together.xyz/v1" : "e.g. http://localhost:11434"}
                                        helperText={knowledgeConfig.embedding_provider === "openai" ? "Optional — leave empty for OpenAI direct." : "Optional — defaults to localhost:11434."}
                                        invalid={!!baseUrlError}
                                        invalidText={baseUrlError ?? undefined}
                                      />
                                      <TextInput
                                        id="knowledge-embedding-api-key"
                                        type="password"
                                        labelText="API Key"
                                        value={knowledgeConfig.embedding_api_key ?? ""}
                                        onChange={(e: any) => onKnowledgeConfigChange({ ...knowledgeConfig, embedding_api_key: e.target.value })}
                                        placeholder={knowledgeConfig.embedding_provider === "openai" ? "Leave empty to use OPENAI_API_KEY env" : "Usually unused for Ollama"}
                                        helperText={knowledgeConfig.embedding_provider === "openai" ? "Optional — falls back to OPENAI_API_KEY env var." : "Optional — Ollama typically doesn't require a key."}
                                      />
                                    </Stack>
                                  </>
                                )}

                                {/* === Advanced extra_params editor (for Azure api_version, Bedrock region, ...) === */}
                                {showAdvanced && (knowledgeConfig.embedding_provider === "litellm" ||
                                  knowledgeConfig.embedding_provider === "openai") && (
                                  <TextInput
                                    id="knowledge-embedding-extra-params"
                                    labelText="Advanced: Extra provider kwargs (optional, JSON dict)"
                                    value={
                                      knowledgeConfig.embedding_extra_params &&
                                      Object.keys(knowledgeConfig.embedding_extra_params).length > 0
                                        ? JSON.stringify(knowledgeConfig.embedding_extra_params)
                                        : ""
                                    }
                                    onChange={(e: any) => {
                                      const raw = e.target.value.trim();
                                      if (!raw) {
                                        onKnowledgeConfigChange({ ...knowledgeConfig, embedding_extra_params: {} });
                                        return;
                                      }
                                      try {
                                        const parsed = JSON.parse(raw);
                                        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
                                          // Reject reserved keys that have dedicated fields above —
                                          // prevents the "I put my model in the JSON" foot-gun the
                                          // user hit. We surface the rejection via setExtraParamsHint.
                                          const reserved = ["embedding_model", "model", "embedding_api_key", "api_key", "embedding_base_url", "base_url"];
                                          const violations = reserved.filter((k) => k in parsed);
                                          if (violations.length > 0) {
                                            setExtraParamsHint(
                                              `Don't put ${violations.join(", ")} here — those go in the named fields above. This box is for provider-specific extras (e.g. api_version for Azure).`,
                                            );
                                            return;
                                          }
                                          setExtraParamsHint(null);
                                          onKnowledgeConfigChange({ ...knowledgeConfig, embedding_extra_params: parsed });
                                        }
                                      } catch {
                                        // Don't propagate while user is typing invalid JSON.
                                      }
                                    }}
                                    placeholder={
                                      knowledgeConfig.embedding_model?.startsWith("azure/")
                                        ? '{"api_version":"2024-02-15","azure_deployment":"my-deployment"}'
                                        : 'leave empty for most providers'
                                    }
                                    helperText="NOT for the model name — model has its own field above. Use this only for provider-specific extras (Azure: api_version. Bedrock: aws_region_name)."
                                    invalid={!!extraParamsHint}
                                    invalidText={extraParamsHint ?? undefined}
                                  />
                                )}

                                {/* === Action row: Test connection + status Tags ===
                                    Carbon Tag is the right primitive for compact status
                                    indicators — supports color tokens, icons (renderIcon),
                                    and is screen-reader friendly. Replaces the previous
                                    span+inline-hex approach. */}
                                <Stack orientation="horizontal" gap={3} style={{ alignItems: "center", flexWrap: "wrap" }}>
                                  <Button
                                    kind="tertiary"
                                    size="sm"
                                    disabled={testing || !knowledgeConfig.embedding_provider}
                                    onClick={handleTestConnection}
                                    renderIcon={testing ? Renew : undefined}
                                    iconDescription={testing ? "Testing connection…" : undefined}
                                  >
                                    {testing ? "Testing…" : "Test connection"}
                                  </Button>

                                  {/* Save-state indicator: each variant maps to a real
                                      PATCH lifecycle event (saving/2xx/non-2xx/network).
                                      The prior 1500ms-setTimeout "Saved" sticker that
                                      lied is gone — now the user sees a "Saving…" tag
                                      while the network call is in flight, "Saved" only
                                      AFTER a 2xx response (auto-hides after 3s via
                                      ``recentlySaved``), and a red "Couldn't save"
                                      button-tag with one-click Retry on any failure. */}
                                  {saveState === "saving" && (
                                    <Tag type="gray" size="sm" renderIcon={Renew}>
                                      Saving…
                                    </Tag>
                                  )}
                                  {/* saving-slow: 25s+ into the save with no
                                      response yet. Softer copy than a fail
                                      state — corporate VPNs / first-time
                                      Watsonx endpoint resolution can take
                                      30-45s and a perfectly-healthy save
                                      shouldn't read as broken. */}
                                  {saveState === "saving-slow" && (
                                    <Tag type="gray" size="sm" renderIcon={Renew}>
                                      Still saving — network is slow
                                    </Tag>
                                  )}
                                  {saveState === "saved" && recentlySaved && (
                                    <Tag type="green" size="sm" renderIcon={Checkmark}>
                                      Saved
                                    </Tag>
                                  )}
                                  {saveState === "failed" && draftSaveStatus?.kind === "failed" && (
                                    <Button
                                      kind="ghost"
                                      size="sm"
                                      renderIcon={ErrorFilled}
                                      onClick={() => onRetryDraftSave?.()}
                                      style={{ color: "var(--cds-support-error)" }}
                                    >
                                      Couldn&apos;t save — Retry
                                    </Button>
                                  )}

                                  {/* Key-source chip — only shown when no test result is up
                                      yet (test result is more informative when available) */}
                                  {!testResult && accel?.key_source?.required && (
                                    <Tag
                                      type={
                                        accel.key_source.source === "missing"
                                          ? "red"
                                          : accel.key_source.source === "ui"
                                            ? "green"
                                            : "magenta"
                                      }
                                      size="sm"
                                      title="Where the embedding API key is being read from."
                                    >
                                      {accel.key_source.source === "ui"
                                        ? "Key: from UI"
                                        : accel.key_source.source === "missing"
                                          ? "Key: missing"
                                          : `Key: ${accel.key_source.source}`}
                                    </Tag>
                                  )}
                                </Stack>

                                {/* Test connection result — use InlineNotification so the
                                    FULL error is visible (no 80-char truncation behind a
                                    title attr). Critical for debugging 401s etc. */}
                                {testResult && (
                                  <InlineNotification
                                    kind={testResult.ok ? "success" : "error"}
                                    lowContrast
                                    hideCloseButton={false}
                                    onCloseButtonClick={() => setTestResult(null)}
                                    title={
                                      testResult.ok
                                        ? `Connected — dim=${testResult.dim}, ${testResult.latency_ms} ms`
                                        : "Test failed"
                                    }
                                    subtitle={
                                      testResult.ok
                                        ? "You can now upload documents on the Documents tab."
                                        : testResult.error || "No detail returned."
                                    }
                                  />
                                )}

                                {/* Full autosave-failure detail. Replaces the
                                    prior native ``title=`` attribute tooltip
                                    on the Retry button (which truncated to
                                    ~80 chars and was invisible on most
                                    screens). Surfaces the full server error
                                    so debugging a 422 / 500 doesn't require
                                    opening the browser network tab. */}
                                {saveState === "failed" && draftSaveStatus?.kind === "failed" && (
                                  <InlineNotification
                                    kind="error"
                                    lowContrast
                                    hideCloseButton={false}
                                    onCloseButtonClick={() => onDismissDraftSave?.()}
                                    title="Couldn't save your changes"
                                    subtitle={draftSaveStatus.error || "No detail returned."}
                                  />
                                )}

                                <Stack orientation="horizontal" gap={3} style={{ alignItems: "center", flexWrap: "wrap" }}>
                                  <Toggle
                                    id="knowledge-use-gpu"
                                    labelText="GPU Acceleration"
                                    labelA="Off"
                                    labelB="On"
                                    toggled={knowledgeConfig.use_gpu ?? true}
                                    onToggle={(checked: boolean) => onKnowledgeConfigChange({ ...knowledgeConfig, use_gpu: checked })}
                                    size="sm"
                                  />
                                  {/* Honest device label: green when GPU engaged, magenta
                                      when GPU requested but ORT loaded CPU only, gray when
                                      not relevant (cloud provider). */}
                                  {accel && (
                                    <Tag
                                      type={
                                        accel.fallback_to_cpu
                                          ? "magenta"
                                          : accel.embedding_relevant
                                            ? "green"
                                            : "gray"
                                      }
                                      size="sm"
                                      renderIcon={
                                        accel.fallback_to_cpu
                                          ? ErrorFilled
                                          : accel.embedding_relevant
                                            ? Checkmark
                                            : undefined
                                      }
                                      title="What the running engine actually loaded for embedding inference."
                                    >
                                      {accel.fallback_to_cpu ? "GPU fallback to CPU" : `Detected: ${accel.device_label}`}
                                    </Tag>
                                  )}
                                </Stack>
                                {showAdvanced && (
                                  <>
                                    <Stack orientation="horizontal" gap={4}>
                                      <NumberInput
                                        id="knowledge-embedding-batch-size"
                                        label="Batch Size"
                                        value={knowledgeConfig.embedding_batch_size ?? 64}
                                        min={1}
                                        max={2048}
                                        step={16}
                                        helperText="Chunks per embed call. Smaller = finer progress; larger = lower per-call overhead."
                                        onChange={((_e: unknown, { value }: { value: number }) => onKnowledgeConfigChange({ ...knowledgeConfig, embedding_batch_size: value })) as any}
                                      />
                                      <NumberInput
                                        id="knowledge-embedding-concurrency"
                                        label="Concurrency"
                                        value={knowledgeConfig.embedding_concurrency ?? 4}
                                        min={1}
                                        max={32}
                                        step={1}
                                        helperText="Parallel embed sub-batches for network providers (OpenAI / Ollama). No effect on local providers."
                                        onChange={((_e: unknown, { value }: { value: number }) => onKnowledgeConfigChange({ ...knowledgeConfig, embedding_concurrency: value })) as any}
                                      />
                                    </Stack>
                                    <NumberInput
                                      id="knowledge-vector-insert-batch-size"
                                      label="Vector Insert Batch Size"
                                      value={knowledgeConfig.vector_insert_batch_size ?? 200}
                                      min={1}
                                      max={5000}
                                      step={50}
                                      helperText="Chunks per add_many transaction. Caps each transaction so a large document does not blow past pgvector's command_timeout or hold the HNSW write lock for long. Default 200 works for typical docs."
                                      onChange={((_e: unknown, { value }: { value: number }) => onKnowledgeConfigChange({ ...knowledgeConfig, vector_insert_batch_size: value })) as any}
                                    />
                                  </>
                                )}
                                {/* Per-section Reset to factory defaults */}
                                <Button kind="ghost" size="sm" renderIcon={Reset}
                                  onClick={() => setResetTarget({
                                    section: "Embeddings",
                                    fields: ["embedding_provider", "embedding_model", "embedding_api_key", "embedding_base_url", "embedding_extra_params", "use_gpu", "embedding_batch_size", "embedding_concurrency", "vector_insert_batch_size"],
                                  })}>
                                  Reset Embeddings to defaults
                                </Button>
                              </Stack>
                            </AccordionItem>

                            <AccordionItem title={sectionTitle("Chunking", chunkingStatus)}>
                              <Stack gap={4} style={{ paddingTop: "0.5rem" }}>
                                {ragProfiles && (knowledgeConfig.rag_profile ?? "standard") !== "custom" && (
                                  <p style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)", margin: 0 }}>
                                    Values set by the <strong>{ragProfiles[knowledgeConfig.rag_profile ?? "standard"]?.name ?? "Standard"}</strong> profile. Edit to override.
                                  </p>
                                )}
                                <Stack orientation="horizontal" gap={4}>
                                  <NumberInput
                                    id="knowledge-chunk-size"
                                    label="Chunk Size"
                                    value={knowledgeConfig.chunk_size ?? 1000}
                                    min={100}
                                    max={10000}
                                    step={100}
                                    onChange={((_e: unknown, { value }: { value: number }) => onKnowledgeConfigChange({ ...knowledgeConfig, chunk_size: value })) as any}
                                  />
                                  <NumberInput
                                    id="knowledge-chunk-overlap"
                                    label="Chunk Overlap"
                                    value={knowledgeConfig.chunk_overlap ?? 200}
                                    min={0}
                                    max={(knowledgeConfig.chunk_size ?? 1000) - 1}
                                    invalid={(knowledgeConfig.chunk_overlap ?? 0) >= (knowledgeConfig.chunk_size ?? 1000)}
                                    invalidText="Overlap must be less than chunk size"
                                    onChange={((_e: unknown, { value }: { value: number }) => onKnowledgeConfigChange({ ...knowledgeConfig, chunk_overlap: value })) as any}
                                  />
                                </Stack>
                                <Button kind="ghost" size="sm" renderIcon={Reset}
                                  onClick={() => setResetTarget({ section: "Chunking", fields: ["chunk_size", "chunk_overlap"] })}>
                                  Reset Chunking to defaults
                                </Button>
                              </Stack>
                            </AccordionItem>

                            <AccordionItem title="Document Parsing (Docling)">
                              <Stack gap={4} style={{ paddingTop: "0.5rem" }}>
                                <Select
                                  id="knowledge-docling-pdf-mode"
                                  labelText="Parsing Level"
                                  helperText="Applies to PDFs and every other Docling-supported format (DOCX, PPTX, HTML, images, …). Trades parse speed for extraction fidelity. Saved with the published config snapshot."
                                  value={knowledgeConfig.docling_pdf_mode ?? "accurate"}
                                  onChange={(e: any) => onKnowledgeConfigChange({ ...knowledgeConfig, docling_pdf_mode: e.target.value })}
                                >
                                  <SelectItem value="fast" text="Fast — OCR off, tables off (digital docs only; ~3–10× faster)" />
                                  <SelectItem value="balanced" text="Balanced — OCR off, tables on (digital docs with tables)" />
                                  <SelectItem value="accurate" text="Accurate (default) — OCR on, tables on (scanned docs supported)" />
                                </Select>
                                <p style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)", margin: 0 }}>
                                  ⚠️ <strong>Fast</strong> mode will extract little or no text from scanned documents. Use <strong>Accurate</strong> for OCR.
                                </p>
                                {showAdvanced && (
                                <Select
                                  id="knowledge-docling-layout-engine"
                                  labelText="Layout Engine"
                                  helperText="Which backend runs the layout-detection model. Auto = ONNX (default; CPU on Mac, CUDA on NVIDIA). Transformers = PyTorch (only way to engage Apple GPU/MPS; ~500 MB more RAM)."
                                  value={knowledgeConfig.docling_layout_engine ?? "auto"}
                                  onChange={(e: any) => onKnowledgeConfigChange({ ...knowledgeConfig, docling_layout_engine: e.target.value })}
                                >
                                  <SelectItem value="auto" text="Auto (default) — ONNX, fastest startup on CPU & NVIDIA" />
                                  <SelectItem value="onnx" text="ONNX — explicit (same as Auto today; pinned)" />
                                  <SelectItem value="transformers" text="Transformers (PyTorch) — engages MPS on Mac / CUDA on NVIDIA" />
                                </Select>
                                )}
                                <Button kind="ghost" size="sm" renderIcon={Reset}
                                  onClick={() => setResetTarget({ section: "Document Parsing", fields: ["docling_pdf_mode", "docling_layout_engine"] })}>
                                  Reset Document Parsing to defaults
                                </Button>
                              </Stack>
                            </AccordionItem>

                            {showAdvanced &&
                              (() => {
                                // Operator-only query transformation. Off everywhere by default
                                // (eval-gated). The original query always runs; this only adds
                                // extra retrieval legs, and the engine fails open on LLM error.
                                const qtVal = knowledgeConfig.search_query_transform ?? "off";
                                const qtProfile = knowledgeConfig.rag_profile ?? "standard";
                                const qtDefault = ragProfiles?.[qtProfile]?.search?.query_transform ?? "off";
                                const qtLabels: Record<string, string> = {
                                  off: "Off",
                                  multi_query: "Reword the query (multi-query)",
                                  hyde: "Draft an ideal answer first (HyDE)",
                                };
                                return (
                                  <AccordionItem title="Retrieval behavior">
                                    <Stack gap={4} style={{ paddingTop: "0.5rem" }}>
                                      <Select
                                        id="knowledge-query-transform"
                                        labelText="Query expansion"
                                        helperText="Rewrites the query before searching to catch documents that word things differently. Adds one LLM call (~0.3–1.5s) per search and may not improve every corpus — leave Off unless you've seen it help. The original query still runs, and if the rewrite errors or times out the plain query is used."
                                        value={qtVal}
                                        onChange={(e: any) =>
                                          onKnowledgeConfigChange?.({ ...knowledgeConfig, search_query_transform: e.target.value })
                                        }
                                      >
                                        <SelectItem value="off" text="Off — search the query as typed (default)" />
                                        <SelectItem value="multi_query" text="Reword the query (multi-query) — try a few alternate phrasings" />
                                        <SelectItem value="hyde" text="Draft an ideal answer first (HyDE) — match against a hypothetical passage" />
                                      </Select>
                                      <p style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)", margin: 0 }}>
                                        Default for the {ragProfiles?.[qtProfile]?.name ?? qtProfile} profile:{" "}
                                        {qtLabels[qtDefault] ?? qtDefault}
                                        {qtVal !== qtDefault && (
                                          <Button
                                            kind="ghost"
                                            size="sm"
                                            onClick={() =>
                                              onKnowledgeConfigChange?.({ ...knowledgeConfig, search_query_transform: qtDefault })
                                            }
                                          >
                                            Reset to profile default
                                          </Button>
                                        )}
                                      </p>
                                      {qtVal !== "off" && (
                                        <InlineNotification
                                          kind="info"
                                          lowContrast
                                          hideCloseButton
                                          title="Adds an extra AI step per search"
                                          subtitle="Every search now makes one model call first — about 0.3–1.5s slower, with a small added cost. Watch latency after enabling and switch back to Off if recall doesn't improve."
                                        />
                                      )}
                                    </Stack>
                                  </AccordionItem>
                                );
                              })()}

                            {showAdvanced && (
                              <AccordionItem title="Score & Metric">
                                <Stack gap={4} style={{ paddingTop: "0.5rem" }}>
                                  {/* Read-only: only COSINE is supported end-to-end
                                      (config.validate() rejects IP/L2), so a dropdown here could
                                      only ever fail the save. Restore a Select when multi-metric ships. */}
                                  <div>
                                    <p style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)", margin: 0 }}>
                                      Distance Metric
                                    </p>
                                    <p style={{ fontSize: "0.875rem", color: "var(--cds-text-primary)", margin: "0.25rem 0 0 0" }}>
                                      Cosine Similarity
                                    </p>
                                  </div>
                                </Stack>
                              </AccordionItem>
                            )}

                            {showAdvanced && (
                            <AccordionItem title="Limits">
                              <Stack gap={4} style={{ paddingTop: "0.5rem" }}>
                                <Stack orientation="horizontal" gap={4}>
                                  <NumberInput
                                    id="knowledge-max-upload"
                                    label="Max Upload Size (MB)"
                                    value={knowledgeConfig.max_upload_size_mb ?? 100}
                                    min={1} max={1000}
                                    onChange={((_e: unknown, { value }: { value: number }) => onKnowledgeConfigChange({ ...knowledgeConfig, max_upload_size_mb: value })) as any}
                                  />
                                  <NumberInput
                                    id="knowledge-max-files"
                                    label="Max Files per Request"
                                    value={knowledgeConfig.max_files_per_request ?? 10}
                                    min={1} max={100}
                                    onChange={((_e: unknown, { value }: { value: number }) => onKnowledgeConfigChange({ ...knowledgeConfig, max_files_per_request: value })) as any}
                                  />
                                </Stack>
                                <Stack orientation="horizontal" gap={4}>
                                  <NumberInput
                                    id="knowledge-max-url-download"
                                    label="Max URL Download (MB)"
                                    value={knowledgeConfig.max_url_download_size_mb ?? 50}
                                    min={1} max={500}
                                    onChange={((_e: unknown, { value }: { value: number }) => onKnowledgeConfigChange({ ...knowledgeConfig, max_url_download_size_mb: value })) as any}
                                  />
                                  <NumberInput
                                    id="knowledge-max-chunks"
                                    label="Max Chunks per Document"
                                    value={knowledgeConfig.max_chunks_per_document ?? 10000}
                                    min={100} max={100000} step={1000}
                                    onChange={((_e: unknown, { value }: { value: number }) => onKnowledgeConfigChange({ ...knowledgeConfig, max_chunks_per_document: value })) as any}
                                  />
                                </Stack>
                                <NumberInput
                                  id="knowledge-max-pending"
                                  label="Max Pending Tasks"
                                  value={knowledgeConfig.max_pending_tasks ?? 10}
                                  min={1} max={50}
                                  onChange={((_e: unknown, { value }: { value: number }) => onKnowledgeConfigChange({ ...knowledgeConfig, max_pending_tasks: value })) as any}
                                />
                                <Button kind="ghost" size="sm" renderIcon={Reset}
                                  onClick={() => setResetTarget({ section: "Limits", fields: ["max_upload_size_mb", "max_files_per_request", "max_url_download_size_mb", "max_chunks_per_document", "max_pending_tasks"] })}>
                                  Reset Limits to defaults
                                </Button>
                              </Stack>
                            </AccordionItem>
                            )}
                          </Accordion>
                          )}

                          {/* === Reset to defaults — confirmation modal ===
                              Confirmation modal so a stray click doesn't wipe
                              the user's tuned values. Only resets the fields
                              listed in resetTarget.fields — never touches
                              other sections. */}
                          {resetTarget && (
                            <ComposedModal
                              open
                              onClose={() => setResetTarget(null)}
                              size="sm"
                            >
                              <ModalHeader title={`Reset ${resetTarget.section} to defaults?`} />
                              <ModalBody>
                                <p style={{ marginBottom: "0.75rem" }}>
                                  This restores the <strong>{resetTarget.section}</strong> section to factory defaults.
                                  Your other knowledge settings are untouched.
                                </p>
                                <p style={{ fontSize: "0.8125rem", color: "var(--cds-text-secondary)", margin: 0 }}>
                                  Fields affected: <code>{resetTarget.fields.join(", ")}</code>
                                </p>
                              </ModalBody>
                              <ModalFooter>
                                <Button kind="secondary" onClick={() => setResetTarget(null)}>
                                  Cancel
                                </Button>
                                <Button kind="danger" renderIcon={Reset} onClick={performReset}>
                                  Reset section
                                </Button>
                              </ModalFooter>
                            </ComposedModal>
                          )}
                        </>
                      )}
                    </Stack>
                    ) : (
                      <Tile>
                        <p style={{ color: "var(--cds-text-secondary)", margin: 0 }}>
                          Knowledge settings are managed from the Settings page.
                        </p>
                      </Tile>
                    )}
                  </TabPanel>

                </TabPanels>
              </Tabs>
            </Stack>
          </Theme>
        </ModalBody>

        {/* Custom footer — avoids Carbon's ModalFooter which wraps in <form> and causes page navigation */}
        <div style={{
          display: "flex", justifyContent: "flex-end", gap: "0.5rem",
          padding: "1rem", borderTop: "1px solid var(--cds-border-subtle)",
          background: "var(--cds-layer-01)",
        }}>
          <Button type="button" kind="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      </ComposedModal>

      {/* Delete confirmation modal */}
      {deleteConfirm && (
        <ComposedModal
          open
          onClose={() => setDeleteConfirm(null)}
          size="sm"
          preventCloseOnClickOutside
        >
          <ModalHeader title="Delete document?" buttonOnClick={() => setDeleteConfirm(null)} />
          <ModalBody>
            <p>
              Are you sure you want to delete <strong>{deleteConfirm}</strong>? This action cannot be undone.
            </p>
          </ModalBody>
          <ModalFooter>
            <Button type="button" kind="secondary" onClick={() => setDeleteConfirm(null)}>
              Cancel
            </Button>
            <Button type="button" kind="danger" renderIcon={TrashCan} onClick={() => handleDelete(deleteConfirm)}>
              Delete
            </Button>
          </ModalFooter>
        </ComposedModal>
      )}
    </>
  );
}
