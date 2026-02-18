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
import { customSendMessage as customSendMessageImpl } from './customSendMessage';
import { customLoadHistory } from './customLoadHistory';
import './CarbonChat.css';

// Reset thread ID when conversation restarts
let currentThreadId: string | null = null;

function resetThreadId() {
  currentThreadId = null;
}

export function getOrCreateThreadId(): string {
  if (!currentThreadId) {
    currentThreadId = crypto.randomUUID();
  }
  return currentThreadId;
}

interface CarbonChatProps {
  className?: string;
  theme?: 'light' | 'dark';
  contained?: boolean;
  useDraft?: boolean;
  threadId?: string | null;
  disableHistory?: boolean;
  onThreadChange?: (threadId: string) => void;
}

const CarbonChat = ({
  className = '',
  theme = 'light',
  contained = false,
  useDraft = false,
  threadId = null,
  disableHistory = false,
  onThreadChange
}: CarbonChatProps) => {
  const chatInstanceRef = useRef<ChatInstance | null>(null);
  const [showDebugPanel, setShowDebugPanel] = useState(false);
  const [debugData, setDebugData] = useState<any>(null);
  const [isLoadingDebug, setIsLoadingDebug] = useState(false);
  const [debugError, setDebugError] = useState<string | null>(null);
  const [lastUpdateTime, setLastUpdateTime] = useState<Date | null>(null);

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
      const response = await fetch(`/api/agent/state?thread_id=${activeThreadId}`, {
        headers: {
          'X-Thread-ID': activeThreadId
        }
      });
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

  // Wrap the custom send message function to ensure it's properly bound
  const handleCustomSendMessage = useCallback(
    async (
      request: MessageRequest,
      options: CustomSendMessageOptions,
      instance: ChatInstance
    ) => {
      const result = await customSendMessageImpl(request, options, instance, useDraft, disableHistory);
      
      // Notify parent of thread change after message is sent
      if (onThreadChange && currentThreadId) {
        onThreadChange(currentThreadId);
      }
      
      return result;
    },
    [useDraft, disableHistory, onThreadChange]
  );

  const handleChatReady = useCallback((instance: ChatInstance) => {
    chatInstanceRef.current = instance;
    instance.on({
      type: BusEventType.RESTART_CONVERSATION,
      handler: () => {
        resetThreadId();
      },
    });
  }, []);

  // Load history when threadId changes
  useEffect(() => {
    if (chatInstanceRef.current) {
      if (threadId) {
        // Update the global thread ID to match the selected thread
        currentThreadId = threadId;
        
        // Load and insert the conversation history
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

      <ChatCustomElement
      className={`${contained ? 'carbon-chat-contained' : 'carbon-chat-fullscreen'} ${className}`}
      injectCarbonTheme={theme === 'dark' ? CarbonTheme.G100 : CarbonTheme.WHITE}
      openChatByDefault={true}
      assistantName="CUGA Agent"
      header={{
        isOn: true,
        showRestartButton: true,
        showCloseButton: false
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
      onAfterRender={handleChatReady}
      />
    </>
  );
};

export default CarbonChat;