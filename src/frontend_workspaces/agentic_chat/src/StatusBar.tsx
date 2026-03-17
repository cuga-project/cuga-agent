import React, { useState, useEffect, useRef } from "react";
import { Wrench, Zap, CheckCircle2, AlertCircle, Users, User, MoreHorizontal, Lightbulb } from "lucide-react";
import { apiFetch } from "../../frontend/src/api";
import { exampleUtterances } from "./exampleUtterances";
import "./StatusBar.css";

interface Tool {
  name: string;
  status: "connected" | "disconnected" | "error";
  type: string;
}

interface SubAgent {
  name: string;
  role: string;
  enabled: boolean;
}

interface StatusBarProps {
  threadId?: string;
}

export function StatusBar({ threadId }: StatusBarProps) {
  const [tools, setTools] = useState<Tool[]>([]);
  const [internalToolsCount, setInternalToolsCount] = useState<Record<string, number>>({});
  const [mode, setMode] = useState<"fast" | "balanced">("fast");
  const [agentMode, setAgentMode] = useState<"supervisor" | "single">("supervisor");
  const [subAgents, setSubAgents] = useState<SubAgent[]>([]);
  const [showToolsPopup, setShowToolsPopup] = useState(false);
  const [showAgentsPopup, setShowAgentsPopup] = useState(false);
  const [showAgentSelector, setShowAgentSelector] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [showMoreMenu, setShowMoreMenu] = useState(false);
  const [showExamplesPopup, setShowExamplesPopup] = useState(false);
  const [showModePopup, setShowModePopup] = useState(false);
  const [isInputEmpty, setIsInputEmpty] = useState(true);
  const [visibleItems, setVisibleItems] = useState<Set<string>>(new Set(['tools', 'mode', 'agents', 'connection']));
  const statusBarRef = useRef<HTMLDivElement>(null);
  const agentsPopupTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const examplesPopupTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const modePopupTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Log threadId changes for debugging
  useEffect(() => {
    console.log('[StatusBar] threadId updated:', threadId);
  }, [threadId]);

  useEffect(() => {
    loadTools();
    loadSubAgents();
  }, []);

  // Cleanup timeouts on unmount
  useEffect(() => {
    return () => {
      if (agentsPopupTimeoutRef.current) {
        clearTimeout(agentsPopupTimeoutRef.current);
      }
      if (examplesPopupTimeoutRef.current) {
        clearTimeout(examplesPopupTimeoutRef.current);
      }
      if (modePopupTimeoutRef.current) {
        clearTimeout(modePopupTimeoutRef.current);
      }
    };
  }, []);

  // Monitor input field to detect if it's empty
  useEffect(() => {
    const checkInputEmpty = () => {
      const inputField = document.getElementById('main-input_field');
      if (inputField) {
        const isEmpty = !inputField.textContent?.trim();
        setIsInputEmpty(isEmpty);
      }
    };

    // Check initially
    checkInputEmpty();

    // Set up observer to watch for changes
    const inputField = document.getElementById('main-input_field');
    if (inputField) {
      const observer = new MutationObserver(checkInputEmpty);
      observer.observe(inputField, {
        characterData: true,
        childList: true,
        subtree: true,
      });

      // Also listen for input events
      inputField.addEventListener('input', checkInputEmpty);

      return () => {
        observer.disconnect();
        inputField.removeEventListener('input', checkInputEmpty);
      };
    }
  }, []);

  // Responsive behavior - hide items when container is too narrow
  useEffect(() => {
    const updateVisibleItems = () => {
      if (!statusBarRef.current) return;

      const containerWidth = statusBarRef.current.offsetWidth;
      const newVisibleItems = new Set<string>();

      // Priority order: connection (always visible), tools, mode, agents
      if (containerWidth > 800) {
        newVisibleItems.add('tools');
        newVisibleItems.add('mode');
        newVisibleItems.add('agents');
      } else if (containerWidth > 600) {
        newVisibleItems.add('tools');
        newVisibleItems.add('mode');
      } else if (containerWidth > 400) {
        newVisibleItems.add('tools');
      }
      // Connection is always visible

      setVisibleItems(newVisibleItems);
    };

    updateVisibleItems();

    const resizeObserver = new ResizeObserver(updateVisibleItems);
    if (statusBarRef.current) {
      resizeObserver.observe(statusBarRef.current);
    }

    return () => {
      resizeObserver.disconnect();
    };
  }, []);

  const loadTools = async () => {
    try {
      const response = await apiFetch('/api/tools/status');
      if (response.ok) {
        const data = await response.json();
        setTools(data.tools || []);
        setInternalToolsCount(data.internalToolsCount || {});
      }
    } catch (error) {
      console.error("Error loading tools:", error);
    }
  };

  const loadSubAgents = async () => {
    try {
      const response = await apiFetch('/api/config/subagents');
      if (response.ok) {
        const data = await response.json();
        setSubAgents(data.subAgents || []);
        setAgentMode(data.mode || "supervisor");
        setSelectedAgent(data.selectedAgent || null);
      }
    } catch (error) {
      console.error("Error loading sub-agents:", error);
    }
  };

  const toggleMode = () => {
    // Mode switching disabled - requires local setup
    return;
  };

  const toggleAgentMode = () => {
    const newMode = agentMode === "supervisor" ? "single" : "supervisor";
    
    if (newMode === "single") {
      // Show agent selector when switching to single mode
      setShowAgentSelector(true);
    } else {
      // Clear selected agent when switching to supervisor mode
      setSelectedAgent(null);
      setAgentMode(newMode);
      // Send agent mode change to backend
      apiFetch('/api/config/agent-mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: newMode, selectedAgent: null }),
      }).catch(err => console.error("Failed to update agent mode:", err));
    }
  };

  const selectAgent = (agentName: string) => {
    setSelectedAgent(agentName);
    setAgentMode("single");
    setShowAgentSelector(false);
    // Send agent selection to backend
    apiFetch('/api/config/agent-mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: "single", selectedAgent: agentName }),
    }).catch(err => console.error("Failed to update agent mode:", err));
  };

  const cancelAgentSelection = () => {
    setShowAgentSelector(false);
    // Keep current mode if cancelled
  };

  const toggleAgentEnabled = (agentName: string) => {
    const updatedAgents = subAgents.map(agent =>
      agent.name === agentName ? { ...agent, enabled: !agent.enabled } : agent
    );
    setSubAgents(updatedAgents);

    // Send update to backend
    apiFetch('/api/config/subagents', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        subAgents: updatedAgents,
        mode: agentMode,
        selectedAgent: selectedAgent
      }),
    }).catch(err => console.error("Failed to update agent status:", err));
  };

  const handleAgentsMouseEnter = () => {
    // Clear any pending hide timeout
    if (agentsPopupTimeoutRef.current) {
      clearTimeout(agentsPopupTimeoutRef.current);
      agentsPopupTimeoutRef.current = null;
    }
    setShowAgentsPopup(true);
  };

  const handleAgentsMouseLeave = () => {
    // Delay hiding the popup to allow mouse movement to the popup
    agentsPopupTimeoutRef.current = setTimeout(() => {
      setShowAgentsPopup(false);
    }, 300); // 300ms delay
  };

  const handleAgentsPopupMouseEnter = () => {
    // Clear the hide timeout when mouse enters the popup
    if (agentsPopupTimeoutRef.current) {
      clearTimeout(agentsPopupTimeoutRef.current);
      agentsPopupTimeoutRef.current = null;
    }
  };

  const handleAgentsPopupMouseLeave = () => {
    // Hide the popup when mouse leaves the popup area
    setShowAgentsPopup(false);
  };

  const handleExamplesMouseEnter = () => {
    // Clear any pending hide timeout
    if (examplesPopupTimeoutRef.current) {
      clearTimeout(examplesPopupTimeoutRef.current);
      examplesPopupTimeoutRef.current = null;
    }
    setShowExamplesPopup(true);
  };

  const handleExamplesMouseLeave = () => {
    // Delay hiding the popup to allow mouse movement to the popup
    examplesPopupTimeoutRef.current = setTimeout(() => {
      setShowExamplesPopup(false);
    }, 5000); // 5000ms (5 seconds) delay
  };

  const handleExamplesPopupMouseEnter = () => {
    // Clear the hide timeout when mouse enters the popup
    if (examplesPopupTimeoutRef.current) {
      clearTimeout(examplesPopupTimeoutRef.current);
      examplesPopupTimeoutRef.current = null;
    }
  };

  const handleExamplesPopupMouseLeave = () => {
    // Hide the popup when mouse leaves the popup area
    setShowExamplesPopup(false);
  };

  const handleModeMouseEnter = () => {
    console.log('[StatusBar] Mode hover entered');
    // Clear any pending hide timeout
    if (modePopupTimeoutRef.current) {
      clearTimeout(modePopupTimeoutRef.current);
      modePopupTimeoutRef.current = null;
    }
    setShowModePopup(true);
    console.log('[StatusBar] showModePopup set to true');
  };

  const handleModeMouseLeave = () => {
    console.log('[StatusBar] Mode hover left');
    // Delay hiding the popup with longer delay
    modePopupTimeoutRef.current = setTimeout(() => {
      setShowModePopup(false);
      console.log('[StatusBar] showModePopup set to false');
    }, 500);
  };

  const handleModePopupMouseEnter = () => {
    console.log('[StatusBar] Mode popup hover entered');
    // Clear the hide timeout when mouse enters the popup
    if (modePopupTimeoutRef.current) {
      clearTimeout(modePopupTimeoutRef.current);
      modePopupTimeoutRef.current = null;
    }
  };

  const handleModePopupMouseLeave = () => {
    console.log('[StatusBar] Mode popup hover left');
    // Delay hiding with longer timeout for stability
    modePopupTimeoutRef.current = setTimeout(() => {
      setShowModePopup(false);
    }, 500);
  };

  const connectedTools = tools.filter(t => t.status === "connected");
  const errorTools = tools.filter(t => t.status === "error");
  const activeAgents = subAgents.filter(a => a.enabled);

  const getSelectedAgentInfo = () => {
    if (!selectedAgent) return null;
    return subAgents.find(a => a.name === selectedAgent);
  };

  const handleExampleClick = (utterance: string) => {
    // Send the utterance to the input field
    const inputField = document.getElementById('main-input_field');
    if (inputField) {
      inputField.textContent = utterance;
      inputField.focus();
      // Trigger input event to update parent component
      const event = new Event('input', { bubbles: true });
      inputField.dispatchEvent(event);
    }
    setShowExamplesPopup(false);
  };

  // Get overflow items for the More menu
  const getOverflowItems = () => {
    const overflowItems = [];
    if (!visibleItems.has('mode')) {
      overflowItems.push({
        id: 'mode',
        label: `Mode: ${mode === 'fast' ? 'Lite' : 'Balanced'}`,
        icon: <Zap size={14} />,
        action: toggleMode
      });
    }
    if (!visibleItems.has('agents')) {
      overflowItems.push({
        id: 'agents',
        label: `${agentMode === 'supervisor' ? 'Supervisor' : 'Single'} (${agentMode === 'supervisor' ? activeAgents.length : (selectedAgent ? getSelectedAgentInfo()?.name : 'None')})`,
        icon: agentMode === 'supervisor' ? <Users size={14} /> : <User size={14} />,
        action: () => setShowAgentsPopup(true)
      });
    }
    if (!visibleItems.has('tools')) {
      overflowItems.push({
        id: 'tools',
        label: `Tools: ${connectedTools.length}/${tools.length}`,
        icon: <Wrench size={14} />,
        action: () => setShowToolsPopup(true)
      });
    }
    return overflowItems;
  };

  return (
    <>
      {/* Agent Selector Modal */}
      {showAgentSelector && (
        <div className="config-modal-overlay" onClick={cancelAgentSelection}>
          <div className="config-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: "500px" }}>
            <div className="config-modal-header">
              <h2>Select Agent to Talk With</h2>
              <button className="config-modal-close" onClick={cancelAgentSelection}>
                <span style={{ fontSize: "20px" }}>×</span>
              </button>
            </div>
            <div className="config-modal-content">
              <p style={{ marginBottom: "16px", color: "#64748b", fontSize: "14px" }}>
                Choose which agent you want to communicate with directly:
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {subAgents.filter(a => a.enabled).length === 0 ? (
                  <div style={{ 
                    padding: "24px", 
                    textAlign: "center", 
                    color: "#94a3b8",
                    background: "#f8fafc",
                    borderRadius: "8px"
                  }}>
                    No active agents available. Enable agents in Sub Agents configuration.
                  </div>
                ) : (
                  subAgents.filter(a => a.enabled).map((agent) => (
                    <div
                      key={agent.name}
                      onClick={() => selectAgent(agent.name)}
                      style={{
                        padding: "16px",
                        background: "#f8fafc",
                        border: "2px solid #e5e7eb",
                        borderRadius: "8px",
                        cursor: "pointer",
                        transition: "all 0.2s",
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.borderColor = "#667eea";
                        e.currentTarget.style.background = "#f1f5f9";
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.borderColor = "#e5e7eb";
                        e.currentTarget.style.background = "#f8fafc";
                      }}
                    >
                      <div style={{ fontWeight: 600, fontSize: "14px", color: "#1e293b", marginBottom: "4px" }}>
                        {agent.name}
                      </div>
                      <div style={{ fontSize: "12px", color: "#64748b" }}>
                        {agent.role}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
            <div className="config-modal-footer">
              <button className="cancel-btn" onClick={cancelAgentSelection}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="status-bar" ref={statusBarRef}>
        <div className="status-bar-left">
          {/* Left spacer */}
        </div>

        <div className="status-bar-center">
        {/* Example Utterances */}
        <div
          className={`status-item status-examples ${isInputEmpty ? 'animate-prompt' : ''}`}
          onMouseEnter={handleExamplesMouseEnter}
          onMouseLeave={handleExamplesMouseLeave}
        >
          <Lightbulb size={14} className={isInputEmpty ? 'lightbulb-glow' : ''} />
          <span className="status-label">Try these examples</span>
          <span className="status-badge">{exampleUtterances.length}</span>

          {showExamplesPopup && (
            <div 
              className="examples-popup"
              onMouseEnter={handleExamplesPopupMouseEnter}
              onMouseLeave={handleExamplesPopupMouseLeave}
            >
              <div className="examples-popup-header">
                <span>Example Queries</span>
                <span className="examples-count">{exampleUtterances.length} examples</span>
              </div>
              <div className="examples-list">
                {exampleUtterances.map((utterance, index) => (
                  <div
                    key={index}
                    className="example-item"
                    onClick={() => handleExampleClick(utterance.text)}
                  >
                    <Lightbulb size={12} className="example-icon" />
                    <span className="example-text">{utterance.text}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Tools Status */}
        {visibleItems.has('tools') && (
          <div
            className="status-item status-tools"
            onMouseEnter={() => setShowToolsPopup(true)}
            onMouseLeave={() => setShowToolsPopup(false)}
          >
            <Wrench size={14} />
            <span className="status-label">Tools</span>
            <span className="status-badge">{connectedTools.length}</span>
            {errorTools.length > 0 && (
              <AlertCircle size={12} className="status-warning" />
            )}

            {showToolsPopup && (
              <div className="tools-popup">
                <div className="tools-popup-header">
                  <span>Connected Tools</span>
                  <span className="tools-count">{connectedTools.length}/{tools.length}</span>
                </div>
                <div className="tools-list">
                  {tools.length === 0 ? (
                    <div className="tools-empty">No tools configured</div>
                  ) : (
                    <>
                      {/* Group tools by type and show counts */}
                      {Object.entries(
                        tools.reduce((acc, tool) => {
                          if (!acc[tool.type]) {
                            acc[tool.type] = { total: 0, connected: 0, tools: [] };
                          }
                          acc[tool.type].total++;
                          if (tool.status === 'connected') {
                            acc[tool.type].connected++;
                          }
                          acc[tool.type].tools.push(tool);
                          return acc;
                        }, {} as Record<string, { total: number; connected: number; tools: Tool[] }>)
                      ).map(([type, data]) => (
                        <div key={type} className="tool-group">
                          <div className="tool-group-header">
                            <span className="tool-group-name">{type}</span>
                            <div className="tool-group-stats">
                              <span className="tool-group-count">Connected: {data.connected}/{data.total}</span>
                              {internalToolsCount[type.toLowerCase()] !== undefined && (
                                <span className="tool-group-internal">Internal: {internalToolsCount[type.toLowerCase()]} tools</span>
                              )}
                            </div>
                          </div>
                          <div className="tool-group-items">
                            {data.tools.map((tool) => (
                              <div key={tool.name} className={`tool-item ${tool.status}`}>
                                <div className="tool-status-indicator" />
                                <div className="tool-info">
                                  <span className="tool-name">{tool.name}</span>
                                </div>
                                <span className="tool-status-text">{tool.status}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      ))}
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Mode Toggle */}
        {visibleItems.has('mode') && (
          <div 
            className="status-item status-mode"
            style={{ position: 'relative', cursor: 'pointer' }}
            onMouseEnter={handleModeMouseEnter}
            onMouseLeave={handleModeMouseLeave}
          >
            <Zap size={14} />
            <div className="mode-toggle">
              <div 
                className={`mode-option ${mode === "fast" ? "active" : ""} disabled`}
                style={{ cursor: 'not-allowed', opacity: 0.6 }}
              >
                Lite
              </div>
              <div 
                className={`mode-option ${mode === "balanced" ? "active" : ""}`}
                style={{ cursor: 'pointer' }}
              >
                Balanced
              </div>
            </div>

            {showModePopup && (
              <div 
                className="tools-popup"
                onMouseEnter={handleModePopupMouseEnter}
                onMouseLeave={handleModePopupMouseLeave}
              >
                <div className="tools-popup-header">
                  <span>This feature works locally</span>
                </div>
                <div className="tools-list" style={{ padding: '12px 14px' }}>
                  <div style={{ marginBottom: '12px', color: '#64748b', fontSize: '13px', lineHeight: '1.5' }}>
                    Clone the repo to experience full features of CUGA:
                  </div>
                  <a 
                    href="https://github.com/cuga-project/cuga-agent" 
                    target="_blank" 
                    rel="noopener noreferrer"
                    style={{
                      color: '#667eea',
                      textDecoration: 'none',
                      fontWeight: 500,
                      fontSize: '13px',
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '6px',
                      padding: '4px 0',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.textDecoration = 'underline';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.textDecoration = 'none';
                    }}
                  >
                    <span>github.com/cuga-project/cuga-agent</span>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
                      <polyline points="15 3 21 3 21 9"></polyline>
                      <line x1="10" y1="14" x2="21" y2="3"></line>
                    </svg>
                  </a>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Agent Mode Toggle */}
        {visibleItems.has('agents') && (
          <div
            className="status-item status-agents"
            onMouseEnter={handleAgentsMouseEnter}
            onMouseLeave={handleAgentsMouseLeave}
          >
            {agentMode === "supervisor" ? <Users size={14} /> : <User size={14} />}
            <div className="mode-toggle">
              <div
                className={`mode-option ${agentMode === "supervisor" ? "active" : ""}`}
                onClick={(e) => {
                  e.stopPropagation();
                  if (agentMode !== "supervisor") {
                    toggleAgentMode();
                  }
                }}
              >
                Supervisor
              </div>
              <div
                className={`mode-option ${agentMode === "single" ? "active" : ""} disabled`}
                title="Single agent mode (Coming soon)"
              >
                Single
              </div>
            </div>
            {agentMode === "supervisor" && (
              <span className="status-badge">{activeAgents.length}</span>
            )}

            {showAgentsPopup && (
              <div
                className="agents-popup"
                onMouseEnter={handleAgentsPopupMouseEnter}
                onMouseLeave={handleAgentsPopupMouseLeave}
              >
                <div className="agents-popup-header">
                  {agentMode === "supervisor" ? (
                    <>
                      <span>Talking with All Agents</span>
                      <span className="agents-count">{activeAgents.length} active</span>
                    </>
                  ) : (
                    <>
                      <span>Direct Agent Communication</span>
                      <span className="agents-count">Single mode</span>
                    </>
                  )}
                </div>
                <div className="agents-list">
                  {agentMode === "supervisor" ? (
                    subAgents.length === 0 ? (
                      <div className="agents-empty">No sub-agents configured</div>
                    ) : (
                      <>
                        <div className="agents-info-box">
                          <span className="agents-info-label">Available Sub-Agents (click to toggle):</span>
                        </div>
                        {subAgents.map((agent) => (
                          <div
                            key={agent.name}
                            className={`agent-item ${agent.enabled ? "enabled" : "disabled"}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleAgentEnabled(agent.name);
                            }}
                            style={{ cursor: "pointer" }}
                          >
                            <input
                              type="checkbox"
                              checked={agent.enabled}
                              onChange={() => {}}
                              style={{
                                cursor: "pointer",
                                marginRight: "8px",
                                width: "16px",
                                height: "16px"
                              }}
                              onClick={(e) => e.stopPropagation()}
                            />
                            <div className="agent-status-indicator" />
                            <div className="agent-info">
                              <span className="agent-name">{agent.name}</span>
                              <span className="agent-role">{agent.role}</span>
                            </div>
                            <span className="agent-status-text">{agent.enabled ? "active" : "inactive"}</span>
                          </div>
                        ))}
                      </>
                    )
                  ) : (
                    <div className="agents-info-box single-mode">
                      <User size={32} className="single-agent-icon" />
                      {selectedAgent ? (
                        <>
                          <span className="single-agent-label">Talking with: {getSelectedAgentInfo()?.name}</span>
                          <span className="single-agent-description">
                            Role: {getSelectedAgentInfo()?.role}
                          </span>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setShowAgentSelector(true);
                            }}
                            style={{
                              marginTop: "8px",
                              padding: "6px 12px",
                              background: "#667eea",
                              color: "white",
                              border: "none",
                              borderRadius: "6px",
                              fontSize: "12px",
                              cursor: "pointer",
                            }}
                          >
                            Change Agent
                          </button>
                        </>
                      ) : (
                        <>
                          <span className="single-agent-label">Direct Agent Communication</span>
                          <span className="single-agent-description">
                            Click to select which agent to talk with.
                          </span>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setShowAgentSelector(true);
                            }}
                            style={{
                              marginTop: "8px",
                              padding: "6px 12px",
                              background: "#667eea",
                              color: "white",
                              border: "none",
                              borderRadius: "6px",
                              fontSize: "12px",
                              cursor: "pointer",
                            }}
                          >
                            Select Agent
                          </button>
                        </>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
        </div>

        {/* More Menu for overflow items */}
        {getOverflowItems().length > 0 && (
          <div
            className="status-item status-more"
            onMouseEnter={() => setShowMoreMenu(true)}
            onMouseLeave={() => setShowMoreMenu(false)}
          >
            <MoreHorizontal size={14} />
            <span className="status-label">More</span>

            {showMoreMenu && (
              <div className="more-popup">
                <div className="more-popup-header">
                  <span>More Options</span>
                </div>
                <div className="more-list">
                  {getOverflowItems().map((item) => (
                    <div
                      key={item.id}
                      className="more-item"
                      onClick={(e) => {
                        e.stopPropagation();
                        item.action();
                        setShowMoreMenu(false);
                      }}
                    >
                      {item.icon}
                      <span className="more-item-label">{item.label}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

      <div className="status-bar-right">
        {/* Connection Status */}
        <div className="status-item status-connection">
          <CheckCircle2 size={14} className="status-connected" />
          <span className="status-label">Connected</span>
        </div>
      </div>
    </div>
    </>
  );
}

