import React, {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useRef,
} from "react";
import { RotateCcw, X } from "lucide-react";
import { useSessionKnowledgeAttachments } from "../../frontend/src/knowledge/useSessionKnowledgeAttachments";

export interface SessionAttachmentsHandle {
  handleFileDrop: (files: File[]) => void;
  triggerFilePicker: () => void;
  getDocCount: () => number;
}

interface Props {
  threadId: string;
  disabled?: boolean;
  sessionDocsVersion: number;
  onSessionDocsChanged: () => void;
}

const ATTACHMENT_STYLES = `
.session-attachment-bar {
  display: flex;
  gap: 8px;
  padding: 8px 16px;
  overflow-x: auto;
  border-bottom: 1px solid #e5e7eb;
  background: #f9fafb;
}

.attachment-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px 4px 8px;
  border-radius: 16px;
  background: white;
  border: 1px solid #e5e7eb;
  font-size: 12px;
  white-space: nowrap;
}

.attachment-chip--uploading {
  border-color: #0f62fe;
  color: #0f62fe;
}

.attachment-chip--error {
  border-color: #da1e28;
}

.attachment-chip-name {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #374151;
}

.attachment-chip-remove {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: none;
  background: none;
  color: #9ca3af;
  cursor: pointer;
  padding: 0;
  border-radius: 50%;
}

.attachment-chip-remove:hover {
  background: #f3f4f6;
  color: #374151;
}

.attachment-chip-retry {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border: none;
  background: none;
  color: #da1e28;
  cursor: pointer;
  padding: 0;
  border-radius: 50%;
}

.attachment-chip-retry:hover {
  background: #fee2e2;
}
`;

const SessionAttachments = forwardRef<SessionAttachmentsHandle, Props>(
  function SessionAttachments(
    { threadId, disabled, sessionDocsVersion, onSessionDocsChanged },
    ref,
  ) {
    const fileInputRef = useRef<HTMLInputElement>(null);
    const {
      attachmentItems,
      documents,
      uploadFiles,
      retryUpload,
      dismissUpload,
    } = useSessionKnowledgeAttachments({
      threadId,
      sessionDocsVersion,
      onSessionDocsChanged,
    });

    useImperativeHandle(
      ref,
      () => ({
        handleFileDrop: (files: File[]) => {
          if (!disabled) {
            void uploadFiles(files);
          }
        },
        triggerFilePicker: () => {
          if (!disabled) {
            fileInputRef.current?.click();
          }
        },
        getDocCount: () => documents.length,
      }),
      [disabled, documents.length, uploadFiles],
    );

    const handleFileSelect = useCallback(
      (event: React.ChangeEvent<HTMLInputElement>) => {
        const files = event.target.files;
        if (files?.length) {
          void uploadFiles(Array.from(files));
        }
        if (fileInputRef.current) {
          fileInputRef.current.value = "";
        }
      },
      [uploadFiles],
    );

    const transientItems = attachmentItems.filter((item) => item.kind === "upload");

    return (
      <>
        <style>{ATTACHMENT_STYLES}</style>

        {transientItems.length > 0 && (
          <div className="session-attachment-bar">
            {transientItems.map((item) => (
              <div
                key={item.id}
                className={`attachment-chip attachment-chip--${item.status}`}
              >
                <span className="attachment-chip-name">{item.displayName}</span>
                {item.status === "error" && (
                  <button
                    className="attachment-chip-retry"
                    onClick={() => void retryUpload(item.id)}
                    title="Retry upload"
                    type="button"
                  >
                    <RotateCcw size={12} />
                  </button>
                )}
                <button
                  className="attachment-chip-remove"
                  onClick={() => dismissUpload(item.id)}
                  title="Remove upload"
                  type="button"
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
        )}

        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.xlsx,.pptx,.txt,.md,.html,.csv,.json,.xml"
          style={{ display: "none" }}
          onChange={handleFileSelect}
        />
      </>
    );
  },
);

export default SessionAttachments;
