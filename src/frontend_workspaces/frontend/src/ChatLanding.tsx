import React, { useState, useEffect } from "react";
import { ConfigHeader } from "./ConfigHeader";
import CarbonChat from "./carbon-chat/CarbonChat";
import {
  SideNav,
  SideNavItems,
  SideNavLink,
  Button,
} from "@carbon/react";
import { Add, TrashCan } from "@carbon/icons-react";
import "./ChatLanding.css";

interface ConversationThread {
  thread_id: string;
  latest_version: number;
  first_message: string;
  updated_at: string;
}

// Helper function to format timestamp
const formatTimestamp = (isoString: string): string => {
  // Ensure the ISO string is treated as UTC by adding 'Z' if not present
  const utcString = isoString.endsWith('Z') ? isoString : `${isoString}Z`;
  const date = new Date(utcString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSecs = Math.floor(diffMs / 1000);
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffSecs < 10) return "Just now";
  if (diffSecs < 60) return `${diffSecs}s ago`;
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  
  // Format as date for older conversations
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
};

export function ChatLanding() {
  const [leftSidebarCollapsed, setLeftSidebarCollapsed] = useState(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [threads, setThreads] = useState<ConversationThread[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);

  const handleToggleLeftSidebar = () => {
    setLeftSidebarCollapsed(!leftSidebarCollapsed);
  };

  const handleToggleWorkspace = () => {
    setWorkspaceOpen(!workspaceOpen);
  };

  const handleThreadClick = (threadId: string) => {
    setSelectedThreadId(threadId);
  };

  const handleNewConversation = () => {
    // Set to null to start a fresh conversation
    // A new thread ID will be generated when the user sends their first message
    setSelectedThreadId(null);
  };

  const handleRemoveAllConversations = async () => {
    if (!window.confirm("Are you sure you want to remove all conversations? This action cannot be undone.")) {
      return;
    }
    
    try {
      // Delete all conversations for this agent
      const deletePromises = threads.map(thread =>
        fetch(`/api/conversations/${thread.thread_id}?agent_id=cuga-default&user_id=default_user`, {
          method: 'DELETE'
        })
      );
      
      await Promise.all(deletePromises);
      
      // Clear the threads list
      setThreads([]);
      setSelectedThreadId(null);
      
      console.log('All conversations removed successfully');
    } catch (error) {
      console.error('Error removing conversations:', error);
      alert('Failed to remove conversations. Please try again.');
    }
  };

  // Function to refresh thread list from server
  const refreshThreads = async () => {
    try {
      const response = await fetch('/api/conversation-threads?agent_id=cuga-default&user_id=default_user');
      if (response.ok) {
        const data = await response.json();
        setThreads(data.threads || []);
      } else {
        console.error('Failed to fetch conversation threads');
      }
    } catch (error) {
      console.error('Error fetching conversation threads:', error);
    }
  };

  // Handle thread change notifications from CarbonChat
  const handleThreadChange = async (threadId: string) => {
    console.log(`Thread changed to: ${threadId}`);
    
    // Only update if the thread ID is different from current selection
    if (threadId !== selectedThreadId) {
      // Update selected thread ID
      setSelectedThreadId(threadId);
      
      // Refresh the thread list to include any new threads
      // Use a small delay to avoid rapid successive calls
      setTimeout(() => {
        refreshThreads();
      }, 500);
    }
  };

  // Fetch conversation threads on component mount
  useEffect(() => {
    const fetchThreads = async () => {
      try {
        const response = await fetch('/api/conversation-threads?agent_id=cuga-default&user_id=default_user');
        if (response.ok) {
          const data = await response.json();
          setThreads(data.threads || []);
        } else {
          console.error('Failed to fetch conversation threads');
        }
      } catch (error) {
        console.error('Error fetching conversation threads:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchThreads();
  }, []);

  return (
    <div className="chat-landing">
      <ConfigHeader
        onToggleLeftSidebar={handleToggleLeftSidebar}
        onToggleWorkspace={handleToggleWorkspace}
        leftSidebarCollapsed={leftSidebarCollapsed}
        workspaceOpen={workspaceOpen}
      />
      <div className="chat-landing-body">
        {!leftSidebarCollapsed && (
          <SideNav
            isFixedNav
            expanded={true}
            isChildOfHeader={false}
            aria-label="Conversation History"
            className="conversation-history-sidenav"
            style={{ width: '28rem' }}
          >
            <SideNavItems>
              <div style={{ padding: '0.75rem', display: 'flex', flexDirection: 'row', gap: '0.5rem', alignItems: 'center' }}>
                <Button
                  kind="tertiary"
                  size="sm"
                  renderIcon={Add}
                  onClick={handleNewConversation}
                  hasIconOnly
                  iconDescription="New conversation"
                  tooltipPosition="bottom"
                />
                <Button
                  kind="ghost"
                  size="sm"
                  renderIcon={TrashCan}
                  onClick={handleRemoveAllConversations}
                  disabled={threads.length === 0}
                  hasIconOnly
                  iconDescription="Remove all"
                  tooltipPosition="bottom"
                />
                {/* Debug: Show current thread ID */}
                <div style={{
                  fontSize: '0.65rem',
                  color: '#888',
                  marginLeft: 'auto',
                  wordBreak: 'break-all',
                  fontFamily: 'monospace',
                  maxWidth: '60%'
                }}>
                  {selectedThreadId ? selectedThreadId.substring(0, 8) + '...' : 'none'}
                </div>
              </div>
              {loading ? (
                <SideNavLink href="#">
                  Loading conversations...
                </SideNavLink>
              ) : threads.length === 0 ? (
                <SideNavLink href="#">
                  No conversations yet
                </SideNavLink>
              ) : (
                threads.map((thread: ConversationThread) => (
                  <SideNavLink
                    key={thread.thread_id}
                    href={`#${thread.thread_id}`}
                    className="conversation-link"
                    onClick={(e) => {
                      e.preventDefault();
                      handleThreadClick(thread.thread_id);
                    }}
                    aria-current={selectedThreadId === thread.thread_id ? "page" : undefined}
                  >
                    <div className="conversation-link-content">
                      <div className="conversation-title">{thread.first_message}</div>
                      <div className="conversation-timestamp">{formatTimestamp(thread.updated_at)}</div>
                    </div>
                  </SideNavLink>
                ))
              )}
            </SideNavItems>
          </SideNav>
        )}
        <div className="chat-content-area">
          <CarbonChat
            contained={true}
            threadId={selectedThreadId}
            onThreadChange={handleThreadChange}
          />
        </div>
      </div>
    </div>
  );
}