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
import { SlashCommandDropdown } from './SlashCommandDropdown';
import { findShadowRoots } from './composerTextarea';
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

// Setter used by customSendMessage on a ``ThreadIdChanged`` SSE event.
export function setThreadId(newThreadId: string): void {
  currentThreadId = newThreadId;
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
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const [chatElement, setChatElement] = useState<HTMLElement | null>(null);
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
  // Known slash-command names, used to decorate /command mentions in user
  // bubbles with pills. Slash semantics are soft (a mention anywhere in the
  // message is a suggestion to the agent), so every known token gets a pill,
  // not just a leading one.
  const [knownCommandNames, setKnownCommandNames] = useState<string[]>([]);

  useEffect(() => {
    initAgentProfile(useDraft);
    getResponseUserProfile(useDraft).then((p) => setAssistantName(p.nickname || "CUGA Agent"));
  }, [useDraft]);

  useEffect(() => {
    let cancelled = false;
    api
      .getCommands()
      .then((commands) => {
        if (!cancelled) setKnownCommandNames(commands.map((c) => c.name));
      })
      .catch(() => {
        // Pills are purely decorative — an unavailable command list just
        // means undecorated text.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Keep the transport thread ID aligned with the parent-owned draft thread
  // even before the chat instance finishes booting.
  useEffect(() => {
    currentThreadId = threadId ?? null;
  }, [threadId]);

  // Shared shadow-DOM walk (same candidate selectors + nested-container
  // traversal as the slash-command composer lookup) — see composerTextarea.ts.
  const resolveChatDomRoots = useCallback(() => findShadowRoots(chatElementRef.current), []);

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

    // Relabel Carbon's reasoning-panel toggle from "Show reasoning"/"Hide
    // reasoning" to "Show details"/"Hide details". The reasoning panel now
    // also hosts the slash-skill audit step (formerly its own chip), so the
    // more neutral wording reads better. Carbon's `strings` prop accepts
    // `reasoningSteps_mainLabelOpen` / `reasoningSteps_mainLabelClosed`, but
    // the React parent commits the toggle attributes before our `useOnMount`
    // strings dispatch reaches them, so the labels stay default. Patching
    // the Lit element directly here is reliable and avoids forking Carbon.
    const TOGGLE_TAGS = [
      "cds-aichat-reasoning-steps-toggle",
      "cds-custom-aichat-reasoning-steps-toggle",
    ];
    const applyReasoningToggleLabels = (root: ShadowRoot | Document) => {
      for (const tag of TOGGLE_TAGS) {
        const nodes = Array.from(root.querySelectorAll(tag)) as Array<
          HTMLElement & { openLabelText?: string; closedLabelText?: string }
        >;
        for (const el of nodes) {
          if (el.closedLabelText !== "Show details") {
            el.closedLabelText = "Show details";
          }
          if (el.openLabelText !== "Hide details") {
            el.openLabelText = "Hide details";
          }
          // Mirror the property change onto the attribute so the visible
          // text inside the toggle's own shadow tree updates immediately.
          if (el.getAttribute("closed-label-text") !== "Show details") {
            el.setAttribute("closed-label-text", "Show details");
          }
          if (el.getAttribute("open-label-text") !== "Hide details") {
            el.setAttribute("open-label-text", "Hide details");
          }
        }
      }
    };

    // Wrap known ``/command`` mentions in user (request) bubbles with pill
    // spans. Carbon owns the bubble DOM inside its shadow root, so — like
    // the attachment chips below — decoration happens as a DOM pass driven
    // by the MutationObserver, and the spans are styled inline (light-DOM
    // stylesheets cannot pierce the shadow boundary; Carbon's injected
    // theme defines the --cds-* variables the colors derive from, so both
    // Carbon themes are covered). Idempotent by construction: text already
    // inside a pill span is skipped, and the plain-text fragments left
    // around a wrapped token can no longer match a known /command, so the
    // decorate pass reaches a fixed point.
    const wrapKnownSlashTokens = (root: Element | ShadowRoot, known: Set<string>) => {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      const textNodes: Text[] = [];
      let node: Node | null;
      while ((node = walker.nextNode())) {
        const parent = (node as Text).parentElement;
        if (parent && parent.closest(".cuga-slash-command-pill")) continue;
        textNodes.push(node as Text);
      }
      for (const textNode of textNodes) {
        const text = textNode.textContent ?? "";
        // A decorated token is a maximal non-whitespace run ``/name`` at
        // the start of the text or right after whitespace whose full name
        // is a known command ("/deck," is NOT decorated — strict match).
        const re = /(^|\s)\/(\S+)/g;
        let match: RegExpExecArray | null;
        let last = 0;
        let fragment: DocumentFragment | null = null;
        while ((match = re.exec(text))) {
          const name = match[2];
          if (!known.has(name)) continue;
          const start = match.index + match[1].length;
          const end = start + 1 + name.length;
          if (!fragment) fragment = document.createDocumentFragment();
          if (start > last) {
            fragment.appendChild(document.createTextNode(text.slice(last, start)));
          }
          const pill = document.createElement("span");
          pill.className = "cuga-slash-command-pill";
          pill.textContent = text.slice(start, end);
          // Same color recipe as .cuga-slash-inline-pill in
          // CarbonChat.css — keep the two in sync.
          pill.style.background =
            "color-mix(in srgb, var(--cds-support-warning, #f1c21b) 24%, transparent)";
          pill.style.boxShadow =
            "inset 0 0 0 1px color-mix(in srgb, var(--cds-support-warning, #f1c21b) 38%, transparent)";
          pill.style.borderRadius = "4px";
          pill.style.padding = "0 0.25em";
          pill.style.fontFamily = "ui-monospace, SFMono-Regular, Menlo, monospace";
          pill.style.fontSize = "0.92em";
          pill.style.fontWeight = "500";
          pill.style.color = "inherit";
          fragment.appendChild(pill);
          last = end;
        }
        if (fragment) {
          if (last < text.length) {
            fragment.appendChild(document.createTextNode(text.slice(last)));
          }
          textNode.replaceWith(fragment);
        }
      }
    };

    const applySlashCommandPills = (shadowRoot: ShadowRoot) => {
      if (knownCommandNames.length === 0) return;
      const known = new Set(knownCommandNames);
      // Carbon registers its parts under either the ``cds-aichat--`` or the
      // ``cds-custom-aichat--`` class prefix depending on the custom-element
      // registration mode (cf. the dual tag list in the reasoning-toggle
      // relabel above), so match both.
      const containers = Array.from(
        shadowRoot.querySelectorAll(
          [
            ".cds-aichat--message--request .cds-aichat--sent--text",
            ".cds-aichat--message--request .cds-aichat--message--padding",
            ".cds-custom-aichat--message--request .cds-custom-aichat--sent--text",
            ".cds-custom-aichat--message--request .cds-custom-aichat--message--padding",
          ].join(", "),
        ),
      ) as HTMLElement[];
      for (const container of containers) {
        // Carbon renders the bubble text through a ``cds-aichat-markdown``
        // Lit element that reflects its source into ITS OWN shadow root; the
        // light-DOM copy we'd otherwise decorate sits inside a ``<div
        // hidden>`` slot host and never paints. So decorate the markdown
        // element's rendered shadow tree. This is safe: a sent message's
        // markdown source is static (Lit never re-renders it), and our outer
        // MutationObserver does not observe nested shadow roots, so wrapping
        // text there can't trigger a decorate loop. If a re-render ever did
        // clear the pills, the next outer mutation re-runs this pass.
        const markdowns = Array.from(
          container.querySelectorAll("cds-aichat-markdown, cds-custom-aichat-markdown"),
        ) as Array<HTMLElement & { shadowRoot: ShadowRoot | null }>;
        if (markdowns.length > 0) {
          for (const md of markdowns) {
            if (md.shadowRoot) wrapKnownSlashTokens(md.shadowRoot, known);
          }
        } else {
          // Builds that render the text directly (no markdown element).
          wrapKnownSlashTokens(container, known);
        }
      }
    };

    const applyMessageAttachmentDecorations = () => {
      roots.forEach((shadowRoot) => {
        // Keep the reasoning-toggle relabel inside the per-root pass so it
        // runs whenever decorations rebuild, but the observer callback also
        // invokes it directly (without debounce) — relabeling is cheap and
        // idempotent, and we don't want a queued rAF to delay the label fix.
        applyReasoningToggleLabels(shadowRoot);
        applySlashCommandPills(shadowRoot);
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

    // rAF-debounce decoration rebuilds — observer fires per mutation during streaming and a tight rebuild loop is expensive.
    let scheduled = false;
    let cancelled = false;
    let rafHandle = 0;
    const flush = () => {
      scheduled = false;
      if (cancelled) return;
      applyMessageAttachmentDecorations();
    };
    const scheduleDecorations = () => {
      if (scheduled) return;
      scheduled = true;
      rafHandle = window.requestAnimationFrame(flush);
    };

    const observers = roots.map((shadowRoot) => {
      const observer = new MutationObserver(() => {
        applyReasoningToggleLabels(shadowRoot);
        scheduleDecorations();
      });
      observer.observe(shadowRoot, { childList: true, subtree: true });
      return observer;
    });

    return () => {
      cancelled = true;
      if (scheduled && rafHandle) window.cancelAnimationFrame(rafHandle);
      observers.forEach((observer) => observer.disconnect());
    };
  }, [chatRenderTick, knownCommandNames, messageAttachmentSnapshots, onPreviewKnowledgeAttachment, resolveChatDomRoots]);

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
    if (chatElementRef.current) {
      setChatElement(chatElementRef.current);
    }
    
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
        ref={wrapperRef}
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
        <SlashCommandDropdown
          chatElement={chatElement}
          portalContainer={wrapperRef.current}
        />
      </div>
    </>
  );
};

export default CarbonChat;
