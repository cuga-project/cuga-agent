import React, { useState, useEffect, useCallback } from "react";
import { ConfigHeader } from "./ConfigHeader";
import CarbonChat from "./carbon-chat/CarbonChat";
import {
  IconButton,
  Tag,
  TreeView,
  TreeNode,
  Tabs,
  Tab,
  TabList,
  TabPanels,
  TabPanel,
  ComposedModal,
  ModalHeader,
  ModalBody,
  ModalFooter,
  SkeletonText,
  ToastNotification,
  Button,
} from "@carbon/react";
import Markdown from "@carbon/ai-chat-components/es/react/markdown.js";
import {
  Add,
  TrashCan,
  Folder,
  FolderOpen,
  Debug,
  Application,
  ChevronRight,
  DocumentBlank,
  Chat,
  Time,
  SidePanelOpen,
  SidePanelClose,
  ChevronDown,
  ChevronUp,
  Download,
} from "@carbon/icons-react";
import "./ChatLanding.css";

// ─── Types ────────────────────────────────────────────────────────────────────

interface ConversationThread {
  thread_id: string;
  latest_version: number;
  first_message: string;
  updated_at: string;
}

interface AppTool {
  name: string;
  description: string;
}

interface AppConfig {
  appName: string;
  tools: AppTool[];
}

interface WorkspaceChild {
  label: string;
  type: "folder" | "file";
}

interface WorkspaceFolder {
  path: string;
  label: string;
  readOnly: boolean;
  children?: WorkspaceChild[];
}

interface FileNode {
  name: string;
  path: string;
  type: "file" | "directory";
  children?: FileNode[];
}

interface AgentConfig {
  name: string;
  description: string;
  configVersion?: number | string | null;
  apps: AppConfig[];
  workspaceFolders: WorkspaceFolder[];
}

interface HomescreenConfig {
  isOn?: boolean;
  greeting?: string;
  starters?: string[];
}

// ─── Mock data ────────────────────────────────────────────────────────────────

const MOCK_AGENT_CONFIG: AgentConfig = {
  name: "CUGA Default Agent",
  description: "A general-purpose assistant with file-system access and web search capabilities.",
  apps: [
    {
      appName: "File System",
      tools: [
        { name: "read_file", description: "Read contents of a file" },
        { name: "write_file", description: "Write or update a file" },
        { name: "list_directory", description: "List files in a directory" },
        { name: "delete_file", description: "Delete a file permanently" },
      ],
    },
    {
      appName: "Web Search",
      tools: [
        { name: "web_search", description: "Search the web for information" },
        { name: "fetch_url", description: "Fetch and parse a web page" },
      ],
    },
    {
      appName: "Code Execution",
      tools: [
        { name: "run_python", description: "Execute a Python snippet" },
        { name: "run_bash", description: "Execute a Bash command" },
      ],
    },
  ],
  workspaceFolders: [
    {
      path: "/workspace/project-a",
      label: "project-a",
      readOnly: false,
      children: [
        { label: "src", type: "folder" },
        { label: "tests", type: "folder" },
        { label: "README.md", type: "file" },
        { label: "package.json", type: "file" },
      ],
    },
    {
      path: "/workspace/shared-docs",
      label: "shared-docs",
      readOnly: true,
      children: [
        { label: "specs", type: "folder" },
        { label: "guidelines.md", type: "file" },
      ],
    },
  ],
};

// ─── Responsive breakpoints ───────────────────────────────────────────────────

const BP_HIDE_RIGHT = 1100; // px — hide right panel below this
const BP_HIDE_LEFT = 768; // px — hide left panel below this

// ─── Helpers ──────────────────────────────────────────────────────────────────

const formatTimestamp = (isoString: string): string => {
  const utcString = isoString.endsWith("Z") ? isoString : `${isoString}Z`;
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
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

const truncateText = (text: string, maxLength: number = 100): string => {
  if (text.length <= maxLength) return text;
  return text.substring(0, maxLength) + "...";
};

const TEXT_EXTENSIONS = [".txt", ".md", ".json", ".yaml", ".yml", ".log", ".csv", ".html", ".css", ".js", ".ts", ".py"];

// ─── Inline style constants ───────────────────────────────────────────────────

// Glass / frosted-transparent panel base — floats over the chat
const panelStyle = (side: "left" | "right", headerH: number, width: string, visible: boolean): React.CSSProperties => ({
  position: "fixed",
  top: headerH,
  bottom: 0,
  [side]: 0,
  width,
  zIndex: 200,
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
  // Transparent glass effect
  background: "rgba(var(--cds-background-rgb, 255,255,255), 0.55)",
  backdropFilter: "blur(12px)",
  WebkitBackdropFilter: "blur(12px)",
  // IBM Carbon border standards - using subtle border for minimal visibility
  borderRight: side === "left" ? "1px solid var(--cds-border-subtle-01, rgba(0, 0, 0, 0.1))" : undefined,
  borderLeft: side === "right" ? "1px solid var(--cds-border-subtle-01, rgba(0, 0, 0, 0.1))" : undefined,
  boxShadow: side === "left"
    ? "1px 0 4px rgba(0, 0, 0, 0.05)"
    : "-1px 0 4px rgba(0, 0, 0, 0.05)",
  // Slide in/out
  transform: visible ? "translateX(0)" : side === "left" ? "translateX(-100%)" : "translateX(100%)",
  transition: "transform 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
  pointerEvents: visible ? "auto" : "none",
});

const panelHeader: React.CSSProperties = {
  padding: "1.5rem 1rem 1rem",
  borderBottom: "1px solid var(--cds-border-subtle-01)",
  flexShrink: 0,
  background: "transparent",
};

// ─── Component ────────────────────────────────────────────────────────────────

const HEADER_HEIGHT = 48; // Carbon shell header default
const LEFT_W = "22rem";
const RIGHT_W = "26rem";

export function ChatLanding() {
  const [windowW, setWindowW] = useState(window.innerWidth);
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [threads, setThreads] = useState<ConversationThread[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [agentConfig, setAgentConfig] = useState<AgentConfig>(MOCK_AGENT_CONFIG);
  const [homescreenConfig, setHomescreenConfig] = useState<HomescreenConfig | undefined>(undefined);
  const [configLoading, setConfigLoading] = useState(true);
  const [toastNotifications, setToastNotifications] = useState<Array<{ id: string; kind: "error" | "info" | "success" | "warning"; title: string; subtitle: string }>>([]);
  const [expandedApps, setExpandedApps] = useState<Set<string>>(new Set());
  const [workspaceTree, setWorkspaceTree] = useState<FileNode[]>([]);
  const [workspaceTreeLoading, setWorkspaceTreeLoading] = useState(true);
  const [fileModal, setFileModal] = useState<{ path: string; content: string; name: string } | null>(null);

  // ── Responsive: auto-collapse on small screens ──────────────────────────────
  useEffect(() => {
    const onResize = () => {
      const w = window.innerWidth;
      setWindowW(w);
      if (w < BP_HIDE_LEFT) setLeftOpen(false);
      if (w < BP_HIDE_RIGHT) setRightOpen(false);
      if (w >= BP_HIDE_LEFT && w >= BP_HIDE_RIGHT) {
        setLeftOpen(true);
        setRightOpen(true);
      }
    };
    window.addEventListener("resize", onResize);
    onResize(); // run once on mount
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const canShowLeft = windowW >= BP_HIDE_LEFT;
  const canShowRight = windowW >= BP_HIDE_RIGHT;

  // Toast notification helpers
  const addToast = useCallback((kind: "error" | "info" | "success" | "warning", title: string, subtitle: string) => {
    const id = `toast-${Date.now()}-${Math.random()}`;
    setToastNotifications((prev) => [...prev, { id, kind, title, subtitle }]);
    setTimeout(() => {
      setToastNotifications((prev) => prev.filter((t) => t.id !== id));
    }, 5000);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToastNotifications((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // ── Thread helpers ──────────────────────────────────────────────────────────
  const refreshThreads = useCallback(async () => {
    try {
      const res = await fetch("/api/conversation-threads?agent_id=cuga-default&user_id=default_user");
      if (res.ok) setThreads((await res.json()).threads || []);
    } catch (err) {
      console.error("Error fetching threads:", err);
    }
  }, []);

  const handleThreadChange = useCallback(
    async (threadId: string) => {
      setActiveThreadId(threadId);
      if (threadId !== selectedThreadId) {
        setSelectedThreadId(threadId);
        setTimeout(refreshThreads, 500);
      }
    },
    [selectedThreadId, refreshThreads],
  );

  const handleRemoveAll = async () => {
    if (!window.confirm("Remove all conversations? This cannot be undone.")) return;
    try {
      await Promise.all(
        threads.map((t) =>
          fetch(`/api/conversations/${t.thread_id}?agent_id=cuga-default&user_id=default_user`, { method: "DELETE" }),
        ),
      );
      setThreads([]);
      setSelectedThreadId(null);
      setActiveThreadId(null);
    } catch (err) {
      console.error(err);
      alert("Failed to remove conversations.");
    }
  };

  // Fetch agent configuration
  useEffect(() => {
    (async () => {
      try {
        const agentId = "cuga-default";
        const isDraft = false; // Use published config for chat landing
        
        const [contextRes, toolsListRes, manageRes] = await Promise.all([
          fetch("/api/agent/context"),
          fetch(`/api/tools/list?agent_id=${agentId}&draft=${isDraft ? "1" : "0"}`),
          fetch("/api/manage/config"),
        ]);

        let agentName = "CUGA Default Agent";
        let agentDescription = "A general-purpose assistant with configured tools and workspace access.";
        let configVersion: number | string | null = null;

        if (contextRes.ok) {
          const contextData = await contextRes.json();
          agentName = contextData.agent_id || agentName;
          configVersion = contextData.config_version ?? null;
        }

        // Get tools from tools/list endpoint and group by app
        let apps: AppConfig[] = MOCK_AGENT_CONFIG.apps;
        if (toolsListRes.ok) {
          const toolsData = await toolsListRes.json();
          const tools = toolsData.tools || [];
          
          if (tools.length > 0) {
            // Group tools by their app field
            const toolsByApp = new Map<string, AppTool[]>();
            
            tools.forEach((tool: any) => {
              const appName = tool.app || "Unknown App";
              if (!toolsByApp.has(appName)) {
                toolsByApp.set(appName, []);
              }
              toolsByApp.get(appName)!.push({
                name: tool.name,
                description: tool.description || "No description available",
              });
            });
            
            // Convert map to apps array
            apps = [];
            toolsByApp.forEach((tools, appName) => {
              apps.push({
                appName,
                tools,
              });
            });
          }
        } else {
          const errorMsg = `Failed to load tools list (${toolsListRes.status} ${toolsListRes.statusText})`;
          addToast("warning", "Tools Load Warning", errorMsg);
        }

        if (manageRes.ok) {
          const manageData = await manageRes.json();
          const hs = manageData.config?.homescreen;
          if (hs && typeof hs === "object") {
            setHomescreenConfig({
              isOn: hs.isOn ?? true,
              greeting: hs.greeting,
              starters: Array.isArray(hs.starters) ? hs.starters.slice(0, 4) : undefined,
            });
          }
        }

        const config: AgentConfig = {
          name: agentName,
          description: agentDescription,
          configVersion,
          apps,
          workspaceFolders: MOCK_AGENT_CONFIG.workspaceFolders, // TODO: Get from API if available
        };
        
        setAgentConfig(config);
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : "Network error loading agent configuration";
        addToast("error", "Configuration Load Error", errorMsg);
        console.error("Error fetching agent config:", err);
        // Keep using MOCK_AGENT_CONFIG as fallback
      } finally {
        setConfigLoading(false);
      }
    })();
  }, [addToast]);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/conversation-threads?agent_id=cuga-default&user_id=default_user");
        if (!res.ok) {
          const errorMsg = `Failed to load conversation threads (${res.status} ${res.statusText})`;
          addToast("warning", "Threads Load Warning", errorMsg);
          console.error(errorMsg);
        } else {
          setThreads((await res.json()).threads || []);
        }
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : "Network error loading conversation threads";
        addToast("error", "Threads Load Error", errorMsg);
        console.error(err);
      } finally {
        setLoading(false);
      }
    })();
  }, [addToast]);

  const fetchWorkspaceTree = useCallback(async () => {
    try {
      const res = await fetch("/api/workspace/tree");
      if (res.ok) {
        const data = await res.json();
        setWorkspaceTree(data.tree || []);
      }
    } catch (err) {
      console.error("Error fetching workspace tree:", err);
    } finally {
      setWorkspaceTreeLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchWorkspaceTree();
    const interval = setInterval(fetchWorkspaceTree, 2500);
    return () => clearInterval(interval);
  }, [fetchWorkspaceTree]);

  const totalTools = agentConfig.apps.reduce((s, a) => s + a.tools.length, 0);

  const handleFileClick = useCallback(async (node: FileNode) => {
    if (node.type !== "file") return;
    const isTextFile = TEXT_EXTENSIONS.some((ext) => node.name.toLowerCase().endsWith(ext));
    if (!isTextFile) {
      addToast("info", "Preview not available", "Only text and markdown files can be previewed.");
      return;
    }
    try {
      const res = await fetch(`/api/workspace/file?path=${encodeURIComponent(node.path)}`);
      if (res.ok) {
        const data = await res.json();
        setFileModal({ path: node.path, content: data.content, name: node.name });
      } else {
        addToast("error", "Failed to load file", res.statusText);
      }
    } catch (err) {
      addToast("error", "Error loading file", err instanceof Error ? err.message : "Unknown error");
    }
  }, [addToast]);

  const renderFileNode = useCallback(
    (node: FileNode) => (
      <TreeNode
        key={node.path}
        id={node.path}
        label={
          <span
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.5rem",
              fontSize: "0.8125rem",
              color: "var(--cds-text-primary)",
              cursor: node.type === "file" ? "pointer" : "default",
            }}
            role={node.type === "file" ? "button" : undefined}
            onClick={node.type === "file" ? (e) => { e.stopPropagation(); handleFileClick(node); } : undefined}
          >
            {node.name}
          </span>
        }
        renderIcon={node.type === "directory" ? FolderOpen : DocumentBlank}
        isExpanded={node.type === "directory"}
      >
        {node.type === "directory" && node.children?.map((child) => renderFileNode(child))}
      </TreeNode>
    ),
    [handleFileClick],
  );

  const handleToggleLeft = () => canShowLeft && setLeftOpen((v) => !v);
  const handleToggleWorkspace = () => canShowRight && setRightOpen((v) => !v);

  return (
    <div className="chat-landing">
      <ConfigHeader
        onToggleLeftSidebar={handleToggleLeft}
        onToggleWorkspace={handleToggleWorkspace}
        leftSidebarCollapsed={!leftOpen}
        workspaceOpen={rightOpen}
      />

      {/* ── Full-width chat — panels float on top ─────────────────────────── */}
      <div className="chat-content-area" style={{ position: "relative", height: `calc(100vh - ${HEADER_HEIGHT}px)` }}>
        <CarbonChat
          contained={true}
          threadId={selectedThreadId}
          isReadonly={selectedThreadId != null && selectedThreadId !== activeThreadId}
          onThreadChange={handleThreadChange}
          homescreen={homescreenConfig}
        />
      </div>

      {/* ══════════════════════════════════════════════════════════════════════
          LEFT PANEL — fixed, transparent, slides over chat
          ══════════════════════════════════════════════════════════════════════ */}
      {canShowLeft && (
        <div style={panelStyle("left", HEADER_HEIGHT, LEFT_W, leftOpen)}>
          {/* Header */}
          <div style={panelHeader}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.625rem" }}>
                <Chat size={20} style={{ color: "var(--cds-interactive)", flexShrink: 0 }} />
                <div>
                  <p style={{ fontSize: "0.875rem", fontWeight: 600, margin: 0, color: "var(--cds-text-primary)" }}>
                    Conversations
                  </p>
                  <p style={{ fontSize: "0.6875rem", color: "var(--cds-text-secondary)", margin: "0.2rem 0 0" }}>
                    {loading ? "Loading…" : `${threads.length} thread${threads.length !== 1 ? "s" : ""}`}
                  </p>
                </div>
              </div>

              <div style={{ display: "flex", gap: "0.25rem", alignItems: "center" }}>
                <IconButton
                  label="New conversation"
                  kind="ghost"
                  size="sm"
                  onClick={() => {
                    setSelectedThreadId(null);
                    setActiveThreadId(null);
                  }}
                >
                  <Add />
                </IconButton>
                <IconButton
                  label="Remove all"
                  kind="ghost"
                  size="sm"
                  onClick={handleRemoveAll}
                  disabled={threads.length === 0}
                >
                  <TrashCan />
                </IconButton>
                <IconButton label="Close panel" kind="ghost" size="sm" onClick={() => setLeftOpen(false)}>
                  <SidePanelClose />
                </IconButton>
              </div>
            </div>

            {selectedThreadId && (
              <div style={{ marginTop: "0.5rem", display: "flex", alignItems: "center", gap: "0.375rem" }}>
                <Time size={12} style={{ color: "var(--cds-text-secondary)" }} />
                <code style={{ fontSize: "0.6rem", color: "var(--cds-text-secondary)", fontFamily: "monospace" }}>
                  {selectedThreadId}
                </code>
              </div>
            )}
          </div>

          {/* Thread list */}
          <div style={{ flex: 1, overflowY: "auto" }}>
            {loading ? (
              <div style={{ padding: "1rem" }}>
                <SkeletonText paragraph lineCount={5} />
              </div>
            ) : threads.length === 0 ? (
              <div
                style={{
                  padding: "3rem 1rem",
                  textAlign: "center",
                  color: "var(--cds-text-secondary)",
                  fontSize: "0.8125rem",
                }}
              >
                <Chat size={32} style={{ opacity: 0.25, display: "block", margin: "0 auto 0.75rem" }} />
                No conversations yet.
                <br />
                Start a new chat to begin.
              </div>
            ) : (
              threads.map((thread) => {
                const isActive = selectedThreadId === thread.thread_id;
                return (
                  <button
                    key={thread.thread_id}
                    onClick={() => setSelectedThreadId(thread.thread_id)}
                    style={{
                      display: "block",
                      width: "100%",
                      textAlign: "left",
                      background: isActive ? "rgba(var(--cds-interactive-rgb, 15,98,254), 0.08)" : "transparent",
                      border: "none",
                      borderLeft: isActive ? "3px solid var(--cds-interactive)" : "3px solid transparent",
                      borderBottom: "1px solid var(--cds-border-subtle-00)",
                      padding: "0.75rem 1rem",
                      cursor: "pointer",
                      transition: "background 0.12s",
                    }}
                    onMouseEnter={(e) => {
                      if (!isActive) (e.currentTarget as HTMLButtonElement).style.background = "rgba(0,0,0,0.04)";
                    }}
                    onMouseLeave={(e) => {
                      if (!isActive) (e.currentTarget as HTMLButtonElement).style.background = "transparent";
                    }}
                  >
                    <p
                      style={{
                        margin: 0,
                        fontSize: "0.8125rem",
                        fontWeight: isActive ? 600 : 400,
                        color: "var(--cds-text-primary)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {thread.first_message || "Untitled conversation"}
                    </p>
                    <div style={{ marginTop: "0.25rem", display: "flex", alignItems: "center", gap: "0.375rem" }}>
                      <Time size={10} style={{ color: "var(--cds-text-secondary)" }} />
                      <span style={{ fontSize: "0.6875rem", color: "var(--cds-text-secondary)" }}>
                        {formatTimestamp(thread.updated_at)}
                      </span>
                      {isActive && (
                        <Tag type="blue" size="sm" style={{ marginLeft: "auto" }}>
                          active
                        </Tag>
                      )}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
          RIGHT PANEL — fixed, transparent, slides over chat
          ══════════════════════════════════════════════════════════════════════ */}
      {canShowRight && (
        <div style={panelStyle("right", HEADER_HEIGHT, RIGHT_W, rightOpen)}>
          {/* Agent identity header */}
          <div style={panelHeader}>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
              <div style={{ display: "flex", alignItems: "flex-start", gap: "0.625rem" }}>
                <Application
                  size={20}
                  style={{ flexShrink: 0, marginTop: "0.125rem", color: "var(--cds-interactive)" }}
                />
                <div>
                  <p style={{ fontSize: "0.875rem", fontWeight: 600, margin: 0, color: "var(--cds-text-primary)" }}>
                    {agentConfig.name}
                  </p>
                  <p
                    style={{
                      fontSize: "0.6875rem",
                      color: "var(--cds-text-secondary)",
                      margin: "0.2rem 0 0",
                      lineHeight: 1.4,
                    }}
                  >
                    {agentConfig.description}
                  </p>
                  {agentConfig.configVersion != null && (
                    <div style={{ marginTop: "0.5rem", display: "flex", alignItems: "center", gap: "0.375rem" }}>
                      <Tag type="gray" size="sm">
                        Config v{agentConfig.configVersion}
                      </Tag>
                    </div>
                  )}
                </div>
              </div>
              <IconButton label="Close panel" kind="ghost" size="sm" onClick={() => setRightOpen(false)}>
                <SidePanelClose />
              </IconButton>
            </div>
          </div>

          {/* Tabs */}
          <Tabs style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <TabList aria-label="Agent panel tabs" style={{ flexShrink: 0 }}>
              <Tab>
                <span style={{ display: "flex", alignItems: "center", gap: "0.375rem" }}>
                  <Debug size={14} />
                  Configuration
                  <Tag type="teal" size="sm" style={{ marginLeft: "0.25rem" }}>
                    {totalTools}
                  </Tag>
                </span>
              </Tab>
              <Tab>
                <span style={{ display: "flex", alignItems: "center", gap: "0.375rem" }}>
                  <Folder size={14} />
                  Workspace
                  <Tag type="blue" size="sm" style={{ marginLeft: "0.25rem" }}>
                    {workspaceTree.length}
                  </Tag>
                </span>
              </Tab>
            </TabList>

            <TabPanels style={{ flex: 1, overflowY: "auto" }}>
              {/* ── Configuration tab ── */}
              <TabPanel style={{ padding: "1rem", overflowY: "scroll" }}>
                <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                  {agentConfig.apps.map((app) => {
                    const isExpanded = expandedApps.has(app.appName);
                    const visibleTools = isExpanded ? app.tools : app.tools.slice(0, 5);
                    const hasMore = app.tools.length > 5;
                    
                    return (
                      <div
                        key={app.appName}
                        style={{
                          border: "1px solid var(--cds-border-subtle-01)",
                          borderRadius: "4px",
                          overflow: "hidden",
                          background: "rgba(var(--cds-background-rgb, 255,255,255), 0.4)",
                        }}
                      >
                        <div
                          style={{
                            background: "rgba(var(--cds-layer-02-rgb, 244,244,244), 0.6)",
                            padding: "0.5rem 0.75rem",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            borderBottom: "1px solid var(--cds-border-subtle-01)",
                          }}
                        >
                          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                            <Application size={14} style={{ color: "var(--cds-interactive)" }} />
                            <span style={{ fontSize: "0.8125rem", fontWeight: 600, color: "var(--cds-text-primary)" }}>
                              {app.appName}
                            </span>
                          </div>
                          <Tag type="teal" size="sm">
                            {app.tools.length} tool{app.tools.length !== 1 ? "s" : ""}
                          </Tag>
                        </div>

                        <div style={{ padding: "0.25rem 0" }}>
                          {visibleTools.map((tool, idx) => (
                            <div
                              key={tool.name}
                              style={{
                                padding: "0.5rem 0.75rem",
                                borderBottom:
                                  idx < visibleTools.length - 1 || hasMore ? "1px solid var(--cds-border-subtle-00)" : "none",
                                display: "flex",
                                alignItems: "flex-start",
                                gap: "0.5rem",
                              }}
                            >
                              <ChevronRight
                                size={12}
                                style={{ flexShrink: 0, marginTop: "0.2rem", color: "var(--cds-interactive)" }}
                              />
                              <div>
                                <code
                                  style={{
                                    fontSize: "0.75rem",
                                    fontWeight: 600,
                                    color: "var(--cds-text-primary)",
                                    display: "block",
                                  }}
                                >
                                  {tool.name}
                                </code>
                                <span
                                  style={{
                                    fontSize: "0.6875rem",
                                    color: "var(--cds-text-secondary)",
                                    lineHeight: 1.4,
                                    display: "block",
                                    wordBreak: "break-word"
                                  }}
                                  title={tool.description}
                                >
                                  {truncateText(tool.description, 120)}
                                </span>
                              </div>
                            </div>
                          ))}
                          
                          {hasMore && (
                            <div style={{ padding: "0.5rem 0.75rem", display: "flex", justifyContent: "center" }}>
                              <Button
                                kind="ghost"
                                size="sm"
                                renderIcon={isExpanded ? ChevronUp : ChevronDown}
                                onClick={() => {
                                  setExpandedApps((prev) => {
                                    const next = new Set(prev);
                                    if (isExpanded) {
                                      next.delete(app.appName);
                                    } else {
                                      next.add(app.appName);
                                    }
                                    return next;
                                  });
                                }}
                              >
                                {isExpanded ? "Show less" : `Show ${app.tools.length - 5} more`}
                              </Button>
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </TabPanel>

              {/* ── Workspace tab ── */}
              <TabPanel style={{ padding: "1rem", overflowY: "scroll" }}>
                {workspaceTreeLoading ? (
                  <div style={{ padding: "1rem" }}>
                    <SkeletonText paragraph lineCount={5} />
                  </div>
                ) : workspaceTree.length === 0 ? (
                  <div
                    style={{
                      padding: "2rem 1rem",
                      textAlign: "center",
                      color: "var(--cds-text-secondary)",
                      fontSize: "0.8125rem",
                    }}
                  >
                    <Folder size={32} style={{ opacity: 0.25, display: "block", margin: "0 auto 0.75rem" }} />
                    No workspace files.
                  </div>
                ) : (
                  <TreeView label="Workspace" hideLabel>
                    {workspaceTree.map((node) => renderFileNode(node))}
                  </TreeView>
                )}
              </TabPanel>
            </TabPanels>
          </Tabs>
        </div>
      )}

      {/* ── Floating re-open buttons when panels are closed ──────────────── */}
      {canShowLeft && !leftOpen && (
        <button
          onClick={() => setLeftOpen(true)}
          title="Open conversations"
          style={{
            position: "fixed",
            top: HEADER_HEIGHT + 12,
            left: 8,
            zIndex: 201,
            background: "rgba(var(--cds-background-rgb, 255,255,255), 0.7)",
            backdropFilter: "blur(8px)",
            border: "1px solid var(--cds-border-subtle-01)",
            borderRadius: "4px",
            padding: "6px",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            color: "var(--cds-text-primary)",
          }}
        >
          <SidePanelOpen size={16} />
        </button>
      )}

      {canShowRight && !rightOpen && (
        <button
          onClick={() => setRightOpen(true)}
          title="Open agent panel"
          style={{
            position: "fixed",
            top: HEADER_HEIGHT + 12,
            right: 8,
            zIndex: 201,
            background: "rgba(var(--cds-background-rgb, 255,255,255), 0.7)",
            backdropFilter: "blur(8px)",
            border: "1px solid var(--cds-border-subtle-01)",
            borderRadius: "4px",
            padding: "6px",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            color: "var(--cds-text-primary)",
          }}
        >
          <SidePanelOpen size={16} style={{ transform: "scaleX(-1)" }} />
        </button>
      )}

      <ComposedModal
        open={!!fileModal}
        onClose={() => setFileModal(null)}
        size="lg"
        isFullWidth
      >
        <ModalHeader
          title={fileModal?.name ?? ""}
          buttonOnClick={() => setFileModal(null)}
        />
        <ModalBody hasScrollingContent className="chat-landing-file-modal-body">
          {fileModal && (
            <div className="chat-landing-file-modal-markdown">
              <Markdown>
                {fileModal.name.toLowerCase().endsWith(".md")
                  ? fileModal.content
                  : `\`\`\`\n${fileModal.content}\n\`\`\``}
              </Markdown>
            </div>
          )}
        </ModalBody>
        {fileModal && (
          <ModalFooter className="chat-landing-file-modal-footer">
            <Button
              kind="secondary"
              renderIcon={Download}
              onClick={async () => {
                try {
                  const res = await fetch(`/api/workspace/download?path=${encodeURIComponent(fileModal.path)}`);
                  if (res.ok) {
                    const blob = await res.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = fileModal.name;
                    a.click();
                    URL.revokeObjectURL(url);
                  }
                } catch (err) {
                  addToast("error", "Download failed", err instanceof Error ? err.message : "Unknown error");
                }
              }}
            >
              Download
            </Button>
            <Button kind="primary" onClick={() => setFileModal(null)}>
              Close
            </Button>
          </ModalFooter>
        )}
      </ComposedModal>

      {/* Toast Notifications */}
      <div
        style={{
          position: "fixed",
          top: "3rem",
          right: "1rem",
          zIndex: 9999,
          display: "flex",
          flexDirection: "column",
          gap: "0.5rem",
          maxWidth: "400px",
        }}
      >
        {toastNotifications.map((toast) => (
          <ToastNotification
            key={toast.id}
            kind={toast.kind}
            title={toast.title}
            subtitle={toast.subtitle}
            timeout={5000}
            onClose={() => removeToast(toast.id)}
            lowContrast
          />
        ))}
      </div>
    </div>
  );
}
