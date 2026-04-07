import React, { useState, useEffect } from "react";
import { MessageSquare, Database, ChevronLeft, ChevronRight, Plus, Trash2, Workflow, Info, HelpCircle } from "lucide-react";
import { apiFetch } from "../../frontend/src/api";
import "./LeftSidebar.css";
import VariablesSidebar from "./VariablesSidebar";

interface Conversation {
  id: string;
  title: string;
  timestamp: number;
  preview?: string;
}

interface SavedFlow {
  id: string;
  name: string;
  description?: string;
  parameters: Array<{
    name: string;
    type: string;
    required: boolean;
    default?: any;
  }>;
  timestamp: number;
}

interface LeftSidebarProps {
  globalVariables: Record<string, any>;
  variablesHistory: Array<{
    id: string;
    title: string;
    timestamp: number;
    variables: Record<string, any>;
  }>;
  selectedAnswerId: string | null;
  onSelectAnswer: (id: string | null) => void;
  isCollapsed?: boolean;
  activeTab?: "conversations" | "variables" | "savedflows";
  onTabChange?: (tab: "conversations" | "variables" | "savedflows") => void;
  leftSidebarRef?: React.RefObject<{ addConversation: (title: string) => void }>;
  onSelectConversation?: (threadId: string) => void;
}

const ENABLE_CHAT_HISTORY = true;
const ENABLE_SAVED_FLOWS = false;

export function LeftSidebar({
  globalVariables,
  variablesHistory,
  selectedAnswerId,
  onSelectAnswer,
  isCollapsed = false,
  activeTab = "conversations",
  onTabChange,
  leftSidebarRef,
  onSelectConversation
}: LeftSidebarProps) {
  const [isExpanded, setIsExpanded] = useState(!isCollapsed);

  useEffect(() => {
    setIsExpanded(!isCollapsed);
  }, [isCollapsed]);
  
  // Debug logging for variables
  useEffect(() => {
    console.log('[LeftSidebar] globalVariables updated:', Object.keys(globalVariables).length, 'keys');
    console.log('[LeftSidebar] variablesHistory updated:', variablesHistory.length, 'items');
  }, [globalVariables, variablesHistory]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedConversation, setSelectedConversation] = useState<string | null>(null);
  const [savedFlows, setSavedFlows] = useState<SavedFlow[]>([]);
  const [hoveredFlowId, setHoveredFlowId] = useState<string | null>(null);
  const [tooltipPosition, setTooltipPosition] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    console.log('[LeftSidebar] Component mounted');
    loadConversations();
    loadSavedFlows();
  }, []);

  const loadConversations = async () => {
    try {
      const response = await apiFetch('/api/conversations');
      if (response.ok) {
        const data = await response.json();
        setConversations(data);
      }
    } catch (error) {
      console.error("Error loading conversations:", error);
    }
  };

  const createNewConversation = async (customTitle?: string) => {
    console.log('[LeftSidebar] createNewConversation called with title:', customTitle);
    const newConv = {
      id: `conv-${Date.now()}`,
      title: customTitle || "New Conversation",
      timestamp: Date.now()
    };

    // Always add conversation locally first for immediate UI feedback
    console.log('[LeftSidebar] Adding conversation locally:', newConv);
    setConversations(prevConversations => [newConv, ...prevConversations]);
    setSelectedConversation(newConv.id);

    // Try to sync with API (but don't fail if API is unavailable)
    try {
      const response = await apiFetch('/api/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: customTitle || "New Conversation",
          timestamp: Date.now()
        })
      });

      if (response.ok) {
        const apiConv = await response.json();
        console.log('[LeftSidebar] API sync successful:', apiConv);
        // Update with API response if needed
        setConversations(prevConversations =>
          prevConversations.map(conv =>
            conv.id === newConv.id ? { ...apiConv, id: newConv.id } : conv
          )
        );
      } else {
        console.warn('[LeftSidebar] API call failed but local conversation added');
      }
    } catch (error) {
      console.warn('[LeftSidebar] API error but local conversation added:', error);
    }
  };

  // Function to add a conversation programmatically
  const addConversation = React.useCallback((title: string) => {
    createNewConversation(title);
  }, []);

  // Expose addConversation function via ref
  React.useImperativeHandle(leftSidebarRef, () => ({
    addConversation
  }), [addConversation]);

  // Debug: log when ref is set
  React.useEffect(() => {
    if (leftSidebarRef?.current) {
      console.log('[LeftSidebar] Ref is set, addConversation available');
    }
  }, [leftSidebarRef?.current]);

  const deleteConversation = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      const response = await apiFetch(`/api/conversations/${id}`, {
        method: 'DELETE'
      });
      
      if (response.ok) {
        setConversations(conversations.filter(c => c.id !== id));
        if (selectedConversation === id) {
          setSelectedConversation(null);
        }
      }
    } catch (error) {
      console.error("Error deleting conversation:", error);
    }
  };

  const loadSavedFlows = async () => {
    try {
      const response = await apiFetch('/api/flows');
      if (response.ok) {
        const data = await response.json();
        setSavedFlows(data.flows || []);
      } else {
        setSavedFlows([]);
      }
    } catch (error) {
      console.error("Error loading saved flows:", error);
      setSavedFlows([]);
    }
  };

  const formatDate = (timestamp: number) => {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const minutes = Math.floor(diff / (1000 * 60));
    const hours = Math.floor(diff / (1000 * 60 * 60));
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));
    
    if (minutes < 1) return "1 minute ago";
    if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
    if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
    if (days === 1) return "now";
    if (days < 7) return `${days} days ago`;
    return date.toLocaleDateString();
  };

  if (!isExpanded) {
    return (
      <div className="left-sidebar-floating-toggle" onClick={() => setIsExpanded(true)}>
        <ChevronRight size={20} />
        <div className="sidebar-floating-count">{conversations.length}</div>
      </div>
    );
  }

  return (
    <div className={`left-sidebar ${isExpanded ? "expanded" : "collapsed"}`}>
      <div className="left-sidebar-header">
        <div className="left-sidebar-tabs">
          {ENABLE_CHAT_HISTORY && (
            <button
              className={`sidebar-tab ${activeTab === "conversations" ? "active" : ""}`}
              onClick={() => onTabChange ? onTabChange("conversations") : null}
            >
              <MessageSquare size={16} />
              <span>Chats</span>
            </button>
          )}
          <button
            className={`sidebar-tab ${activeTab === "variables" ? "active" : ""}`}
            onClick={() => onTabChange ? onTabChange("variables") : null}
          >
            <Database size={16} />
            <span>Variables</span>
            <div 
              className="sidebar-tab-info-tooltip-wrapper"
              onClick={(e) => e.stopPropagation()}
              onMouseEnter={(e) => e.stopPropagation()}
            >
              <HelpCircle size={14} className="sidebar-info-icon" />
              <div className="sidebar-tab-info-tooltip">
                Variables are the results from task execution. Ask any question about the variables and CUGA will respond.
              </div>
            </div>
          </button>
          {ENABLE_SAVED_FLOWS && (
            <button
              className={`sidebar-tab ${activeTab === "savedflows" ? "active" : ""}`}
              onClick={() => onTabChange ? onTabChange("savedflows") : null}
            >
              <Workflow size={16} />
              <span>Saved Flows</span>
            </button>
          )}
        </div>
        <button 
          className="left-sidebar-toggle"
          onClick={() => setIsExpanded(false)}
        >
          <ChevronLeft size={18} />
        </button>
      </div>

      <div className="left-sidebar-content">
        {ENABLE_CHAT_HISTORY && activeTab === "conversations" ? (
          <>
            <div className="conversations-actions">
              <button className="new-conversation-btn" onClick={() => createNewConversation()}>
                <Plus size={16} />
                <span>New Chat</span>
              </button>
            </div>

            <div className="conversations-list">
              {conversations.length === 0 ? (
                <div className="empty-state">
                  <MessageSquare size={32} />
                  <p>No conversations yet</p>
                  <small>Start a new chat to begin</small>
                </div>
              ) : (
                conversations.map((conv) => (
                  <div
                    key={conv.id}
                    className={`conversation-item ${selectedConversation === conv.id ? "selected" : ""}`}
                    onClick={() => { setSelectedConversation(conv.id); onSelectConversation?.(conv.id); }}
                  >
                    <div className="conversation-header">
                      <MessageSquare size={14} />
                      <span className="conversation-title">{conv.title}</span>
                      <button
                        className="delete-conversation-btn"
                        onClick={(e) => deleteConversation(conv.id, e)}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                    {conv.preview && (
                      <div className="conversation-preview">{conv.preview}</div>
                    )}
                    <div className="conversation-date">{formatDate(conv.timestamp)}</div>
                  </div>
                ))
              )}
            </div>
          </>
        ) : activeTab === "variables" ? (
          <div className="variables-wrapper">
            <VariablesSidebar
              variables={globalVariables}
              history={variablesHistory}
              selectedAnswerId={selectedAnswerId}
              onSelectAnswer={onSelectAnswer}
            />
          </div>
        ) : ENABLE_SAVED_FLOWS && activeTab === "savedflows" ? (
          <>
            <div className="conversations-list">
              {savedFlows.length === 0 ? (
                <div className="empty-state">
                  <Workflow size={32} />
                  <p>No saved flows yet</p>
                  <small>Saved flows will appear here</small>
                </div>
              ) : (
                savedFlows.map((flow) => (
                  <div key={flow.id} className="conversation-item flow-item">
                    <div className="conversation-header">
                      <Workflow size={14} />
                      <span className="conversation-title">{flow.name}</span>
                      <div
                        className="flow-info-icon-wrapper"
                        onMouseEnter={(e) => {
                          const rect = e.currentTarget.getBoundingClientRect();
                          const viewportWidth = window.innerWidth;
                          const tooltipWidth = 300;
                          const tooltipHeight = 120; // approximate height

                          // Position tooltip to the right if there's space, otherwise to the left
                          let left = rect.right + 12;
                          if (left + tooltipWidth > viewportWidth) {
                            left = rect.left - tooltipWidth - 12;
                          }

                          // Ensure tooltip doesn't go off the top or bottom
                          let top = rect.top;
                          if (top + tooltipHeight > window.innerHeight) {
                            top = window.innerHeight - tooltipHeight - 10;
                          }
                          if (top < 10) {
                            top = 10;
                          }

                          setTooltipPosition({ top, left });
                          setHoveredFlowId(flow.id);
                        }}
                        onMouseLeave={(e) => {
                          // Only hide if the mouse is not entering the tooltip area
                          const tooltip = document.querySelector('.flow-info-tooltip');
                          if (tooltip) {
                            const tooltipRect = tooltip.getBoundingClientRect();
                            const mouseX = e.clientX;
                            const mouseY = e.clientY;

                            // Check if mouse is within tooltip bounds
                            if (mouseX >= tooltipRect.left && mouseX <= tooltipRect.right &&
                                mouseY >= tooltipRect.top && mouseY <= tooltipRect.bottom) {
                              return; // Keep tooltip visible
                            }
                          }

                          setHoveredFlowId(null);
                          setTooltipPosition(null);
                        }}
                      >
                        <Info size={14} className="flow-info-icon" />
                      </div>
                    </div>
                    {flow.description && (
                      <div className="conversation-preview">{flow.description}</div>
                    )}
                    <div className="flow-parameters">
                      <div className="flow-function-signature">
                        <code>{flow.name}(</code>
                        <div className="flow-params-list">
                          {flow.parameters.map((param, idx) => (
                            <div key={idx} className="flow-param">
                              <code className="param-name">{param.name}</code>
                              <span className="param-type">: {param.type}</span>
                              {!param.required && param.default !== undefined && (
                                <span className="param-default"> = {JSON.stringify(param.default)}</span>
                              )}
                              {param.required && <span className="param-required">*</span>}
                              {idx < flow.parameters.length - 1 && <span>,</span>}
                            </div>
                          ))}
                        </div>
                        <code>)</code>
                      </div>
                    </div>
                    <div className="conversation-date">{formatDate(flow.timestamp)}</div>
                  </div>
                ))
              )}
            </div>
          </>
        ) : null}
      </div>
      
      {hoveredFlowId && tooltipPosition && (
        <div
          className="flow-info-tooltip"
          style={{
            top: `${tooltipPosition.top}px`,
            left: `${tooltipPosition.left}px`
          }}
          onMouseEnter={() => {
            // Keep tooltip visible when mouse enters it
            setHoveredFlowId(hoveredFlowId);
          }}
          onMouseLeave={() => {
            // Hide tooltip when mouse leaves
            setHoveredFlowId(null);
            setTooltipPosition(null);
          }}
        >
          Saved from a previous conversation where you completed a similar task using CRM, filesystem, and email tools. Reuse this flow to repeat the same pattern with different parameters.
        </div>
      )}
    </div>
  );
}


