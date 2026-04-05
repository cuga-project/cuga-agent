/*
 *  Copyright IBM Corp. 2025
 *
 *  This source code is licensed under the Apache-2.0 license found in the
 *  LICENSE file in the root directory of this source tree.
 *
 *  @license
 */
import React, { useCallback, useRef, useEffect, useState } from 'react';
import {
  ChatCustomElement,
  type ChatInstance,
  type MessageRequest,
  type CustomSendMessageOptions,
  CarbonTheme,
  BusEventType,
} from '@carbon/ai-chat';
import { FileText, Loader2, Paperclip, RotateCcw, X } from "lucide-react";
import * as api from '../api';
import {
  useSessionKnowledgeAttachments,
  type KnowledgeAttachmentScope,
  type SessionAttachmentItem,
  type KnowledgeAttachmentSnapshot,
} from "../knowledge/useSessionKnowledgeAttachments";
import { customSendMessage as customSendMessageImpl, stopCugaAgent } from './customSendMessage';
import { customLoadHistory } from './customLoadHistory';
import { initAgentProfile, getResponseUserProfile } from './carbonChatHelpers';
import './CarbonChat.css';

// Reset thread ID when conversation restarts
export function generateUUID(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

let currentThreadId: string | null = null;

function resetThreadId() {
  currentThreadId = null;
}

export function getOrCreateThreadId(): string {
  if (!currentThreadId) {
    currentThreadId = generateUUID();
  }
  return currentThreadId;
}

const DEFAULT_HOMESCREEN = {
  isOn: true,
  greeting: 'Hello, how can I help you today?',
  starters: ['Hi, what can you do for me?'],
};

interface HomescreenConfig {
  isOn?: boolean;
  greeting?: string;
  starters?: string[];
}

interface CarbonChatProps {
  className?: string;
  theme?: 'light' | 'dark';
  contained?: boolean;
  useDraft?: boolean;
  threadId?: string | null;
  attachmentScope?: "none" | KnowledgeAttachmentScope;
  knowledgeEnabled?: boolean | null;
  agentKnowledgeEnabled?: boolean | null;
  sessionKnowledgeEnabled?: boolean | null;
  disableHistory?: boolean;
  isReadonly?: boolean;
  homescreen?: HomescreenConfig;
  onThreadChange?: (threadId: string) => void;
  sessionDocsVersion?: number;
  onSessionDocsChanged?: () => void;
  onOpenKnowledge?: () => void;
  onPreviewKnowledgeAttachment?: (attachment: KnowledgeAttachmentSnapshot) => void;
}

interface ConversationMessage {
  role: string;
  content: string;
  timestamp: string;
  metadata?: {
    attachments?: KnowledgeAttachmentSnapshot[];
  };
}

function ComposerToolbar({
  items,
  onDeleteDocument,
  onRetryUpload,
  onDismissUpload,
  onPreviewAttachment,
  onOpenKnowledge,
  onAttachClick,
  attachmentScope,
}: {
  items: SessionAttachmentItem[];
  onDeleteDocument: (knowledgeFilename: string) => void;
  onRetryUpload: (uploadId: string) => void;
  onDismissUpload: (uploadId: string) => void;
  onPreviewAttachment?: (attachment: KnowledgeAttachmentSnapshot) => void;
  onOpenKnowledge?: () => void;
  onAttachClick?: () => void;
  attachmentScope: KnowledgeAttachmentScope;
}) {
  const hasItems = items.length > 0;

  if (!onAttachClick && !hasItems) {
    return null;
  }

  return (
    <div className={`cuga-composer-toolbar${hasItems ? ' cuga-composer-toolbar--has-items' : ''}`}>
      <div className="cuga-composer-toolbar__row">
        {onAttachClick && (
          <button
            type="button"
            className="cuga-composer-toolbar__attach"
            onClick={onAttachClick}
            aria-label="Attach files"
            title="Attach files"
          >
            <Paperclip size={16} />
          </button>
        )}

        {hasItems && (
          <>
            <div className="cuga-composer-toolbar__divider" />
            <div className="cuga-composer-toolbar__chips">
              {items.map((item) => {
                const previewSnapshot =
                  item.kind === "document" && item.knowledgeFilename
                    ? {
                        knowledge_filename: item.knowledgeFilename,
                        display_name: item.displayName,
                        mime_type: item.mimeType,
                        size_bytes: item.sizeBytes,
                        scope: attachmentScope,
                      }
                    : null;

                return (
                  <div
                    key={item.id}
                    className={`cuga-composer-chip cuga-composer-chip--${item.status}`}
                  >
                    <button
                      type="button"
                      className="cuga-composer-chip__main"
                      onClick={() => {
                        if (previewSnapshot && onPreviewAttachment) {
                          onPreviewAttachment(previewSnapshot);
                        }
                      }}
                      disabled={!previewSnapshot || !onPreviewAttachment}
                    >
                      {item.status === "uploading" ? (
                        <Loader2 size={13} className="cuga-composer-chip__spinner" />
                      ) : (
                        <FileText size={13} />
                      )}
                      <span className="cuga-composer-chip__name">{item.displayName}</span>
                    </button>
                    {item.kind === "upload" && item.status === "error" && (
                      <button
                        type="button"
                        className="cuga-composer-chip__action"
                        onClick={() => onRetryUpload(item.id)}
                        title="Retry upload"
                      >
                        <RotateCcw size={11} />
                      </button>
                    )}
                    <button
                      type="button"
                      className="cuga-composer-chip__action"
                      onClick={() => {
                        if (item.kind === "document" && item.knowledgeFilename) {
                          onDeleteDocument(item.knowledgeFilename);
                        } else {
                          onDismissUpload(item.id);
                        }
                      }}
                      title={item.kind === "document" ? "Remove file" : "Dismiss"}
                    >
                      <X size={11} />
                    </button>
                  </div>
                );
              })}
            </div>
            {onOpenKnowledge && (
              <button
                type="button"
                className="cuga-composer-toolbar__manage"
                onClick={onOpenKnowledge}
              >
                Manage
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

const CarbonChat = ({
  className = '',
  theme = 'light',
  contained = false,
  useDraft = false,
  threadId = null,
  attachmentScope = "none",
  knowledgeEnabled = true,
  agentKnowledgeEnabled = true,
  sessionKnowledgeEnabled = true,
  disableHistory = false,
  isReadonly = false,
  homescreen,
  onThreadChange,
  sessionDocsVersion = 0,
  onSessionDocsChanged,
  onOpenKnowledge,
  onPreviewKnowledgeAttachment,
}: CarbonChatProps) => {
  const hs = homescreen ?? DEFAULT_HOMESCREEN;
  const starterLabels = (hs.starters ?? DEFAULT_HOMESCREEN.starters ?? []).filter(Boolean).slice(0, 4);
  const chatInstanceRef = useRef<ChatInstance | null>(null);
  const chatElementRef = useRef<HTMLElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const skipNextHistoryLoadRef = useRef<string | null>(null);
  const [showDebugPanel, setShowDebugPanel] = useState(false);
  const [debugData, setDebugData] = useState<any>(null);
  const [isLoadingDebug, setIsLoadingDebug] = useState(false);
  const [debugError, setDebugError] = useState<string | null>(null);
  const [lastUpdateTime, setLastUpdateTime] = useState<Date | null>(null);
  const [messageAttachmentSnapshots, setMessageAttachmentSnapshots] = useState<KnowledgeAttachmentSnapshot[][]>([]);
  const [chatRenderTick, setChatRenderTick] = useState(0);
  const [isDragOver, setIsDragOver] = useState(false);
  const dragCounterRef = useRef(0);
  const effectiveAttachmentScope: KnowledgeAttachmentScope = attachmentScope === "agent" ? "agent" : "session";
  const scopeEnabled =
    attachmentScope === "agent"
      ? agentKnowledgeEnabled !== false
      : attachmentScope === "session"
        ? sessionKnowledgeEnabled !== false
        : false;
  const {
    attachmentItems,
    isAvailable: attachmentsAvailable,
    uploadFiles,
    retryUpload,
    dismissUpload,
    deleteDocument,
    createMessageAttachmentSnapshot,
  } = useSessionKnowledgeAttachments({
    threadId,
    scope: effectiveAttachmentScope,
    enabled: scopeEnabled,
    sessionDocsVersion,
    onSessionDocsChanged,
    visibleDocumentMode: attachmentScope === "agent" ? "tracked" : "all",
  });
  const attachmentsEnabled = knowledgeEnabled !== false && attachmentScope !== "none" && scopeEnabled && attachmentsAvailable;
  const [assistantName, setAssistantName] = useState("CUGA Agent");

  useEffect(() => {
    initAgentProfile(useDraft);
    getResponseUserProfile(useDraft).then((p) => setAssistantName(p.nickname || "CUGA Agent"));
  }, [useDraft]);

  // Keep the transport thread ID aligned with the parent-owned draft thread
  // even before the chat instance finishes booting.
  useEffect(() => {
    currentThreadId = threadId ?? null;
  }, [threadId]);

  const resolveChatDomRoots = useCallback(() => {
    const directCandidates = [
      chatElementRef.current,
      document.querySelector("cds-custom-aichat-react"),
      document.querySelector("cds-custom-aichat-custom-element"),
      document.querySelector("cds-aichat-react"),
      document.querySelector("cds-aichat-custom-element"),
    ].filter(Boolean) as Array<HTMLElement & { shadowRoot?: ShadowRoot | null }>;

    const roots: ShadowRoot[] = [];
    for (const candidate of directCandidates) {
      const candidateShadow = candidate.shadowRoot;
      if (candidateShadow && !roots.includes(candidateShadow)) {
        roots.push(candidateShadow);
      }

      const nestedContainers = candidateShadow
        ? Array.from(
            candidateShadow.querySelectorAll(
              "cds-custom-aichat-container, cds-aichat-container",
            ),
          ) as Array<HTMLElement & { shadowRoot?: ShadowRoot | null }>
        : [];

      for (const nestedContainer of nestedContainers) {
        if (nestedContainer.shadowRoot && !roots.includes(nestedContainer.shadowRoot)) {
          roots.push(nestedContainer.shadowRoot);
        }
      }
    }

    return roots;
  }, []);

  const refreshMessageAttachmentSnapshots = useCallback(
    async (targetThreadId?: string | null) => {
      if (attachmentScope !== "session" || !targetThreadId || disableHistory) {
        setMessageAttachmentSnapshots([]);
        return;
      }

      try {
        const response = await api.getConversationMessages(targetThreadId);
        if (!response.ok) {
          setMessageAttachmentSnapshots([]);
          return;
        }

        const data = await response.json();
        const messages = (data.messages ?? []) as ConversationMessage[];
        const userMessageAttachments = messages
          .filter((message) => message.role === "user" || message.role === "human")
          .map((message) => message.metadata?.attachments ?? []);
        setMessageAttachmentSnapshots(userMessageAttachments);
      } catch (error) {
        console.error("Failed to refresh message attachment snapshots:", error);
      }
    },
    [attachmentScope, disableHistory],
  );

  useEffect(() => {
    void refreshMessageAttachmentSnapshots(threadId);
  }, [refreshMessageAttachmentSnapshots, threadId]);

  // Format relative time (e.g., "2 seconds ago", "5 minutes ago")
  const formatRelativeTime = useCallback((date: Date) => {
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSeconds = Math.floor(diffMs / 1000);
    const diffMinutes = Math.floor(diffSeconds / 60);
    const diffHours = Math.floor(diffMinutes / 60);

    if (diffSeconds < 60) {
      return `${diffSeconds} second${diffSeconds !== 1 ? 's' : ''} ago`;
    } else if (diffMinutes < 60) {
      return `${diffMinutes} minute${diffMinutes !== 1 ? 's' : ''} ago`;
    } else {
      return `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`;
    }
  }, []);

  // Fetch debug data from /api/agent/state
  const fetchDebugData = useCallback(async () => {
    setIsLoadingDebug(true);
    setDebugError(null);
    try {
      const activeThreadId = currentThreadId || getOrCreateThreadId();
      const response = await api.getAgentState(activeThreadId);
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      setDebugData(data);
      setLastUpdateTime(new Date());
    } catch (error) {
      console.error('Error fetching debug data:', error);
      setDebugError(error instanceof Error ? error.message : 'Unknown error');
    } finally {
      setIsLoadingDebug(false);
    }
  }, []);

  // Auto-refresh debug data when panel is open
  useEffect(() => {
    if (showDebugPanel) {
      fetchDebugData();
      const interval = setInterval(fetchDebugData, 3000); // Refresh every 3 seconds
      return () => clearInterval(interval);
    }
  }, [showDebugPanel, fetchDebugData]);

  useEffect(() => {
    const roots = resolveChatDomRoots();

    if (roots.length === 0) {
      return;
    }

    const applyMessageAttachmentDecorations = () => {
      roots.forEach((shadowRoot) => {
        const requestNodes = Array.from(
          shadowRoot.querySelectorAll(".cds-custom-aichat--message--request"),
        ) as HTMLElement[];

        requestNodes.forEach((requestNode, index) => {
          const target = (requestNode.querySelector(".cds-custom-aichat--sent--text") ??
            requestNode.querySelector(".cds-custom-aichat--message--padding")) as HTMLElement | null;
          if (!target) {
            return;
          }

          target
            .querySelectorAll(".cuga-carbon-message-attachments")
            .forEach((row) => row.remove());

          const attachments = messageAttachmentSnapshots[index] ?? [];
          if (attachments.length === 0) {
            return;
          }

          const row = document.createElement("div");
          row.className = "cuga-carbon-message-attachments";
          row.style.display = "flex";
          row.style.flexWrap = "wrap";
          row.style.gap = "0.4rem";
          row.style.margin = "0 0 0.55rem";

          attachments.forEach((attachment) => {
            const chip = document.createElement("button");
            chip.type = "button";
            chip.className = "cuga-carbon-message-attachment";
            chip.textContent = attachment.display_name;
            chip.style.border = "1px solid rgba(15, 98, 254, 0.2)";
            chip.style.background = "rgba(15, 98, 254, 0.06)";
            chip.style.color = "#0f62fe";
            chip.style.borderRadius = "999px";
            chip.style.padding = "0.18rem 0.6rem";
            chip.style.fontSize = "0.72rem";
            chip.style.cursor = "pointer";
            chip.addEventListener("click", (event) => {
              event.preventDefault();
              event.stopPropagation();
              onPreviewKnowledgeAttachment?.(attachment);
            });
            row.appendChild(chip);
          });

          target.prepend(row);
        });
      });
    };

    applyMessageAttachmentDecorations();
    const observers = roots.map((shadowRoot) => {
      const observer = new MutationObserver(() => {
        applyMessageAttachmentDecorations();
      });
      observer.observe(shadowRoot, { childList: true, subtree: true });
      return observer;
    });

    return () => observers.forEach((observer) => observer.disconnect());
  }, [chatRenderTick, messageAttachmentSnapshots, onPreviewKnowledgeAttachment, resolveChatDomRoots]);

  // Wrap the custom send message function to ensure it's properly bound
  const handleCustomSendMessage = useCallback(
    async (
      request: MessageRequest,
      options: CustomSendMessageOptions,
      instance: ChatInstance
    ) => {
      const attachmentSnapshot = createMessageAttachmentSnapshot();
      const result = await customSendMessageImpl(
        request,
        options,
        instance,
        useDraft,
        disableHistory,
        undefined,
        attachmentScope === "session" ? attachmentSnapshot : undefined,
      );
      
      if (onThreadChange && currentThreadId) {
        skipNextHistoryLoadRef.current = currentThreadId;
        onThreadChange(currentThreadId);
      }

      await refreshMessageAttachmentSnapshots(currentThreadId);
      
      return result;
    },
    [attachmentScope, createMessageAttachmentSnapshot, disableHistory, onThreadChange, refreshMessageAttachmentSnapshots, useDraft]
  );

  const handleChatReady = useCallback((instance: ChatInstance) => {
    console.log('[CarbonChat] handleChatReady called, setting up event listeners');
    chatInstanceRef.current = instance;
    setChatRenderTick((tick) => tick + 1);
    
    instance.on({
      type: BusEventType.RESTART_CONVERSATION,
      handler: () => {
        console.log('[CarbonChat] RESTART_CONVERSATION event received');
        resetThreadId();
      },
    });

    instance.on({
      type: BusEventType.STOP_STREAMING,
      handler: () => {
        const tid = getOrCreateThreadId();
        console.log('[CarbonChat] STOP_STREAMING event received, calling /stop for thread:', tid);
        stopCugaAgent(tid);
      },
    });
    
    console.log('[CarbonChat] Setting up MESSAGE_ITEM_CUSTOM listener');
    instance.on({
      type: BusEventType.MESSAGE_ITEM_CUSTOM,
      handler: async (event: any) => {
        const buttonItem = event.messageItem;
        if (!buttonItem) return;

        const custom_event_name = buttonItem.custom_event_name;
        const user_defined = buttonItem.user_defined ?? {};

        if (custom_event_name === 'tool_approval_response' || custom_event_name === 'suggest_human_action' || user_defined?.action_id) {
          const approved = user_defined?.approved === true;
          const actionId = user_defined?.action_id;

          const actionResponse = {
            action_id: actionId,
            response_type: 'confirmation',
            timestamp: new Date().toISOString(),
            confirmed: approved,
          };

          const request: MessageRequest = { input: { text: '' } };
          const options: CustomSendMessageOptions = {
            signal: new AbortController().signal,
            silent: false,
          };
          await customSendMessageImpl(request, options, instance, useDraft, disableHistory, actionResponse);
        }
      },
    });
  }, [useDraft, disableHistory]);

  // Load history when threadId changes
  useEffect(() => {
    if (chatInstanceRef.current) {
      if (threadId) {
        currentThreadId = threadId;
        if (skipNextHistoryLoadRef.current === threadId) {
          skipNextHistoryLoadRef.current = null;
          return;
        }
        skipNextHistoryLoadRef.current = null;
        const loadAndInsertHistory = async () => {
          if (!chatInstanceRef.current) return;
          
          try {
            // Clear the current conversation
            await chatInstanceRef.current.messaging.clearConversation();
            
            // Load the history
            const history = await customLoadHistory(chatInstanceRef.current, threadId);
            
            if (history.length > 0 && chatInstanceRef.current) {
              console.log(`Loaded ${history.length} history items for thread ${threadId}`);
              // Insert the history into the chat
              chatInstanceRef.current.messaging.insertHistory(history);
            } else {
              console.log(`No history found for thread ${threadId}`);
            }
          } catch (error) {
            console.error('Error loading history:', error);
          }
        };
        
        loadAndInsertHistory();
      } else {
        // If threadId is null, start a fresh conversation
        console.log('Starting new conversation');
        currentThreadId = null;
        chatInstanceRef.current.messaging.clearConversation();
      }
    }
  }, [threadId]);

  // Wrap customLoadHistory to pass threadId and disableHistory
  const handleCustomLoadHistory = useCallback(
    async (instance: ChatInstance) => {
      if (disableHistory) {
        return [];
      }
      return await customLoadHistory(instance, threadId || undefined);
    },
    [threadId, disableHistory]
  );

  return (
    <>
      {/* Debug Panel Toggle Button */}
      <button
        className="debug-toggle-button"
        onClick={() => setShowDebugPanel(!showDebugPanel)}
        title="Toggle Debug Panel"
      >
        🐛
      </button>

      {/* Debug Panel */}
      {showDebugPanel && (
        <div className="debug-panel">
          <div className="debug-panel-header">
            <h3>Agent State Debug</h3>
            <button
              className="debug-close-button"
              onClick={() => setShowDebugPanel(false)}
            >
              ✕
            </button>
          </div>
          <div className="debug-panel-content">
            {isLoadingDebug && <div className="debug-loading">Loading...</div>}
            {debugError && (
              <div className="debug-error">
                <strong>Error:</strong> {debugError}
              </div>
            )}
            {debugData && (
              <div className="debug-data">
                <div className="debug-section">
                  <strong>Thread ID:</strong>
                  <code>{currentThreadId || 'None'}</code>
                </div>
                {lastUpdateTime && (
                  <div className="debug-section">
                    <strong>Last Updated:</strong>
                    <code>{formatRelativeTime(lastUpdateTime)}</code>
                  </div>
                )}
                <div className="debug-section">
                  <strong>State Data:</strong>
                  <pre>{JSON.stringify(debugData, null, 2)}</pre>
                </div>
              </div>
            )}
          </div>
          <div className="debug-panel-footer">
            <button
              className="debug-refresh-button"
              onClick={fetchDebugData}
              disabled={isLoadingDebug}
            >
              🔄 Refresh
            </button>
            <span className="debug-auto-refresh">
              Auto-refresh: 3s
              {lastUpdateTime && ` • Updated ${formatRelativeTime(lastUpdateTime)}`}
            </span>
          </div>
        </div>
      )}

      <div
        className={`cuga-carbon-chat-wrapper${isDragOver ? ' cuga-carbon-composer--dragover' : ''}`}
        onDragEnter={(e) => {
          if (!attachmentsEnabled) return;
          if (!e.dataTransfer.types.includes('Files')) return;
          e.preventDefault();
          dragCounterRef.current++;
          setIsDragOver(true);
        }}
        onDragOver={(e) => {
          if (!attachmentsEnabled) return;
          if (!e.dataTransfer.types.includes('Files')) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = 'copy';
        }}
        onDragLeave={() => {
          if (!attachmentsEnabled) return;
          dragCounterRef.current--;
          if (dragCounterRef.current <= 0) {
            dragCounterRef.current = 0;
            setIsDragOver(false);
          }
        }}
        onDrop={(e) => {
          if (!attachmentsEnabled) return;
          if (!e.dataTransfer.types.includes('Files')) return;
          e.preventDefault();
          dragCounterRef.current = 0;
          setIsDragOver(false);
          const files = Array.from(e.dataTransfer.files);
          if (files.length > 0) {
            void uploadFiles(files);
          }
        }}
      >
        <ChatCustomElement
        ref={chatElementRef as any}
        className={`${contained ? 'carbon-chat-contained' : 'carbon-chat-fullscreen'} ${className}`}
        injectCarbonTheme={theme === 'dark' ? CarbonTheme.G100 : CarbonTheme.WHITE}
        openChatByDefault={true}
        assistantName={assistantName}
        isReadonly={isReadonly}
        header={{
          isOn: true,
          showRestartButton: true,
          showAiLabel: false,
          hideMinimizeButton: true,

        } as any}
        homescreen={{
          isOn: !isReadonly && (hs.isOn ?? true),
          greeting: hs.greeting ?? DEFAULT_HOMESCREEN.greeting,
          starters: !isReadonly && starterLabels.length > 0
            ? { isOn: true, buttons: starterLabels.map((label) => ({ label })) }
            : { isOn: false, buttons: [] },
        }}
        layout={{
          showFrame: false,
          hasContentMaxWidth: true,
        }}
        input={{
          isVisible: true,
        }}

        messaging={{
          customSendMessage: handleCustomSendMessage,
          customLoadHistory: handleCustomLoadHistory,
        }}
        renderWriteableElements={{
          beforeInputElement: (
            <ComposerToolbar
              items={attachmentItems}
              onDeleteDocument={(knowledgeFilename) => {
                void deleteDocument(knowledgeFilename);
              }}
              onRetryUpload={(uploadId) => {
                void retryUpload(uploadId);
              }}
              onDismissUpload={dismissUpload}
              onPreviewAttachment={onPreviewKnowledgeAttachment}
              onOpenKnowledge={onOpenKnowledge}
              onAttachClick={attachmentsEnabled ? () => fileInputRef.current?.click() : undefined}
              attachmentScope={effectiveAttachmentScope}
            />
          ),
          homeScreenBeforeInputElement: (
            <ComposerToolbar
              items={attachmentItems}
              onDeleteDocument={(knowledgeFilename) => {
                void deleteDocument(knowledgeFilename);
              }}
              onRetryUpload={(uploadId) => {
                void retryUpload(uploadId);
              }}
              onDismissUpload={dismissUpload}
              onPreviewAttachment={onPreviewKnowledgeAttachment}
              onOpenKnowledge={onOpenKnowledge}
              onAttachClick={attachmentsEnabled ? () => fileInputRef.current?.click() : undefined}
              attachmentScope={effectiveAttachmentScope}
            />
          ),
        }}
        onError={(data: any) => console.error('[CarbonChat] onError:', data)}
        onAfterRender={handleChatReady}
        />
        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: "none" }}
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            if (files.length > 0) {
              void uploadFiles(files);
            }
            event.target.value = "";
          }}
        />
      </div>
    </>
  );
};

export default CarbonChat;
