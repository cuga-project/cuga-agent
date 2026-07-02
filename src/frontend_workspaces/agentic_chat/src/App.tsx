import { useState, Component, ErrorInfo, ReactNode, useCallback, useRef, useEffect } from "react";
import React from "react";
import { createRoot } from "react-dom/client";
import { CustomChat } from "./CustomChat";
import { ConfigHeader } from "./ConfigHeader";
import { LeftSidebar } from "./LeftSidebar";
import { StatusBar } from "./StatusBar";
import { KnowledgeSidePanel } from "./KnowledgeSidePanel";
import { GuidedTour, TourStep } from "./GuidedTour";
import { useTour } from "./useTour";
import { AdvancedTourButton } from "./AdvancedTourButton";
import * as api from "../../frontend/src/api";
import { randomUUID } from "./uuid";
import "./AppLayout.css";
import "./mockApi";

// Error Boundary Component
class ErrorBoundary extends Component<
  { children: ReactNode },
  { hasError: boolean; error: Error | null }
> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("Error caught by boundary:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: "20px", textAlign: "center" }}>
          <h2>Something went wrong</h2>
          <p>{this.state.error?.message || "Unknown error"}</p>
          <button
            onClick={() => {
              this.setState({ hasError: false, error: null });
              window.location.reload();
            }}
          >
            Reload Page
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

export function App() {
  const [globalVariables, setGlobalVariables] = useState<Record<string, any>>({});
  const [variablesHistory, setVariablesHistory] = useState<Array<{
    id: string;
    title: string;
    timestamp: number;
    variables: Record<string, any>;
  }>>([]);
  const [selectedAnswerId, setSelectedAnswerId] = useState<string | null>(null);
  const [knowledgePanelOpen, setKnowledgePanelOpen] = useState(false);
  const [knowledgeEnabled, setKnowledgeEnabled] = useState<boolean | null>(null);
  const [agentKnowledgeEnabled, setAgentKnowledgeEnabled] = useState<boolean | null>(null);
  const [sessionKnowledgeEnabled, setSessionKnowledgeEnabled] = useState<boolean | null>(null);
  const [agentLabel, setAgentLabel] = useState("this agent");
  const [sessionDocsVersion, setSessionDocsVersion] = useState(0);
  const [knowledgeDocCount, setKnowledgeDocCount] = useState(0);
  const [leftSidebarCollapsed, setLeftSidebarCollapsed] = useState(false);
  const [activeTab, setActiveTab] = useState<"conversations" | "variables" | "savedflows">("conversations");
  const [previousVariablesCount, setPreviousVariablesCount] = useState(0);
  const [previousHistoryLength, setPreviousHistoryLength] = useState(0);
  const [threadId, setThreadId] = useState(() => randomUUID());
  const [selectedThreadId, setSelectedThreadId] = useState<string | undefined>(undefined);
  const [workspaceFilesystemRoot, setWorkspaceFilesystemRoot] = useState("cuga_workspace");
  const leftSidebarRef = useRef<{ addConversation: (title: string) => void } | null>(null);
  const [hasStartedChat, setHasStartedChat] = useState(() => {
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get('mode') === 'advanced';
  });

  useEffect(() => {
    if (hasStartedChat) {
      const url = new URL(window.location.href);
      url.searchParams.set('mode', 'advanced');
      window.history.replaceState({}, '', url.toString());
    }
  }, [hasStartedChat]);

  useEffect(() => {
    api.getAgentContext()
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data) {
          const agentId = data.agent_id ?? "cuga-default";
          setAgentLabel(agentId);
          setKnowledgeEnabled(data.knowledge_enabled ?? false);
          setAgentKnowledgeEnabled(data.agent_level_knowledge_enabled ?? false);
          setSessionKnowledgeEnabled(data.session_level_knowledge_enabled ?? false);
          api.setKnowledgeAgentId(agentId);
          const wfr = data.workspace_filesystem_root;
          if (typeof wfr === "string" && wfr.trim()) {
            setWorkspaceFilesystemRoot(wfr.trim());
          }
        } else {
          setKnowledgeEnabled(false);
          setAgentKnowledgeEnabled(false);
          setSessionKnowledgeEnabled(false);
        }
      })
      .catch(() => {
        setKnowledgeEnabled(false);
        setAgentKnowledgeEnabled(false);
        setSessionKnowledgeEnabled(false);
      });
  }, []);

  const { isTourActive, hasSeenTour, startTour, completeTour, skipTour, resetTour } = useTour();

  const handleVariablesUpdate = useCallback((variables: Record<string, any>, history: Array<any>) => {
    const currentVariablesCount = Object.keys(variables).length;
    const currentHistoryLength = history.length;

    setGlobalVariables(variables);
    setVariablesHistory(history);

    const hasNewVariables = currentVariablesCount > previousVariablesCount;
    const hasNewHistory = currentHistoryLength > previousHistoryLength;

    if (hasNewVariables || hasNewHistory) {
      setActiveTab("variables");
    }

    setPreviousVariablesCount(currentVariablesCount);
    setPreviousHistoryLength(currentHistoryLength);
  }, [previousVariablesCount, previousHistoryLength]);

  const handleMessageSent = useCallback((message: string) => {
    if (leftSidebarRef.current) {
      const title = message.length > 50 ? message.substring(0, 50) + "..." : message;
      leftSidebarRef.current.addConversation(title);
    }
    setActiveTab("conversations");
  }, []);

  const handleChatStarted = useCallback((started: boolean) => {
    setHasStartedChat(started);
  }, []);

  const tourSteps: TourStep[] = [
    {
      target: ".welcome-title",
      title: "Welcome to CUGA!",
      content: "CUGA is an intelligent digital agent that autonomously executes complex tasks through multi-agent orchestration, API integration, and code generation.",
      placement: "bottom",
      highlightPadding: 12,
    },
    {
      target: "#main-input_field",
      title: "Chat Input",
      content: "Type your requests here. You can ask CUGA to manage contacts, read files, send emails, or perform any complex task.",
      placement: "top",
      highlightPadding: 10,
    },
    {
      target: "#main-input_field",
      title: "File Tagging with @",
      content: "Type @ followed by a file name to tag files in your message. This allows CUGA to access and work with specific files from your workspace.",
      placement: "top",
      highlightPadding: 10,
    },
    {
      target: ".example-utterances-widget",
      title: "Try Example Queries",
      content: "Click any of these example queries to get started quickly. These demonstrate the types of tasks CUGA can handle.",
      placement: "top",
      highlightPadding: 12,
      beforeShow: () => {
        const input = document.getElementById("main-input_field");
        if (input) input.focus();
      },
    },
    {
      target: ".welcome-features",
      title: "Key Features",
      content: "CUGA offers multi-agent coordination, secure code execution, API integration, and smart memory to handle complex workflows.",
      placement: "top",
      highlightPadding: 12,
    },
  ];

  return (
    <ErrorBoundary>
      <div className={`app-layout ${!hasStartedChat ? 'welcome-mode' : ''}`}>
        {hasStartedChat && (
          <ConfigHeader
            onToggleLeftSidebar={() => setLeftSidebarCollapsed(!leftSidebarCollapsed)}
            onToggleKnowledge={() => setKnowledgePanelOpen(!knowledgePanelOpen)}
            knowledgeDocCount={knowledgeDocCount}
            knowledgeEnabled={knowledgeEnabled}
          />
        )}
        <div className="main-layout">
          {hasStartedChat && (
            <LeftSidebar
              globalVariables={globalVariables}
              variablesHistory={variablesHistory}
              selectedAnswerId={selectedAnswerId}
              onSelectAnswer={setSelectedAnswerId}
              isCollapsed={leftSidebarCollapsed}
              activeTab={activeTab}
              onTabChange={setActiveTab}
              leftSidebarRef={leftSidebarRef}
              onSelectConversation={setSelectedThreadId}
            />
          )}
          <div className="chat-container">
            <CustomChat
              onVariablesUpdate={handleVariablesUpdate}
              onMessageSent={handleMessageSent}
              onChatStarted={handleChatStarted}
              initialChatStarted={hasStartedChat}
              onThreadIdChange={setThreadId}
              externalThreadId={selectedThreadId}
              initialThreadId={threadId}
              sessionDocsVersion={sessionDocsVersion}
              onSessionDocsChanged={() => setSessionDocsVersion((v) => v + 1)}
              knowledgeEnabled={sessionKnowledgeEnabled}
              workspaceFilesystemRoot={workspaceFilesystemRoot}
            />
          </div>
          {hasStartedChat && (
            <KnowledgeSidePanel
              isOpen={knowledgePanelOpen}
              onToggle={() => setKnowledgePanelOpen(!knowledgePanelOpen)}
              threadId={threadId}
              sessionDocsVersion={sessionDocsVersion}
              onSessionDocsChanged={() => setSessionDocsVersion((v) => v + 1)}
              onDocCountChanged={setKnowledgeDocCount}
              knowledgeEnabled={knowledgeEnabled}
              agentKnowledgeEnabled={agentKnowledgeEnabled}
              sessionKnowledgeEnabled={sessionKnowledgeEnabled}
              agentLabel={agentLabel}
            />
          )}
        </div>
        {hasStartedChat && <StatusBar threadId={threadId} />}

        {hasStartedChat && <AdvancedTourButton />}

        {hasStartedChat && isTourActive && (
          <GuidedTour
            steps={tourSteps}
            isActive={isTourActive}
            onComplete={completeTour}
            onSkip={skipTour}
          />
        )}
      </div>
    </ErrorBoundary>
  );
}

export function BootstrapAgentic(contentRoot: HTMLElement) {
  console.log("Bootstrapping Agentic Chat in sidepanel");
  const root = createRoot(contentRoot);
  root.render(
      <App />
  );
}
