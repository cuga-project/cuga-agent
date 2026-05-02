import { useState, Component, ErrorInfo, ReactNode, useCallback, useRef, useEffect } from "react";
import React from "react";
import { createRoot } from "react-dom/client";
import { CustomChat } from "./CustomChat";
import { ConfigHeader } from "./ConfigHeader";
import { LeftSidebar } from "./LeftSidebar";
import { StatusBar } from "./StatusBar";
import { WorkspacePanel } from "./WorkspacePanel";
import { KnowledgeSidePanel } from "./KnowledgeSidePanel";
import { FileAutocomplete } from "./FileAutocomplete";
import { GuidedTour, TourStep } from "./GuidedTour";
import { useTour } from "./useTour";
import { AdvancedTourButton } from "./AdvancedTourButton";
import { HelpCircle } from "lucide-react";
import * as api from "../../frontend/src/api";
import { randomUUID } from "./uuid";
import "./AppLayout.css";
import "./mockApi";
import "./workspaceThrottle"; // Enforce 3-second minimum interval between workspace API calls

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
  const [workspacePanelOpen, setWorkspacePanelOpen] = useState(true);
  const [knowledgePanelOpen, setKnowledgePanelOpen] = useState(false);
  const [knowledgeEnabled, setKnowledgeEnabled] = useState<boolean | null>(null);
  const [agentKnowledgeEnabled, setAgentKnowledgeEnabled] = useState<boolean | null>(null);
  const [sessionKnowledgeEnabled, setSessionKnowledgeEnabled] = useState<boolean | null>(null);
  const [agentLabel, setAgentLabel] = useState("this agent");
  const [sessionDocsVersion, setSessionDocsVersion] = useState(0);
  const [knowledgeDocCount, setKnowledgeDocCount] = useState(0);
  const [leftSidebarCollapsed, setLeftSidebarCollapsed] = useState(false);
  const [highlightedFile, setHighlightedFile] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"conversations" | "variables" | "savedflows">("conversations");
  const [previousVariablesCount, setPreviousVariablesCount] = useState(0);
  const [previousHistoryLength, setPreviousHistoryLength] = useState(0);
  const [threadId, setThreadId] = useState(() => randomUUID());
  const [selectedThreadId, setSelectedThreadId] = useState<string | undefined>(undefined);
  const [workspaceFilesystemRoot, setWorkspaceFilesystemRoot] = useState("cuga_workspace");
  const leftSidebarRef = useRef<{ addConversation: (title: string) => void } | null>(null);
  // Initialize hasStartedChat from URL query parameter immediately
  const [hasStartedChat, setHasStartedChat] = useState(() => {
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get('mode') === 'advanced';
  });

  // Update URL when entering advanced mode
  useEffect(() => {
    if (hasStartedChat) {
      const url = new URL(window.location.href);
      url.searchParams.set('mode', 'advanced');
      window.history.replaceState({}, '', url.toString());
    }
  }, [hasStartedChat]);
  
  // Fetch live agent context once on mount so chat respects the published config.
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

  // Handle variables updates from CustomChat
  const handleVariablesUpdate = useCallback((variables: Record<string, any>, history: Array<any>) => {
    console.log('[App] handleVariablesUpdate called');
    console.log('[App] Variables keys:', Object.keys(variables));
    console.log('[App] History length:', history.length);
    console.log('[App] Previous variables count:', previousVariablesCount);
    console.log('[App] Previous history length:', previousHistoryLength);

    const currentVariablesCount = Object.keys(variables).length;
    const currentHistoryLength = history.length;

    setGlobalVariables(variables);
    setVariablesHistory(history);

    // Only switch to variables tab when there's new data (more variables or longer history)
    const hasNewVariables = currentVariablesCount > previousVariablesCount;
    const hasNewHistory = currentHistoryLength > previousHistoryLength;

    if (hasNewVariables || hasNewHistory) {
      console.log('[App] Switching to variables tab - new data detected');
      setActiveTab("variables");
    }

    // Update previous counts
    setPreviousVariablesCount(currentVariablesCount);
    setPreviousHistoryLength(currentHistoryLength);
  }, [previousVariablesCount, previousHistoryLength]);

  // Handle message sent from CustomChat
  const handleMessageSent = useCallback((message: string) => {
    console.log('[App] handleMessageSent called with message:', message);
    console.log('[App] leftSidebarRef.current:', leftSidebarRef.current);
    // Add a new conversation to the left sidebar
    if (leftSidebarRef.current) {
      const title = message.length > 50 ? message.substring(0, 50) + "..." : message;
      console.log('[App] Calling addConversation with title:', title);
      leftSidebarRef.current.addConversation(title);
    } else {
      console.log('[App] leftSidebarRef.current is null');
    }
    // Switch to conversations tab to show the new conversation
    setActiveTab("conversations");
  }, []);

  // Handle chat started state
  const handleChatStarted = useCallback((started: boolean) => {
    setHasStartedChat(started);
  }, []);

  // Define tour steps
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

  // Disabled: Tour no longer starts automatically on welcome screen
  // Start tour automatically for first-time users after a delay
  // useEffect(() => {
  //   if (!hasSeenTour && !hasStartedChat) {
  //     const timer = setTimeout(() => {
  //       startTour();
  //     }, 1000);
  //     return () => clearTimeout(timer);
  //   }
  // }, [hasSeenTour, hasStartedChat, startTour]);

  return (
    <ErrorBoundary>
      <div className={`app-layout ${!hasStartedChat ? 'welcome-mode' : ''}`}>
        {hasStartedChat && (
          <ConfigHeader
            onToggleLeftSidebar={() => setLeftSidebarCollapsed(!leftSidebarCollapsed)}
            onToggleWorkspace={() => setWorkspacePanelOpen(!workspacePanelOpen)}
            onToggleKnowledge={() => setKnowledgePanelOpen(!knowledgePanelOpen)}
            leftSidebarCollapsed={leftSidebarCollapsed}
            workspaceOpen={workspacePanelOpen}
            knowledgeOpen={knowledgePanelOpen}
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
              onFileAutocompleteOpen={() => setWorkspacePanelOpen(true)}
              onFileHover={setHighlightedFile}
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
            <>
              <WorkspacePanel
                isOpen={workspacePanelOpen}
                onToggle={() => setWorkspacePanelOpen(!workspacePanelOpen)}
                highlightedFile={highlightedFile}
                threadId={threadId}
                workspaceFilesystemRoot={workspaceFilesystemRoot}
              />
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
            </>
          )}
        </div>
        {hasStartedChat && <StatusBar threadId={threadId} />}
        <FileAutocomplete
          onFileSelect={(path) => console.log("File selected:", path)}
          onAutocompleteOpen={() => setWorkspacePanelOpen(true)}
          onFileHover={setHighlightedFile}
          disabled={false}
          threadId={threadId}
        />
        
        {/* Tour help button - welcome screen - DISABLED */}
        {/* {!hasStartedChat && hasSeenTour && (
          <button
            className="tour-help-button"
            onClick={resetTour}
            title="Restart Tour"
          >
            <HelpCircle size={20} />
          </button>
        )} */}
        
        {/* Advanced tour button - after chat started */}
        {hasStartedChat && <AdvancedTourButton />}
        
        {/* Guided Tour - only show when chat has started (disabled on welcome screen) */}
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
  // Create a root for React to render into.
  console.log("Bootstrapping Agentic Chat in sidepanel");
  const root = createRoot(contentRoot);
  // Render the App component into the root.
  root.render(
      <App />
  );
}
