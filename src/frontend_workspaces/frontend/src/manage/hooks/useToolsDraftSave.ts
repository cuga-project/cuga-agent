import { useEffect, useRef, type MutableRefObject } from "react";
import * as api from "../../api";
import { isAbortError, type AddToast } from "./saveHelpers";
import type { ToolEntry } from "../../types/tools";

export function useToolsDraftSave(opts: {
  tools: ToolEntry[];
  effectiveAgentId: string | undefined;
  addToast: AddToast;
  skipDraftSaveRef: MutableRefObject<boolean>;
  setDraftSaving: (v: boolean) => void;
  setCurrentVersion: (v: number | "draft" | null) => void;
}) {
  const { tools, effectiveAgentId, addToast, skipDraftSaveRef, setDraftSaving, setCurrentVersion } = opts;
  const toolsAbortRef = useRef<AbortController | null>(null);
  const toolsSaveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      toolsAbortRef.current?.abort();
      if (toolsSaveTimeoutRef.current) clearTimeout(toolsSaveTimeoutRef.current);
    };
  }, []);

  useEffect(() => {
    if (skipDraftSaveRef.current) return;
    toolsAbortRef.current?.abort();
    const ac = new AbortController();
    toolsAbortRef.current = ac;
    const t = setTimeout(() => {
      toolsSaveTimeoutRef.current = null;
      if (toolsAbortRef.current !== ac) return;
      (async () => {
        setDraftSaving(true);
        try {
          const res = await api.patchManageConfigDraftTools(tools, effectiveAgentId, ac.signal);
          if (ac.signal.aborted) return;
          setDraftSaving(false);
          if (res.ok) {
            setCurrentVersion("draft");
            const data = await res.json().catch(() => ({}));
            if (ac.signal.aborted) return;
            if (data.status === "partial" && data.tool_errors) {
              Object.entries(data.tool_errors as Record<string, { error?: string; message?: string }>).forEach(
                ([toolName, err]) => addToast("warning", `Tool: ${toolName}`, err?.error || err?.message || "Unknown error"),
              );
              addToast("info", "Draft saved with warnings", "Some tools failed to load");
            } else {
              addToast("success", "Draft saved", "Tools saved to draft");
            }
          } else {
            addToast("error", "Draft Save Failed", `Failed to save tools (${res.status} ${res.statusText})`);
          }
        } catch (error) {
          if (isAbortError(error)) return;
          setDraftSaving(false);
          addToast("error", "Draft Save Failed", error instanceof Error ? error.message : "Network error");
        }
      })();
    }, 500);
    toolsSaveTimeoutRef.current = t;
    return () => {
      if (toolsSaveTimeoutRef.current) clearTimeout(toolsSaveTimeoutRef.current);
    };
  }, [tools, effectiveAgentId, addToast, skipDraftSaveRef, setCurrentVersion, setDraftSaving]);
}
