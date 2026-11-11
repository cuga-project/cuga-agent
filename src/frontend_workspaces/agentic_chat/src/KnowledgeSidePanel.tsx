import { ContentSkeleton } from "./LoadingState";
import React, { useState, useEffect, useRef } from "react";
import { Close, Document, TrashCan, Locked } from "@carbon/icons-react";
import { IconButton, Toggle, FileUploaderDropContainer, InlineLoading, InlineNotification } from "@carbon/react";
import { useSessionKnowledgeAttachments } from "../../frontend/src/knowledge/useSessionKnowledgeAttachments";
import * as api from "../../frontend/src/api";
import "./KnowledgeSidePanel.css";

interface KnowledgeDoc {
  filename: string;
  chunk_count: number;
  status: string;
  ingested_at: string;
}

interface KnowledgeSidePanelProps {
  isOpen: boolean;
  onToggle: () => void;
  threadId: string | null;
  sessionDocsVersion: number;
  /** Creates the conversation on demand, so a new chat can upload session documents. */
  resolveThreadId?: () => Promise<string>;
  onSessionDocsChanged: () => void;
  // Agent-level docs: prop is OPTIONAL. The original design wanted a
  // shared App-level ``useAgentKnowledgeDocs`` hook so the badge count
  // and the panel had one source of truth (avoids the 6→0→6 flicker on
  // enabled-flag transitions). That hook was never built, and both
  // current callers (App.tsx, ChatLanding.tsx) stopped passing the
  // prop — leading to ``agentDocs.length`` blowing up on undefined at
  // line 178 (the "blank page" bug). Until the App-level hook lands,
  // the panel self-fetches as a fallback when ``agentDocs`` isn't
  // provided. ``onDocCountChanged`` lets the parent track the count
  // for the badge — both callers were already passing this; it just
  // needed to be on the interface.
  agentDocs?: KnowledgeDoc[];
  onDocCountChanged?: (count: number) => void;
  inline?: boolean;
  knowledgeEnabled?: boolean | null;
  agentKnowledgeEnabled?: boolean | null;
  sessionKnowledgeEnabled?: boolean | null;
  // Agent-level citations flag from agent context. When explicitly false
  // the per-session citations toggle is hidden (the master switch lives
  // in Manage → Knowledge → Settings). null/undefined = treat as on.
  citationsEnabled?: boolean | null;
  agentLabel?: string;
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60000) return "Just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function KnowledgeSidePanel({
  isOpen,
  onToggle,
  threadId,
  sessionDocsVersion,
  onSessionDocsChanged,
  resolveThreadId,
  agentDocs: agentDocsProp,
  onDocCountChanged,
  inline = false,
  knowledgeEnabled = true,
  agentKnowledgeEnabled = true,
  sessionKnowledgeEnabled = true,
  citationsEnabled = true,
  agentLabel,
}: KnowledgeSidePanelProps) {
  const agentScopeEnabled = knowledgeEnabled !== false && agentKnowledgeEnabled !== false;
  const sessionScopeEnabled = knowledgeEnabled !== false && sessionKnowledgeEnabled !== false;

  // Self-fetch fallback: if no parent provided ``agentDocs``, load
  // them from the engine ourselves so the panel actually shows
  // documents instead of crashing on ``undefined.length`` (the bug
  // the user filed: badge click → blank page). Cached locally and
  // only re-fetched when the panel opens, the agent scope toggles
  // on, or the parent bumps ``sessionDocsVersion`` to signal a
  // mutation. The pre-existing "6→0→6 flicker" risk this comment
  // mentions is avoided by NOT resetting ``localAgentDocs`` on
  // every render — only on successful fetch.
  const [localAgentDocs, setLocalAgentDocs] = useState<KnowledgeDoc[]>([]);
  const [agentDocsLoading, setAgentDocsLoading] = useState(agentDocsProp === undefined);
  const [agentDocsError, setAgentDocsError] = useState("");
  useEffect(() => {
    if (agentDocsProp !== undefined) return; // parent owns the data
    if (!agentScopeEnabled || !isOpen) return;
    let cancelled = false;
    setAgentDocsLoading(true);
    setAgentDocsError("");
    (async () => {
      try {
        const res = await api.listKnowledgeDocuments();
        if (cancelled) return;
        if (!res.ok) throw new Error(`Failed to load documents (${res.status})`);
        const body = await res.json();
        if (cancelled) return;
        const docs = (body?.documents ?? []) as KnowledgeDoc[];
        setLocalAgentDocs(docs);
      } catch (error) {
        if (!cancelled) setAgentDocsError(error instanceof Error ? error.message : "Failed to load documents");
        // Network/decode failure — keep the last-known docs to avoid
        // the 6→0→6 flicker the prior commit was trying to prevent.
      } finally {
        if (!cancelled) setAgentDocsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentDocsProp, agentScopeEnabled, isOpen, sessionDocsVersion]);

  const agentDocs: KnowledgeDoc[] = agentDocsProp ?? localAgentDocs;
  const effectiveThreadId = sessionScopeEnabled ? threadId ?? "" : "";
  const conversationReady = sessionScopeEnabled && Boolean(effectiveThreadId);
  // A new chat has no ID yet; uploading creates the conversation.
  const uploadReady = conversationReady || (sessionScopeEnabled && Boolean(resolveThreadId));
  const disabledAgentLabel = agentLabel || "this agent";

  // Per-session citations override. ``null`` = no override stored, follow
  // the agent-level default (on). Loaded from the session settings API on
  // mount / thread change; written back optimistically on toggle.
  const [citationsSaving, setCitationsSaving] = useState(false);
  const citationsRequestRef = useRef(0);
  const [sessionCitations, setSessionCitations] = useState<boolean | null>(null);
  useEffect(() => {
    const request = ++citationsRequestRef.current;
    setCitationsSaving(false);
    setSessionCitations(null); // reset when switching conversations
    if (!threadId) return;
    let cancelled = false;
    api
      .getSessionKnowledgeSettings(threadId)
      .then((r) => {
        if (cancelled || request !== citationsRequestRef.current) return;
        const v = r.overrides?.citations_enabled;
        setSessionCitations(typeof v === "boolean" ? v : null);
      })
      .catch(() => {
        // No stored override (or transient failure) — follow agent default.
      });
    return () => {
      cancelled = true;
    };
  }, [threadId]);

  const handleSessionCitationsChange = async (next: boolean) => {
    // Mirror the toggle's own disabled condition (sessionScopeEnabled + a real
    // thread) — a bare !threadId guard lets a programmatic dispatch write a
    // phantom session override while the session scope is off.
    if (!conversationReady) return;
    if (citationsSaving) return;
    const request = ++citationsRequestRef.current;
    setCitationsSaving(true);
    const prev = sessionCitations;
    setSessionCitations(next); // optimistic
    try {
      await api.patchSessionKnowledgeSettings(effectiveThreadId, { citations_enabled: next });
    } catch (err) {
      console.error("Failed to update session citations setting:", err);
      if (request === citationsRequestRef.current) setSessionCitations(prev); // revert only in the same conversation
    } finally { if (request === citationsRequestRef.current) setCitationsSaving(false); }
  };

  const {
    documents: sessionDocs,
    isUploading: uploading,
    isLoading: sessionDocsLoading,
    isDeleting: deleting,
    loadError: sessionDocsError,
    uploadFiles,
    deleteDocument,
  } = useSessionKnowledgeAttachments({
    threadId: effectiveThreadId,
    enabled: sessionScopeEnabled,
    sessionDocsVersion,
    onSessionDocsChanged,
    resolveThreadId,
  });

  // Notify the parent of total doc count changes so the section badge
  // (which the user clicks to open this panel) reflects what we
  // actually show. Without this, a parent that relies solely on the
  // ``onDocCountChanged`` callback for its badge count would never
  // see updates when the panel self-fetches its own agent docs.
  // sessionDocs comes from ``useSessionKnowledgeAttachments`` above.
  useEffect(() => {
    onDocCountChanged?.(agentDocs.length + sessionDocs.length);
  }, [agentDocs.length, sessionDocs.length, onDocCountChanged]);

  const handleDeleteSessionDoc = async (filename: string) => {
    if (!effectiveThreadId) return;
    try {
      await deleteDocument(filename);
    } catch (err) {
      console.error("Failed to delete session doc:", err);
    }
  };

  const handleUpload = async (files: File[]) => {
    if (!uploadReady || files.length === 0) return;
    try {
      await uploadFiles(files);
    } catch (err) {
      console.error("Failed to upload session docs:", err);
    }
  };


  return (
    <div className={`knowledge-panel ${inline ? "inline" : ""} ${isOpen ? "open" : "closed"}`}>
      <div className="knowledge-panel-header">
        <div className="knowledge-panel-title">
          <Document size={18} />
          <span>Knowledge</span>
        </div>
        {!inline && (
          <IconButton kind="ghost" size="sm" label="Close Knowledge" onClick={onToggle}>
            <Close />
          </IconButton>
        )}
      </div>

      <div className="knowledge-panel-content">
        {knowledgeEnabled === false ? (
          <div className="knowledge-unavailable">
            <div className="knowledge-unavailable-eyebrow">Unavailable</div>
            <h3 className="knowledge-unavailable-title">Knowledge isn&apos;t available for {disabledAgentLabel}.</h3>
            <p className="knowledge-unavailable-copy">
              To turn it back on or update documents, open Manage and change the knowledge settings there.
            </p>
          </div>
        ) : !agentScopeEnabled && !sessionScopeEnabled ? (
          <div className="knowledge-unavailable">
            <div className="knowledge-unavailable-eyebrow">Unavailable</div>
            <h3 className="knowledge-unavailable-title">Knowledge scopes are turned off for {disabledAgentLabel}.</h3>
            <p className="knowledge-unavailable-copy">
              Re-enable agent-level or session-level knowledge in Manage to make documents available here again.
            </p>
          </div>
        ) : (
          <>
        {agentScopeEnabled && (
          <div className="knowledge-section">
            <div className="knowledge-section-title">Agent Knowledge</div>
            {agentDocsProp === undefined && agentDocsLoading && agentDocs.length === 0 ? <ContentSkeleton label="Loading agent documents…" rows={2} /> : agentDocsError ? <InlineNotification kind="error" title={agentDocsError} lowContrast hideCloseButton /> : agentDocs.length === 0 ? (
              <div className="knowledge-empty">No agent documents</div>
            ) : (
              agentDocs.map((doc) => (
                <div className="knowledge-doc-row knowledge-doc-row--agent" key={doc.filename}>
                  <div className="knowledge-doc-icon">
                    <Document size={16} />
                  </div>
                  <div className="knowledge-doc-info">
                    <span className="knowledge-doc-filename">{doc.filename}</span>
                    <span className="knowledge-doc-meta">
                      {doc.chunk_count} chunks &middot; {relativeTime(doc.ingested_at)}
                    </span>
                  </div>
                </div>
              ))
            )}
            <div className="knowledge-agent-hint">
              <Locked size={12} />
              <span>Managed in Settings</span>
            </div>
          </div>
        )}

        {sessionScopeEnabled && (
          <div className="knowledge-section">
            <div className="knowledge-section-title">This Conversation</div>
            <div className="knowledge-section-subtitle">Only available in this chat session</div>
            {citationsEnabled !== false && (
              <div className="knowledge-session-citations">
                <div className="knowledge-session-citations-info">
                  <span className="knowledge-session-citations-label">Citations for this chat</span>
                  <span className="knowledge-session-citations-help">Show numbered sources under answers</span>
                </div>
                <Toggle id="session-citations-toggle" size="sm" labelText="" aria-label="Citations for this chat"
                  hideLabel labelA="Off" labelB="On" toggled={sessionCitations ?? true}
                  onToggle={handleSessionCitationsChange} disabled={!conversationReady || citationsSaving} />
              </div>
            )}
            {citationsSaving && <InlineLoading description="Saving citation settings…" aria-live="polite" />}
            {deleting && <InlineLoading description="Removing document…" aria-live="polite" />}
            {sessionDocsLoading && sessionDocs.length === 0 ? <ContentSkeleton label="Loading conversation documents…" rows={2} /> : sessionDocsError ? <InlineNotification kind="error" title={sessionDocsError} lowContrast hideCloseButton /> : sessionDocs.length === 0 ? (
              <div className="knowledge-empty">No session documents</div>
            ) : (
              sessionDocs.map((doc) => (
                <div className="knowledge-doc-row" key={doc.knowledge_filename}>
                  <div className="knowledge-doc-icon">
                    <Document size={16} />
                  </div>
                  <div className="knowledge-doc-info">
                    <span className="knowledge-doc-filename">{doc.display_name}</span>
                    <span className="knowledge-doc-meta">
                      {(doc.chunk_count ?? 0)} chunks &middot; {doc.ingested_at ? relativeTime(doc.ingested_at) : "Just now"}
                    </span>
                  </div>
                  <IconButton kind="ghost" size="sm" label={`Remove document ${doc.display_name}`}
                    disabled={deleting} onClick={() => handleDeleteSessionDoc(doc.knowledge_filename)}><TrashCan /></IconButton>
                </div>
              ))
            )}

            <FileUploaderDropContainer
              labelText={uploadReady ? "Drag and drop files here or click to upload" : "Session files are temporarily unavailable"}
              multiple disabled={uploading || !uploadReady}
              onAddFiles={(_event, { addedFiles }) => { void handleUpload(addedFiles); }}
            />
            {uploading && <InlineLoading description="Uploading..." />}

          </div>
        )}
          </>
        )}
      </div>
    </div>
  );
}
