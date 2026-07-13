import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../../api";
import { isAbortError, type AddToast } from "./saveHelpers";
import type { DraftSaveStatus } from "./useKnowledgeDraftSave";

type LiveKnowledge = {
  provider: string;
  model: string;
  version: number | null;
  chunk_size?: number;
  chunk_overlap?: number;
  metric_type?: string;
};

type KnowledgeConfig = {
  embedding_provider?: unknown;
  embedding_model?: unknown;
  chunk_size?: unknown;
  chunk_overlap?: unknown;
  metric_type?: unknown;
};

export function usePublishConfig(opts: {
  assembleConfig: () => { policies?: unknown };
  agentName: string;
  knowledgeConfig: KnowledgeConfig;
  knowledgeReindexNeeded: boolean;
  knowledgeDocCount: number;
  effectiveAgentId: string | undefined;
  addToast: AddToast;
  setSaveStatus: (v: "idle" | "saving" | "success" | "error") => void;
  setCurrentVersion: (v: number | "draft" | null) => void;
  setLiveKnowledge: (v: LiveKnowledge) => void;
  setKnowledgeSavedSnapshot: (v: any) => void;
  setKnowledgeDocCount: (v: number) => void;
  setDraftSaveStatus: (v: DraftSaveStatus) => void;
  refreshKnowledgeHealth: () => void;
  loadHistory: () => void;
}) {
  const {
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
  } = opts;

  const [showReindexConfirm, setShowReindexConfirm] = useState(false);
  const publishAbortRef = useRef<AbortController | null>(null);
  const reindexPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reindexTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearReindexTimers = useCallback(() => {
    if (reindexPollRef.current) {
      clearInterval(reindexPollRef.current);
      reindexPollRef.current = null;
    }
    if (reindexTimeoutRef.current) {
      clearTimeout(reindexTimeoutRef.current);
      reindexTimeoutRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      publishAbortRef.current?.abort();
      clearReindexTimers();
    };
  }, [clearReindexTimers]);

  const saveConfig = useCallback(async () => {
    setShowReindexConfirm(false);
    if (!agentName.trim()) {
      addToast("error", "Agent name required", "Please enter an agent name before publishing.");
      return;
    }
    setSaveStatus("saving");
    publishAbortRef.current?.abort();
    clearReindexTimers();
    const ac = new AbortController();
    publishAbortRef.current = ac;
    try {
      let toSave = assembleConfig();
      if (!toSave.policies) {
        toSave = { ...toSave, policies: { enablePolicies: true, policies: [] } };
      }
      const res = await api.postManageConfig(toSave, effectiveAgentId, ac.signal);
      if (ac.signal.aborted) return;
      if (res.ok) {
        const data = await res.json();
        if (ac.signal.aborted) return;

        const hasPartialErrors = data.status === "partial" && data.tool_errors;

        if (hasPartialErrors) {
          Object.entries(data.tool_errors as Record<string, { error?: string; message?: string; type?: string }>).forEach(
            ([toolName, errorInfo]) => {
              const errorMsg = errorInfo.error || errorInfo.message || "Unknown error";
              const errorType = errorInfo.type ? ` (${errorInfo.type})` : "";
              addToast("warning", `Tool initialization failed: ${toolName}`, `${errorMsg}${errorType}`);
            },
          );

          const errorCount = Object.keys(data.tool_errors).length;
          addToast("info", "Configuration partially saved", data.message || `${errorCount} tool(s) failed to initialize`);
        }

        if (data.partial_errors && Array.isArray(data.partial_errors) && data.partial_errors.length > 0) {
          data.partial_errors.forEach((error: unknown) => {
            const errorMsg =
              typeof error === "string"
                ? error
                : ((error as { message?: string; error?: string }).message ||
                    (error as { error?: string }).error ||
                    "Unknown error");
            addToast("warning", "Partial save error", errorMsg);
          });
        }

        if (data.reindex && data.reindex.status === "started") {
          const taskIds: string[] = data.reindex.task_ids ?? [];
          const total = data.reindex.count ?? taskIds.length;
          setSaveStatus("saving");
          addToast("info", "Publishing", `Re-indexing ${total} document(s)...`);

          if (taskIds.length > 0) {
            await new Promise<void>((resolve) => {
              let polling = false;
              const cleanup = () => {
                clearReindexTimers();
                resolve();
              };

              reindexPollRef.current = setInterval(async () => {
                if (ac.signal.aborted) {
                  cleanup();
                  return;
                }
                if (polling) return;
                polling = true;
                try {
                  const statuses = await Promise.all(
                    taskIds.map((tid: string) =>
                      api
                        .getKnowledgeTaskStatus(tid)
                        .then((r) => (r.ok ? r.json() : { status: "unknown" }))
                        .catch(() => ({ status: "unknown" })),
                    ),
                  );
                  if (ac.signal.aborted) {
                    cleanup();
                    return;
                  }
                  const completed = statuses.filter((t: { status?: string }) => t.status === "completed").length;
                  const failed = statuses.filter((t: { status?: string }) => t.status === "failed").length;

                  if (completed + failed >= taskIds.length) {
                    cleanup();
                    if (failed === 0) {
                      addToast("success", "Re-index complete", `All ${completed} document(s) re-indexed.`);
                    } else {
                      addToast("warning", "Re-index partial", `${completed} succeeded, ${failed} failed.`);
                    }
                    api
                      .listKnowledgeDocuments()
                      .then((r) => (r.ok ? r.json() : null))
                      .then((d) => {
                        if (ac.signal.aborted) return;
                        if (d) setKnowledgeDocCount(d.documents?.length ?? 0);
                      })
                      .catch(() => {});
                  }
                } catch {
                  cleanup();
                } finally {
                  polling = false;
                }
              }, 2000);

              reindexTimeoutRef.current = setTimeout(() => {
                cleanup();
                if (ac.signal.aborted) return;
                addToast("warning", "Re-index timeout", "Still running. Check knowledge health.");
              }, 300000);
            });
          }
        } else if (data.reindex && data.reindex.status === "busy") {
          addToast("warning", "Re-index deferred", "Uploads in progress. Re-publish after uploads complete.");
        }

        if (ac.signal.aborted) return;

        setCurrentVersion(typeof data.version === "number" ? data.version : "draft");
        setSaveStatus("success");
        setLiveKnowledge({
          provider:
            typeof knowledgeConfig.embedding_provider === "string"
              ? knowledgeConfig.embedding_provider
              : "fastembed",
          model:
            typeof knowledgeConfig.embedding_model === "string" && knowledgeConfig.embedding_model
              ? knowledgeConfig.embedding_model
              : "(default)",
          version: typeof data.version === "number" ? data.version : null,
          chunk_size: typeof knowledgeConfig.chunk_size === "number" ? knowledgeConfig.chunk_size : undefined,
          chunk_overlap:
            typeof knowledgeConfig.chunk_overlap === "number" ? knowledgeConfig.chunk_overlap : undefined,
          metric_type: typeof knowledgeConfig.metric_type === "string" ? knowledgeConfig.metric_type : undefined,
        });
        setKnowledgeSavedSnapshot({ ...knowledgeConfig });
        refreshKnowledgeHealth();
        setDraftSaveStatus({ kind: "idle" });
        if (!hasPartialErrors && (!data.partial_errors || data.partial_errors.length === 0)) {
          addToast("success", "Configuration saved", "Your configuration has been saved successfully");
        }
        loadHistory();
        setTimeout(() => {
          if (ac.signal.aborted) return;
          setSaveStatus("idle");
        }, 2000);
      } else {
        let errorMsg = `Failed to save configuration (${res.status} ${res.statusText})`;
        try {
          const errorData = await res.json();
          errorMsg = errorData.detail || errorData.error || errorData.message || errorMsg;
        } catch {
          // non-JSON
        }

        setSaveStatus("error");
        addToast("error", "Save Failed", errorMsg);
        setTimeout(() => {
          if (ac.signal.aborted) return;
          setSaveStatus("idle");
        }, 2000);
      }
    } catch (error) {
      if (isAbortError(error) || ac.signal.aborted) return;
      const errorMsg = error instanceof Error ? error.message : "Network error occurred";
      setSaveStatus("error");
      addToast("error", "Network Error", errorMsg);
      setTimeout(() => {
        if (ac.signal.aborted) return;
        setSaveStatus("idle");
      }, 2000);
    }
  }, [
    agentName,
    assembleConfig,
    knowledgeConfig,
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
    clearReindexTimers,
  ]);

  const handleSaveClick = useCallback(() => {
    if (knowledgeReindexNeeded && knowledgeDocCount > 0) {
      setShowReindexConfirm(true);
    } else {
      saveConfig();
    }
  }, [knowledgeReindexNeeded, knowledgeDocCount, saveConfig]);

  return { showReindexConfirm, setShowReindexConfirm, handleSaveClick, saveConfig };
}
