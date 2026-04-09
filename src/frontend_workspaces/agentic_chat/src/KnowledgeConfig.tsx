// eslint-disable-next-line @typescript-eslint/no-unused-vars
import React, { useState, useEffect, useRef, useCallback } from "react";
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
  InlineNotification,
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
} from "@carbon/react";
import { Upload, TrashCan, Search, Renew, Document, Checkmark, ErrorFilled } from "@carbon/icons-react";
import { apiFetch } from "../../frontend/src/api";
import * as api from "../../frontend/src/api";
import "./ConfigModal.css";

// ---------------------------------------------------------------------------
// Reindex progress types
// ---------------------------------------------------------------------------
interface ReindexTask {
  task_id: string;
  filename?: string;
  status: "pending" | "running" | "completed" | "failed";
  file_tasks?: Record<string, { filename?: string; status?: string; error?: string }>;
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
  use_gpu?: boolean;
  chunk_size?: number;
  chunk_overlap?: number;
  metric_type?: string;
  max_pending_tasks?: number;
  max_upload_size_mb?: number;
  max_url_download_size_mb?: number;
  max_files_per_request?: number;
  max_chunks_per_document?: number;
}

interface RagProfileMeta {
  name: string;
  description: string;
  search: { max_search_attempts?: number; default_limit?: number; default_score_threshold?: number };
  chunking: { chunk_size?: number; chunk_overlap?: number };
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
  onReindex?: () => Promise<{ count: number; task_ids: string[] } | null>;
  knowledgeReindexing?: boolean;
  ragProfiles?: Record<string, RagProfileMeta>;
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
}: KnowledgePanelProps) {
  const [tabIndex, setTabIndex] = useState(0);
  const knowledgeEnabled = knowledgeConfig?.enabled ?? true;
  const agentLevelEnabled = knowledgeEnabled && (knowledgeConfig?.agent_level_enabled ?? true);
  const sessionLevelEnabled = knowledgeEnabled && (knowledgeConfig?.session_level_enabled ?? true);

  // Documents tab state
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Per-file upload status shown inline in the document list
  // `displayName` = original browser name (shown in UI), `backendName` = sanitized name (for matching)
  const [uploadingFiles, setUploadingFiles] = useState<
    { name: string; backendName?: string; status: "uploading" | "success" | "error"; error?: string; taskId?: string }[]
  >([]);

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

  // Reindex progress state
  const [reindexProgress, setReindexProgress] = useState<{
    taskIds: string[];
    total: number;
    completed: number;
    failed: number;
    tasks: ReindexTask[];
    done: boolean;
  } | null>(null);
  const reindexPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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

  // Cleanup reindex polling on unmount
  useEffect(() => {
    return () => {
      if (reindexPollRef.current) clearInterval(reindexPollRef.current);
    };
  }, []);

  // -------------------------------------------------------------------------
  // Reindex with progress tracking
  // -------------------------------------------------------------------------
  const startReindexWithProgress = useCallback(async () => {
    if (!onReindex) return;
    const result = await onReindex();
    if (!result || !result.task_ids?.length) return;

    const taskIds = result.task_ids;
    let initialTasks: ReindexTask[] = taskIds.map((id) => ({ task_id: id, status: "pending" as const }));
    try {
      const res = await api.getKnowledgeTasks();
      if (res.ok) {
        const data = await res.json();
        const allTasks: ReindexTask[] = data.tasks ?? [];
        const relevantTasks = allTasks
          .filter((t: ReindexTask) => taskIds.includes(t.task_id))
          .map((task) => ({
            ...task,
            filename: getReindexTaskFilename(task),
          }));
        if (relevantTasks.length > 0) {
          initialTasks = relevantTasks;
        }
      }
    } catch {
      // Fall back to task IDs until polling resolves filenames.
    }
    setReindexProgress({
      taskIds,
      total: result.count,
      completed: 0,
      failed: 0,
      tasks: initialTasks,
      done: false,
    });

    // Poll task statuses every 2s
    if (reindexPollRef.current) clearInterval(reindexPollRef.current);
    reindexPollRef.current = setInterval(async () => {
      try {
        const res = await api.getKnowledgeTasks();
        if (!res.ok) return;
        const data = await res.json();
        const allTasks: ReindexTask[] = data.tasks ?? [];
        // Filter to only our reindex tasks
        const relevantTasks = allTasks
          .filter((t: ReindexTask) => taskIds.includes(t.task_id))
          .map((task) => ({
            ...task,
            filename: getReindexTaskFilename(task),
          }));
        const completed = relevantTasks.filter((t: ReindexTask) => t.status === "completed").length;
        const failed = relevantTasks.filter((t: ReindexTask) => t.status === "failed").length;
        const done = completed + failed >= taskIds.length;

        setReindexProgress({
          taskIds,
          total: taskIds.length,
          completed,
          failed,
          tasks: relevantTasks,
          done,
        });

        if (done) {
          if (reindexPollRef.current) clearInterval(reindexPollRef.current);
          reindexPollRef.current = null;
          // Refresh document list after reindex completes
          loadDocuments();
          checkHealth();
          if (failed === 0) {
            onToast?.("success", "Re-index complete", `${completed} document(s) re-indexed successfully.`);
          } else {
            onToast?.("warning", "Re-index finished", `${completed} succeeded, ${failed} failed.`);
          }
        }
      } catch {
        // Polling failure is transient, keep trying
      }
    }, 2000);
  }, [onReindex, loadDocuments, checkHealth, onToast]);

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

    // Debounce doc list refresh — multiple completions within 500ms trigger one refresh
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    const scheduleRefresh = () => {
      if (refreshTimer) clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => loadDocuments(), 500);
    };

    // Upload each file individually in parallel — each request awaits its own ingestion
    const uploadOne = async (file: File, entryId: string) => {
      try {
        const res = await api.uploadKnowledgeDocument(file);
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }));
          setUploadingFiles((prev) =>
            prev.map((f) => f.taskId === entryId
              ? { ...f, status: "error" as const, error: err.detail || "Failed" }
              : f)
          );
          return;
        }
        // Response contains the final completed/failed task (backend awaited ingestion)
        const task = await res.json();
        // Single-file upload returns the task directly (not wrapped in {tasks: [...]})
        const finalTask = task.tasks ? task.tasks[0] : task;

        if (finalTask.status === "completed") {
          setUploadingFiles((prev) =>
            prev.map((f) => f.taskId === entryId
              ? { ...f, backendName: finalTask.filename, status: "success" as const }
              : f)
          );
          scheduleRefresh();
          setTimeout(() => {
            setUploadingFiles((prev) => prev.filter((f) => f.taskId !== entryId));
          }, 3000);
        } else {
          const fileInfo = Object.values(finalTask.file_tasks || {})[0] as { error?: string } | undefined;
          setUploadingFiles((prev) =>
            prev.map((f) => f.taskId === entryId
              ? { ...f, backendName: finalTask.filename, status: "error" as const, error: fileInfo?.error || "Ingestion failed" }
              : f)
          );
        }
      } catch (e: any) {
        setUploadingFiles((prev) =>
          prev.map((f) => f.taskId === entryId
            ? { ...f, status: "error" as const, error: e.message || "Upload failed" }
            : f)
        );
      }
    };

    // Fire all uploads in parallel — each resolves independently
    await Promise.allSettled(entries.map((entry, i) => uploadOne(fileArray[i], entry.taskId)));
  };

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

                        {/* Upload progress — shown inline above the document list */}
                        {uploadingFiles.length > 0 && (
                          <Stack gap={1}>
                            {uploadingFiles.map((f) => (
                              <Tile key={f.taskId || f.name} style={{
                                borderLeft: `3px solid ${
                                  f.status === "uploading" ? "#4589ff" :
                                  f.status === "success" ? "#24a148" : "#da1e28"
                                }`,
                              }}>
                                <Stack orientation="horizontal" gap={4} style={{ alignItems: "center" }}>
                                  <Document size={16} />
                                  <span style={{ flex: 1, fontSize: "0.875rem" }}>{f.name}</span>
                                  <Tag
                                    type={
                                      f.status === "uploading" ? "blue" :
                                      f.status === "success" ? "green" : "red"
                                    }
                                    size="sm"
                                  >
                                    {f.status === "uploading" ? "Processing..." :
                                     f.status === "success" ? "Indexed" : "Failed"}
                                  </Tag>
                                  {f.status === "error" && (
                                    <Button
                                      type="button"
                                      kind="ghost"
                                      size="sm"
                                      hasIconOnly
                                      renderIcon={TrashCan}
                                      iconDescription="Dismiss"
                                      onClick={() => setUploadingFiles((prev) => prev.filter((x) => x.taskId !== f.taskId))}
                                    />
                                  )}
                                </Stack>
                                {f.error && (
                                  <p style={{ fontSize: "0.75rem", color: "#da1e28", margin: "0.25rem 0 0 1.5rem" }}>
                                    {f.error}
                                  </p>
                                )}
                              </Tile>
                            ))}
                          </Stack>
                        )}


                        {documents.length === 0 && uploadingFiles.length === 0 ? (
                          <Tile>
                            <p style={{ color: "var(--cds-text-secondary)", margin: 0 }}>
                              No documents indexed yet. Upload files to get started.
                            </p>
                          </Tile>
                        ) : (
                          <Stack gap={2}>
                            {documents.filter((doc) => !uploadingFiles.some((f) => (f.backendName || f.name) === doc.filename && f.status !== "error")).map((doc) => (
                              <Tile key={doc.filename} style={{ borderLeft: "3px solid #24a148" }}>
                                <Stack orientation="horizontal" gap={4} style={{ alignItems: "center" }}>
                                  <Document size={16} />
                                  <span style={{ flex: 1, color: "var(--cds-text-primary)", fontSize: "0.875rem" }}>
                                    {doc.filename}
                                  </span>
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
                                </Stack>
                              </Tile>
                            ))}
                          </Stack>
                        )}
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
                                <h4 style={{ margin: 0, fontSize: "0.875rem", fontWeight: 600 }}>Retrieval Profile</h4>
                                <p style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)", margin: 0 }}>
                                  Balance retrieval accuracy against response speed and cost.
                                </p>
                              </Stack>
                              <Stack gap={2}>
                                {Object.entries(ragProfiles).map(([key, profile]) => {
                                  const isNamedProfile = (knowledgeConfig.rag_profile ?? "standard") === key;
                                  const chunkingMatches =
                                    profile.chunking.chunk_size === knowledgeConfig.chunk_size &&
                                    profile.chunking.chunk_overlap === knowledgeConfig.chunk_overlap;
                                  // Only show as selected if both the profile name matches AND chunking values match
                                  const isSelected = isNamedProfile && chunkingMatches;
                                  const willChangeChunking = !chunkingMatches;
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
                                        onKnowledgeConfigChange({
                                          ...knowledgeConfig,
                                          rag_profile: key,
                                          chunk_size: profile.chunking.chunk_size ?? knowledgeConfig.chunk_size,
                                          chunk_overlap: profile.chunking.chunk_overlap ?? knowledgeConfig.chunk_overlap,
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
                                          <span style={{ fontWeight: 600, fontSize: "0.875rem", color: "var(--cds-text-primary)" }}>
                                            {profile.name}
                                          </span>
                                          <p style={{ margin: 0, fontSize: "0.75rem", color: "var(--cds-text-secondary)", lineHeight: 1.5 }}>
                                            {profile.description}
                                          </p>
                                          {!isSelected && willChangeChunking && (
                                            <p style={{ margin: "0.25rem 0 0 0", fontSize: "0.6875rem", color: "var(--cds-support-warning)" }}>
                                              Requires re-indexing existing documents.
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
                                  <h4 style={{ margin: 0, fontSize: "0.875rem", fontWeight: 600 }}>
                                    Re-indexing documents...
                                  </h4>
                                  <p style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)", margin: 0 }}>
                                    {reindexProgress.completed + reindexProgress.failed} of {reindexProgress.total} processed
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
                            />
                          )}

                          {agentLevelEnabled && !reindexProgress && (knowledgeReindexNeeded || knowledgeStale || knowledgeReindexDeferred) && (
                            <Stack gap={3}>
                              <InlineNotification
                                kind="warning"
                                title="Re-index recommended"
                                subtitle="Settings changed. Existing documents may use outdated embeddings."
                                lowContrast
                                hideCloseButton
                              />
                              {onReindex && (
                                <Button
                                  type="button"
                                  kind="danger--tertiary"
                                  size="sm"
                                  disabled={knowledgeReindexing}
                                  onClick={startReindexWithProgress}
                                >
                                  {knowledgeReindexing ? "Starting..." : "Re-index all documents"}
                                </Button>
                              )}
                            </Stack>
                          )}

                          {/* ── 5. Advanced configuration (collapsed by default) ── */}
                          <Accordion align="start" size="md">
                            <AccordionItem title="Embeddings">
                              <Stack gap={4} style={{ paddingTop: "0.5rem" }}>
                                <Stack orientation="horizontal" gap={4}>
                                  <Select
                                    id="knowledge-embedding-provider"
                                    labelText="Provider"
                                    value={knowledgeConfig.embedding_provider ?? "auto"}
                                    onChange={(e: any) => onKnowledgeConfigChange({ ...knowledgeConfig, embedding_provider: e.target.value })}
                                  >
                                    <SelectItem value="auto" text="Auto-detect" />
                                    <SelectItem value="openai" text="OpenAI" />
                                    <SelectItem value="huggingface" text="HuggingFace" />
                                    <SelectItem value="ollama" text="Ollama" />
                                  </Select>
                                  <TextInput
                                    id="knowledge-embedding-model"
                                    labelText="Model"
                                    value={knowledgeConfig.embedding_model ?? ""}
                                    onChange={(e: any) => onKnowledgeConfigChange({ ...knowledgeConfig, embedding_model: e.target.value })}
                                    placeholder="Auto-detect per provider"
                                  />
                                </Stack>
                                <Toggle
                                  id="knowledge-use-gpu"
                                  labelText="GPU Acceleration"
                                  labelA="Off"
                                  labelB="On"
                                  toggled={knowledgeConfig.use_gpu ?? true}
                                  onToggle={(checked: boolean) => onKnowledgeConfigChange({ ...knowledgeConfig, use_gpu: checked })}
                                  size="sm"
                                />
                              </Stack>
                            </AccordionItem>

                            <AccordionItem title="Chunking">
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
                              </Stack>
                            </AccordionItem>

                            <AccordionItem title="Score & Metric">
                              <Stack gap={4} style={{ paddingTop: "0.5rem" }}>
                                <Select
                                  id="knowledge-metric-type"
                                  labelText="Distance Metric"
                                  value={knowledgeConfig.metric_type ?? "COSINE"}
                                  onChange={(e: any) => onKnowledgeConfigChange({ ...knowledgeConfig, metric_type: e.target.value })}
                                >
                                  <SelectItem value="COSINE" text="Cosine Similarity" />
                                  <SelectItem value="IP" text="Inner Product" />
                                  <SelectItem value="L2" text="L2 Distance" />
                                </Select>
                              </Stack>
                            </AccordionItem>

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
                              </Stack>
                            </AccordionItem>
                          </Accordion>
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
