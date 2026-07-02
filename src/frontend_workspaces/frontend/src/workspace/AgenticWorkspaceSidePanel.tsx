import React from "react";
import { ChevronRight, Folder, Info } from "lucide-react";
import { useWorkspacePanel } from "./useWorkspacePanel";
import { WorkspacePanelContent } from "./WorkspacePanelContent";
import "./AgenticWorkspaceSidePanel.css";

interface AgenticWorkspaceSidePanelProps {
  isOpen: boolean;
  onToggle: () => void;
  highlightedFile?: string | null;
  threadId?: string;
  workspaceFilesystemRoot?: string;
}

export function AgenticWorkspaceSidePanel({
  isOpen,
  onToggle,
  highlightedFile,
  threadId,
  workspaceFilesystemRoot = "cuga_workspace",
}: AgenticWorkspaceSidePanelProps) {
  const panel = useWorkspacePanel({
    threadId,
    enabled: isOpen,
    pollIntervalMs: 15000,
  });

  return (
    <>
      <div className={`agentic-workspace-side-panel ${isOpen ? "open" : "closed"}`}>
        <div className="agentic-workspace-side-panel-header">
          <div className="agentic-workspace-side-panel-title">
            <Folder size={18} />
            <span>Workspace</span>
            <div className="agentic-workspace-info-tooltip-wrapper">
              <Info size={16} className="info-icon" />
              <div className="agentic-workspace-info-tooltip">
                Workspace files for this chat. Paths mirror the agent: <code>{workspaceFilesystemRoot}</code>. Tag
                files using <code>@</code>
              </div>
            </div>
          </div>
          <button
            type="button"
            className="agentic-workspace-close-btn"
            onClick={onToggle}
            title="Close"
          >
            <ChevronRight size={18} />
          </button>
        </div>

        <div className="agentic-workspace-side-panel-content">
          <WorkspacePanelContent
            panel={panel}
            highlightedFile={highlightedFile}
            workspaceFilesystemRoot={workspaceFilesystemRoot}
            emptyMessage="Workspace is empty"
          />
        </div>
      </div>

      {!isOpen && (
        <button
          type="button"
          className="agentic-workspace-toggle-btn"
          onClick={onToggle}
          title="Open Workspace"
        >
          <Folder size={18} />
        </button>
      )}
    </>
  );
}
