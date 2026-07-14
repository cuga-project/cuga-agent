import { useCallback, useEffect, useRef } from "react";
import * as api from "../../api";
import { isAbortError, type AddToast } from "./saveHelpers";

export function useAgentDraftSave(opts: {
  agentName: string;
  agentDescription: string;
  effectiveAgentId: string | undefined;
  addToast: AddToast;
  setDraftSaving: (v: boolean) => void;
  setCurrentVersion: (v: number | "draft" | null) => void;
}) {
  const { agentName, agentDescription, effectiveAgentId, addToast, setDraftSaving, setCurrentVersion } = opts;
  const agentAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      agentAbortRef.current?.abort();
    };
  }, []);

  const saveAgentDraft = useCallback(async () => {
    setDraftSaving(true);
    agentAbortRef.current?.abort();
    const ac = new AbortController();
    agentAbortRef.current = ac;
    try {
      const res = await api.patchManageConfigDraftAgent(
        { name: agentName.trim(), description: agentDescription.trim() || undefined },
        effectiveAgentId,
        ac.signal,
      );
      if (ac.signal.aborted) return;
      setDraftSaving(false);
      if (res.ok) {
        setCurrentVersion("draft");
        addToast("success", "Draft saved", "Agent settings saved to draft");
      } else {
        addToast("error", "Draft Save Failed", `Failed to save agent (${res.status} ${res.statusText})`);
      }
    } catch (error) {
      if (isAbortError(error)) return;
      setDraftSaving(false);
      addToast("error", "Draft Save Failed", error instanceof Error ? error.message : "Network error");
    }
  }, [agentName, agentDescription, addToast, effectiveAgentId, setCurrentVersion, setDraftSaving]);

  return { saveAgentDraft };
}
