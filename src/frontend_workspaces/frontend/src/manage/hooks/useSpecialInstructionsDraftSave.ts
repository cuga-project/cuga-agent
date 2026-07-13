import { useCallback, useEffect, useRef } from "react";
import * as api from "../../api";
import { isAbortError, type AddToast } from "./saveHelpers";

export function useSpecialInstructionsDraftSave(opts: {
  effectiveAgentId: string | undefined;
  addToast: AddToast;
  setDraftSaving: (v: boolean) => void;
  setCurrentVersion: (v: number | "draft" | null) => void;
}) {
  const { effectiveAgentId, addToast, setDraftSaving, setCurrentVersion } = opts;
  const specialInstructionsAbortRef = useRef<AbortController | null>(null);
  const specialInstructionsSaveRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      specialInstructionsAbortRef.current?.abort();
      if (specialInstructionsSaveRef.current) clearTimeout(specialInstructionsSaveRef.current);
    };
  }, []);

  const saveSpecialInstructionsDraft = useCallback(
    async (value: string, showToast = false) => {
      if (showToast) setDraftSaving(true);
      specialInstructionsAbortRef.current?.abort();
      const ac = new AbortController();
      specialInstructionsAbortRef.current = ac;
      try {
        const res = await api.patchManageConfigDraftSpecialInstructions(value, effectiveAgentId, ac.signal);
        if (ac.signal.aborted) return;
        if (showToast) setDraftSaving(false);
        if (res.ok) {
          setCurrentVersion("draft");
          if (showToast) addToast("success", "Draft saved", "Special instructions saved to draft");
        } else {
          addToast("error", "Draft Save Failed", `Failed to save (${res.status} ${res.statusText})`);
        }
      } catch (err) {
        if (isAbortError(err)) return;
        if (showToast) setDraftSaving(false);
        addToast("error", "Draft Save Failed", err instanceof Error ? err.message : "Network error");
      }
    },
    [effectiveAgentId, addToast, setCurrentVersion, setDraftSaving],
  );

  const scheduleSpecialInstructionsDraftSave = useCallback(
    (value: string) => {
      if (specialInstructionsSaveRef.current) clearTimeout(specialInstructionsSaveRef.current);
      specialInstructionsSaveRef.current = setTimeout(() => {
        specialInstructionsSaveRef.current = null;
        void saveSpecialInstructionsDraft(value);
      }, 800);
    },
    [saveSpecialInstructionsDraft],
  );

  return { saveSpecialInstructionsDraft, scheduleSpecialInstructionsDraftSave };
}
