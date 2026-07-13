import { useCallback, useEffect, useRef } from "react";
import * as api from "../../api";
import { isAbortError, type AddToast } from "./saveHelpers";

export function useFullDraftSave(opts: {
  assembleConfig: (overrides?: object) => object;
  effectiveAgentId: string | undefined;
  addToast: AddToast;
  setDraftSaving: (v: boolean) => void;
  setCurrentVersion: (v: number | "draft" | null) => void;
  importStatus: "idle" | "ok" | "error";
  refreshKnowledgeHealth: () => void | Promise<void>;
}) {
  const {
    assembleConfig,
    effectiveAgentId,
    addToast,
    setDraftSaving,
    setCurrentVersion,
    importStatus,
    refreshKnowledgeHealth,
  } = opts;

  const fullSaveAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      fullSaveAbortRef.current?.abort();
    };
  }, []);

  const performDraftSave = useCallback(
    async (partial?: object) => {
      const toSave = partial ? { ...assembleConfig(), ...partial } : assembleConfig();
      setDraftSaving(true);
      fullSaveAbortRef.current?.abort();
      const ac = new AbortController();
      fullSaveAbortRef.current = ac;
      try {
        const res = await api.postManageConfigDraft(toSave, effectiveAgentId, ac.signal);
        if (ac.signal.aborted) return;
        setDraftSaving(false);
        if (res.ok) {
          const data = await res.json().catch(() => ({}));
          if (ac.signal.aborted) return;
          setCurrentVersion("draft");
          const hasPartialErrors = data.status === "partial" && (data.tool_errors || data.policy_errors);
          if (hasPartialErrors) {
            if (data.tool_errors) {
              Object.entries(data.tool_errors as Record<string, { error?: string; message?: string; type?: string }>).forEach(
                ([toolName, err]) => {
                  const msg = err?.error || err?.message || "Unknown error";
                  const type = err?.type ? ` (${err.type})` : "";
                  addToast("warning", `Tool failed: ${toolName}`, `${msg}${type}`);
                },
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
        if (isAbortError(error)) return;
        setDraftSaving(false);
        const errorMsg = error instanceof Error ? error.message : "Network error saving draft";
        addToast("error", "Draft Save Failed", errorMsg);
      }
    },
    [addToast, assembleConfig, effectiveAgentId, setCurrentVersion, setDraftSaving],
  );

  useEffect(() => {
    if (importStatus !== "ok") return;
    let cancelled = false;
    (async () => {
      await performDraftSave();
      if (!cancelled) void refreshKnowledgeHealth();
    })();
    return () => {
      cancelled = true;
    };
  }, [importStatus, performDraftSave, refreshKnowledgeHealth]);

  return { performDraftSave };
}
