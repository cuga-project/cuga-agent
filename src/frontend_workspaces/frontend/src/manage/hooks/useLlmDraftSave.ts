import { useCallback, useEffect, useRef, type MutableRefObject } from "react";
import * as api from "../../api";
import { isAbortError, type AddToast } from "./saveHelpers";

export function useLlmDraftSave(opts: {
  llmConfigRef: MutableRefObject<unknown>;
  effectiveAgentId: string | undefined;
  addToast: AddToast;
  setDraftSaving: (v: boolean) => void;
  setCurrentVersion: (v: number | "draft" | null) => void;
}) {
  const { llmConfigRef, effectiveAgentId, addToast, setDraftSaving, setCurrentVersion } = opts;
  const llmAbortRef = useRef<AbortController | null>(null);
  const llmBlurSaveRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      llmAbortRef.current?.abort();
      if (llmBlurSaveRef.current) clearTimeout(llmBlurSaveRef.current);
    };
  }, []);

  const saveLlmDraft = useCallback(async () => {
    setDraftSaving(true);
    llmAbortRef.current?.abort();
    const ac = new AbortController();
    llmAbortRef.current = ac;
    try {
      const res = await api.patchManageConfigDraftLlm(llmConfigRef.current, effectiveAgentId, ac.signal);
      if (ac.signal.aborted) return;
      setDraftSaving(false);
      if (res.ok) {
        setCurrentVersion("draft");
        addToast("success", "Draft saved", "LLM settings saved to draft");
      } else {
        addToast("error", "Draft Save Failed", `Failed to save LLM (${res.status} ${res.statusText})`);
      }
    } catch (error) {
      if (isAbortError(error)) return;
      setDraftSaving(false);
      addToast("error", "Draft Save Failed", error instanceof Error ? error.message : "Network error");
    }
  }, [addToast, effectiveAgentId, llmConfigRef, setCurrentVersion, setDraftSaving]);

  const scheduleLlmDraftSave = useCallback(() => {
    if (llmBlurSaveRef.current) clearTimeout(llmBlurSaveRef.current);
    llmBlurSaveRef.current = setTimeout(() => {
      llmBlurSaveRef.current = null;
      saveLlmDraft();
    }, 100);
  }, [saveLlmDraft]);

  return { saveLlmDraft, scheduleLlmDraftSave };
}
