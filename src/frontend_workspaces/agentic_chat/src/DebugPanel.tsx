import React, { useState, useEffect, useCallback } from "react";
import { Bug, RefreshCw, ChevronDown, ChevronUp, Copy, Check } from "lucide-react";
import { apiFetch } from "../../frontend/src/api";
import "./DebugPanel.css";

interface AgentState {
  thread_id: string;
  state: {
    input?: string;
    url?: string;
    current_app?: string;
    chat_messages_count?: number;
    lite_mode?: boolean | null;
  } | null;
  variables: Record<string, any>;
  variables_count: number;
  message?: string;
}

interface DebugPanelProps {
  threadId: string;
}

const EMPTY_THREAD_ID = "Not initialized";

export function DebugPanel({ threadId }: DebugPanelProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [agentState, setAgentState] = useState<AgentState | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [copied, setCopied] = useState(false);

  const fetchAgentState = useCallback(async () => {
    if (!threadId || threadId === EMPTY_THREAD_ID) {
      setError("No thread ID available");
      setAgentState(null);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const response = await apiFetch('/api/agent/state', {
        method: "GET",
        headers: {
          "X-Thread-ID": threadId,
        },
      });

      if (response.status === 503) {
        setAgentState({
          thread_id: threadId,
          state: null,
          variables: {},
          variables_count: 0,
          message: "Agent graph not initialized",
        });
        return;
      }

      if (!response.ok) {
        throw new Error(`Failed to fetch state: ${response.status} ${response.statusText}`);
      }

      const data = await response.json();
      setAgentState(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch agent state");
      console.error("Error fetching agent state:", err);
    } finally {
      setIsLoading(false);
    }
  }, [threadId]);

  useEffect(() => {
    if (isOpen && threadId) {
      fetchAgentState();
    }
  }, [isOpen, threadId, fetchAgentState]);

  useEffect(() => {
    if (!autoRefresh || !isOpen || !threadId) return;

    const interval = setInterval(() => {
      fetchAgentState();
    }, 2000);

    return () => clearInterval(interval);
  }, [autoRefresh, isOpen, threadId, fetchAgentState]);

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const formatJSON = (obj: any): string => {
    try {
      return JSON.stringify(obj, null, 2);
    } catch {
      return String(obj);
    }
  };

  return (
    <div className="debug-panel-container">
      <button
        className="debug-panel-toggle"
        onClick={() => setIsOpen(!isOpen)}
        title="Toggle Debug Panel"
      >
        <Bug size={16} />
        <span>Debug</span>
        {isOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
      </button>

      {isOpen && (
        <div className="debug-panel-content">
          <div className="debug-panel-header">
            <h3>Debug Information</h3>
            <div className="debug-panel-actions">
              <button
                className="debug-action-btn"
                onClick={fetchAgentState}
                disabled={isLoading}
                title="Refresh State"
              >
                <RefreshCw size={14} className={isLoading ? "spinning" : ""} />
              </button>
              <label className="debug-auto-refresh">
                <input
                  type="checkbox"
                  checked={autoRefresh}
                  onChange={(e) => setAutoRefresh(e.target.checked)}
                />
                <span>Auto-refresh</span>
              </label>
            </div>
          </div>

          <div className="debug-section">
            <div className="debug-section-header">
              <strong>Thread ID</strong>
              {threadId && threadId !== EMPTY_THREAD_ID && (
                <button
                  className="debug-copy-btn"
                  onClick={() => copyToClipboard(threadId)}
                  title="Copy Thread ID"
                >
                  {copied ? <Check size={12} /> : <Copy size={12} />}
                </button>
              )}
            </div>
            <div className="debug-thread-id">{threadId || EMPTY_THREAD_ID}</div>
          </div>

          <div className="debug-section">
            <div className="debug-section-header">
              <strong>Agent State</strong>
            </div>
            {isLoading && <div className="debug-loading">Loading...</div>}
            {error && <div className="debug-error">Error: {error}</div>}
            {agentState && (
              <div className="debug-state-content">
                <div className="debug-state-item">
                  <span className="debug-label">Thread ID:</span>
                  <span className="debug-value">{agentState.thread_id}</span>
                </div>
                <div className="debug-state-item">
                  <span className="debug-label">Variables Count:</span>
                  <span className="debug-value">{agentState.variables_count}</span>
                </div>
                {agentState.state ? (
                  <>
                    <div className="debug-state-item">
                      <span className="debug-label">Lite Mode:</span>
                      <span className="debug-value" style={{ 
                        color: agentState.state.lite_mode === null ? '#94a3b8' : 
                               agentState.state.lite_mode ? '#10b981' : '#f59e0b',
                        fontWeight: 600
                      }}>
                        {agentState.state.lite_mode === null ? 'Not Set (using settings)' : 
                         agentState.state.lite_mode ? 'True (Fast/Lite)' : 'False (Balanced)'}
                      </span>
                    </div>
                    <div className="debug-state-item">
                      <span className="debug-label">Current App:</span>
                      <span className="debug-value">{agentState.state.current_app || "N/A"}</span>
                    </div>
                    <div className="debug-state-item">
                      <span className="debug-label">Chat Messages Count:</span>
                      <span className="debug-value">{agentState.state.chat_messages_count || 0}</span>
                    </div>
                    <div className="debug-state-item">
                      <span className="debug-label">URL:</span>
                      <span className="debug-value">{agentState.state.url || "N/A"}</span>
                    </div>
                    {agentState.state.input && (
                      <div className="debug-state-item">
                        <span className="debug-label">Input:</span>
                        <span className="debug-value">{agentState.state.input}</span>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="debug-state-item">
                    <span className="debug-label">State:</span>
                    <span className="debug-value">{agentState.message || "No state available"}</span>
                  </div>
                )}
                {Object.keys(agentState.variables).length > 0 && (
                  <div className="debug-state-item debug-variables">
                    <span className="debug-label">Variables:</span>
                    <pre className="debug-json">{formatJSON(agentState.variables)}</pre>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

