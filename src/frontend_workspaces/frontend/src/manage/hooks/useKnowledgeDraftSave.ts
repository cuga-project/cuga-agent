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
  knowledgeReindexing: boolean;
  knowledgeSaveRetryRef: MutableRefObject<number>;
  knowledgeSaveRetryTimerRef: MutableRefObject<ReturnType<typeof setTimeout> | null>;
  knowledgeSaveRetryNonce: number;
  setKnowledgeSaveRetryNonce: (updater: (n: number) => number) => void;
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
  setKnowledgeSavedSnapshot: (v: unknown) => void;
  setKnowledgeDocCount: (v: number) => void;
}) {
  const {
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
  } = opts;

  const [draftSaveStatus, setDraftSaveStatus] = useState<DraftSaveStatus>({ kind: "idle" });
  const [saveAttempt, setSaveAttempt] = useState(0);
  const knowledgeAbortRef = useRef<AbortController | null>(null);

  const isSavingFamily =
    draftSaveStatus.kind === "saving" || draftSaveStatus.kind === "saving-slow";

  useEffect(() => {
    setDraftSaveStatus({ kind: "idle" });
  }, [effectiveAgentId]);

  useEffect(() => {
    return () => {
      knowledgeAbortRef.current?.abort();
      if (knowledgeSaveRetryTimerRef.current) clearTimeout(knowledgeSaveRetryTimerRef.current);
    };
  }, [knowledgeSaveRetryTimerRef]);

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
  }, [isSavingFamily, saveAttempt]);

  useEffect(() => {
    if (skipDraftSaveRef.current) return;

    if (knowledgeReindexing) {
      knowledgeAbortRef.current?.abort();
      return;
    }

    knowledgeAbortRef.current?.abort();
    const ac = new AbortController();
    knowledgeAbortRef.current = ac;

    const debounceMs = forceImmediateSaveRef.current ? 0 : 800;
    forceImmediateSaveRef.current = false;

    const t = setTimeout(async () => {
      if (knowledgeAbortRef.current !== ac) return;
      setDraftSaveStatus({ kind: "saving" });
      setSaveAttempt((n) => n + 1);
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
          knowledgeSaveRetryRef.current = 0;
          try {
            const body = await res.clone().json();
            if (ac.signal.aborted) return;
            setDraftSaveStatus({ kind: "saved" });
            const _lc = body?.live_changes;
            if (_lc?.adopted_existing_collection) {
              setKnowledgeSavedSnapshot({ ...(knowledgeConfig as object) });
              if (typeof _lc.active_document_count === "number") {
                setKnowledgeDocCount(_lc.active_document_count);
              }
            }
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

          if (detail?.error === "reindex_in_progress") {
            setDraftSaveStatus({ kind: "saving" });
            const MAX_REINDEX_SAVE_RETRIES = 20;
            if (knowledgeSaveRetryRef.current < MAX_REINDEX_SAVE_RETRIES) {
              knowledgeSaveRetryRef.current += 1;
              if (knowledgeSaveRetryTimerRef.current) clearTimeout(knowledgeSaveRetryTimerRef.current);
              knowledgeSaveRetryTimerRef.current = setTimeout(
                () => setKnowledgeSaveRetryNonce((n) => n + 1),
                3000,
              );
            } else {
              setDraftSaveStatus({
                kind: "failed",
                error: "Re-index still running — click Retry once it finishes.",
              });
            }
            return;
          }

          setDraftSaveStatus({
            kind: "failed",
            error: "Save conflicts with current server state. Try again.",
          });
          addToast(
            "warning",
            "Can't change settings yet",
            "A re-index or other config update was in progress. Your change will save on the next attempt.",
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
          console.error(`[useKnowledgeDraftSave] knowledge PATCH failed: ${res.status}`, detail);
          setDraftSaveStatus({
            kind: "failed",
            error: detail ? `Save failed (${res.status}): ${detail}` : `Save failed (${res.status})`,
          });
        }
      } catch (err) {
        if (isAbortError(err)) return;
        console.error("[useKnowledgeDraftSave] knowledge PATCH threw:", err);
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
    knowledgeReindexing,
    knowledgeSaveRetryNonce,
    addToast,
    skipDraftSaveRef,
    forceImmediateSaveRef,
    knowledgeSaveRetryRef,
    knowledgeSaveRetryTimerRef,
    setKnowledgeSaveRetryNonce,
    setCurrentVersion,
    setAdaptationServerError,
    setAutoReindexTrigger,
    setKnowledgeSavedSnapshot,
    setKnowledgeDocCount,
  ]);

  return { draftSaveStatus, setDraftSaveStatus };
}
