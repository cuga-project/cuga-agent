import { useEffect, useRef, useState, type MutableRefObject } from "react";
import * as api from "../../api";
import { isAbortError, type AddToast } from "./saveHelpers";

export type DraftSaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saving-slow" }
  | { kind: "saved" }
  | { kind: "failed"; error: string };

export interface AdaptationServerErrorShape {
  error:
    | "length_exceeded"
    | "bidi_override"
    | "control_char"
    | "contract_override_phrase"
    | "type_error"
    | "null_byte";
  message: string;
  phrase?: string;
  pattern?: string;
  codepoint?: string;
  length?: number;
  max?: number;
}

export function useKnowledgeDraftSave(opts: {
  knowledgeConfig: unknown;
  effectiveAgentId: string | undefined;
  addToast: AddToast;
  skipDraftSaveRef: MutableRefObject<boolean>;
  forceImmediateSaveRef: MutableRefObject<boolean>;
  setCurrentVersion: (v: number | "draft" | null) => void;
  setAdaptationServerError: (v: AdaptationServerErrorShape | null) => void;
  setAutoReindexTrigger: (
    updater:
      | { taskIds: string[]; total: number; triggerKey: string }
      | null
      | ((
          prev: { taskIds: string[]; total: number; triggerKey: string } | null,
        ) => { taskIds: string[]; total: number; triggerKey: string } | null),
  ) => void;
}) {
  const {
    knowledgeConfig,
    effectiveAgentId,
    addToast,
    skipDraftSaveRef,
    forceImmediateSaveRef,
    setCurrentVersion,
    setAdaptationServerError,
    setAutoReindexTrigger,
  } = opts;

  const [draftSaveStatus, setDraftSaveStatus] = useState<DraftSaveStatus>({ kind: "idle" });
  const knowledgeAbortRef = useRef<AbortController | null>(null);

  const isSavingFamily =
    draftSaveStatus.kind === "saving" || draftSaveStatus.kind === "saving-slow";

  useEffect(() => {
    return () => {
      knowledgeAbortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (!isSavingFamily) return;
    const slow = setTimeout(() => {
      setDraftSaveStatus((prev) =>
        prev.kind === "saving" ? { kind: "saving-slow" } : prev,
      );
    }, 25_000);
    const fail = setTimeout(() => {
      knowledgeAbortRef.current?.abort();
      setDraftSaveStatus((prev) =>
        prev.kind === "saving" || prev.kind === "saving-slow"
          ? {
              kind: "failed",
              error: "Save took too long — server may be busy. Try again.",
            }
          : prev,
      );
    }, 90_000);
    return () => {
      clearTimeout(slow);
      clearTimeout(fail);
    };
  }, [isSavingFamily]);

  useEffect(() => {
    if (skipDraftSaveRef.current) return;

    knowledgeAbortRef.current?.abort();
    const ac = new AbortController();
    knowledgeAbortRef.current = ac;

    const debounceMs = forceImmediateSaveRef.current ? 0 : 800;
    forceImmediateSaveRef.current = false;

    const t = setTimeout(async () => {
      if (knowledgeAbortRef.current !== ac) return;
      setDraftSaveStatus({ kind: "saving" });
      try {
        const res = await api.patchManageConfigDraftKnowledge(
          knowledgeConfig,
          effectiveAgentId,
          ac.signal,
        );
        if (ac.signal.aborted) return;
        if (res.ok) {
          setCurrentVersion("draft");
          setAdaptationServerError(null);
          try {
            const body = await res.clone().json();
            if (ac.signal.aborted) return;
            setDraftSaveStatus({ kind: "saved" });
            const collections = body?.auto_reindex?.collections ?? [];
            const taskIds: string[] = collections
              .flatMap((c: { result?: { task_ids?: string[] } }) => c?.result?.task_ids ?? [])
              .filter((id: string) => typeof id === "string" && id.length > 0);
            if (taskIds.length > 0) {
              const total = collections.reduce(
                (sum: number, c: { result?: { count?: number } }) => sum + (c?.result?.count ?? 0),
                0,
              );
              const triggerKey = taskIds.slice().sort().join("|");
              setAutoReindexTrigger((prev) =>
                prev?.triggerKey === triggerKey
                  ? prev
                  : { taskIds, total: total || taskIds.length, triggerKey },
              );
            }
          } catch {
            setDraftSaveStatus({ kind: "saved" });
          }
        } else if (res.status === 422) {
          if (ac.signal.aborted) return;
          try {
            const body = await res.json();
            if (ac.signal.aborted) return;
            const err = (body && (body.detail ?? body)) as Partial<AdaptationServerErrorShape> | null;
            if (err && typeof err.error === "string" && typeof err.message === "string") {
              setAdaptationServerError(err as AdaptationServerErrorShape);
            }
            setDraftSaveStatus({
              kind: "failed",
              error: (err && err.message) || "Couldn't apply — see provider error below",
            });
          } catch {
            setDraftSaveStatus({ kind: "failed", error: "Save rejected by server" });
          }
        } else if (res.status === 409) {
          if (ac.signal.aborted) return;
          let detail: { error?: string; message?: string } | null = null;
          try {
            const body = await res.json();
            detail = (body && (body.detail ?? body)) as { error?: string; message?: string } | null;
          } catch {
            // 409 without a JSON body
          }
          if (ac.signal.aborted) return;
          const msg =
            detail?.error === "reindex_in_progress"
              ? detail?.message ||
                "Re-index is running. Wait for it to finish, then try again."
              : "Save conflicts with current server state. Try again.";
          setDraftSaveStatus({ kind: "failed", error: msg });
          addToast(
            "warning",
            "Can't change settings yet",
            "A Re-index is running. Wait for it to finish, then this change will save.",
          );
        } else {
          if (ac.signal.aborted) return;
          let detail = "";
          try {
            const body = await res.clone().text();
            detail = body ? body.slice(0, 200) : "";
          } catch {
            // ignore
          }
          console.error(`[ManagePage] knowledge PATCH failed: ${res.status}`, detail);
          setDraftSaveStatus({
            kind: "failed",
            error: detail ? `Save failed (${res.status}): ${detail}` : `Save failed (${res.status})`,
          });
        }
      } catch (err) {
        if (isAbortError(err)) return;
        console.error("[ManagePage] knowledge PATCH threw:", err);
        setDraftSaveStatus({
          kind: "failed",
          error: err instanceof Error ? err.message : "Couldn't save — check your connection",
        });
      }
    }, debounceMs);
    return () => {
      clearTimeout(t);
    };
  }, [
    knowledgeConfig,
    effectiveAgentId,
    addToast,
    skipDraftSaveRef,
    forceImmediateSaveRef,
    setCurrentVersion,
    setAdaptationServerError,
    setAutoReindexTrigger,
  ]);

  return { draftSaveStatus, setDraftSaveStatus };
}
