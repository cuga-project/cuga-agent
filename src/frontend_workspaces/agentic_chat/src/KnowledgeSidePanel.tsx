import React, { useState, useRef } from "react";
import { X, FileText, Trash2, Upload, Lock } from "lucide-react";
import { useSessionKnowledgeAttachments } from "../../frontend/src/knowledge/useSessionKnowledgeAttachments";
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
  threadId: string;
  sessionDocsVersion: number;
  onSessionDocsChanged: () => void;
  // Agent-level docs come from App (``useAgentKnowledgeDocs``) so the
  // badge count and the panel share one source of truth. The panel no
  // longer fetches these itself — preventing the 6→0→6 flicker that
  // happened when local state was reset on prop transitions.
  agentDocs: KnowledgeDoc[];
  inline?: boolean;
  knowledgeEnabled?: boolean | null;
  agentKnowledgeEnabled?: boolean | null;
  sessionKnowledgeEnabled?: boolean | null;
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
  agentDocs,
  inline = false,
  knowledgeEnabled = true,
  agentKnowledgeEnabled = true,
  sessionKnowledgeEnabled = true,
  agentLabel,
}: KnowledgeSidePanelProps) {
  const [dragover, setDragover] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const agentScopeEnabled = knowledgeEnabled !== false && agentKnowledgeEnabled !== false;
  const sessionScopeEnabled = knowledgeEnabled !== false && sessionKnowledgeEnabled !== false;
  const effectiveThreadId = sessionScopeEnabled ? threadId : "";
  const conversationReady = sessionScopeEnabled && Boolean(effectiveThreadId);
  const disabledAgentLabel = agentLabel || "this agent";

  const {
    documents: sessionDocs,
    isUploading: uploading,
    uploadFiles,
    deleteDocument,
  } = useSessionKnowledgeAttachments({
    threadId: effectiveThreadId,
    enabled: sessionScopeEnabled,
    sessionDocsVersion,
    onSessionDocsChanged,
  });

  // Agent-doc fetching has moved up to ``useAgentKnowledgeDocs`` (App
  // level). The panel reads ``agentDocs`` from props and no longer owns
  // its own fetch lifecycle — eliminates the 6→0→6 transition that used
  // to happen when ``agentScopeEnabled`` flipped during prop resolution.

  const handleDeleteSessionDoc = async (filename: string) => {
    if (!effectiveThreadId) return;
    try {
      await deleteDocument(filename);
    } catch (err) {
      console.error("Failed to delete session doc:", err);
    }
  };

  const handleUpload = async (files: File[]) => {
    if (!effectiveThreadId || files.length === 0) return;
    try {
      await uploadFiles(files);
    } catch (err) {
      console.error("Failed to upload session docs:", err);
    }
  };

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (conversationReady && e.dataTransfer?.types.includes("Files")) {
      setDragover(true);
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX;
    const y = e.clientY;
    if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) {
      setDragover(false);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragover(false);
    if (!conversationReady) return;
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) {
      await handleUpload(files);
    }
  };

  const handleFileInputChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!conversationReady) return;
    const files = Array.from(e.target.files ?? []);
    if (files.length > 0) {
      await handleUpload(files);
    }
    // Reset input so the same file can be selected again
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  return (
    <div className={`knowledge-panel ${inline ? "inline" : ""} ${isOpen ? "open" : "closed"}`}>
      <div className="knowledge-panel-header">
        <div className="knowledge-panel-title">
          <FileText size={18} />
          <span>Knowledge</span>
        </div>
        {!inline && (
          <button className="knowledge-close-btn" onClick={onToggle} title="Close">
            <X size={18} />
          </button>
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
            {agentDocs.length === 0 ? (
              <div className="knowledge-empty">No agent documents</div>
            ) : (
              agentDocs.map((doc) => (
                <div className="knowledge-doc-row knowledge-doc-row--agent" key={doc.filename}>
                  <div className="knowledge-doc-icon">
                    <FileText size={16} />
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
              <Lock size={12} />
              <span>Managed in Settings</span>
            </div>
          </div>
        )}

        {sessionScopeEnabled && (
          <div className="knowledge-section">
            <div className="knowledge-section-title">This Conversation</div>
            <div className="knowledge-section-subtitle">Only available in this chat session</div>
            {sessionDocs.length === 0 ? (
              <div className="knowledge-empty">No session documents</div>
            ) : (
              sessionDocs.map((doc) => (
                <div className="knowledge-doc-row" key={doc.knowledge_filename}>
                  <div className="knowledge-doc-icon">
                    <FileText size={16} />
                  </div>
                  <div className="knowledge-doc-info">
                    <span className="knowledge-doc-filename">{doc.display_name}</span>
                    <span className="knowledge-doc-meta">
                      {(doc.chunk_count ?? 0)} chunks &middot; {doc.ingested_at ? relativeTime(doc.ingested_at) : "Just now"}
                    </span>
                  </div>
                  <button
                    className="knowledge-doc-delete"
                    onClick={() => handleDeleteSessionDoc(doc.knowledge_filename)}
                    title="Remove document"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))
            )}

            <button
              type="button"
              className={`knowledge-drop-zone ${dragover ? "dragover" : ""} ${conversationReady ? "" : "disabled"}`}
              onClick={() => {
                if (!uploading && conversationReady) {
                  fileInputRef.current?.click();
                }
              }}
              onDragEnter={handleDragEnter}
              onDragLeave={handleDragLeave}
              onDragOver={handleDragOver}
              onDrop={handleDrop}
              disabled={uploading || !conversationReady}
              title={conversationReady ? "Add documents to this conversation" : "Session unavailable"}
            >
              <Upload size={20} />
              <span>
                {uploading
                  ? "Uploading..."
                  : conversationReady
                    ? "Drop files here or click to add"
                    : "Session files are temporarily unavailable"}
              </span>
            </button>

            <input
              ref={fileInputRef}
              type="file"
              multiple
              style={{ display: "none" }}
              onChange={handleFileInputChange}
            />
          </div>
        )}
          </>
        )}
      </div>
    </div>
  );
}
