import React, { useCallback, useRef } from "react";
import {
  IconButton,
  TreeView,
  TreeNode,
  SkeletonText,
  ComposedModal,
  ModalHeader,
  ModalBody,
  ModalFooter,
  Button,
} from "@carbon/react";
import Markdown from "@carbon/ai-chat-components/es/react/markdown.js";
import {
  Folder,
  FolderOpen,
  DocumentBlank,
  Download,
  Upload,
  Renew,
} from "@carbon/icons-react";
import { JSON_UPLOAD_ACCEPT } from "./constants";
import type { FileNode } from "./types";
import type { WorkspacePanelState } from "./useWorkspacePanel";
import "./WorkspacePanelContent.css";

interface WorkspacePanelContentProps {
  panel: WorkspacePanelState;
  highlightedFile?: string | null;
  workspaceFilesystemRoot?: string;
  emptyMessage?: string;
  className?: string;
  showUploadControls?: boolean;
}

export function WorkspacePanelContent({
  panel,
  highlightedFile,
  workspaceFilesystemRoot = "cuga_workspace",
  emptyMessage = "No workspace files.",
  className = "",
  showUploadControls = true,
}: WorkspacePanelContentProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const renderFileNode = useCallback(
    (node: FileNode): React.ReactNode => {
      const isDir = node.type === "directory";
      const dirOpen = isDir && panel.workspaceExpandedDirs.has(node.path);
      const isHighlighted = highlightedFile === node.path;

      return (
        <TreeNode
          key={node.path}
          id={node.path}
          label={
            isDir ? (
              <span
                className={`chat-landing-workspace-tree-name${isHighlighted ? " chat-landing-workspace-tree-name--highlighted" : ""}`}
              >
                {node.name}
              </span>
            ) : (
              <span className="chat-landing-workspace-tree-row">
                <span
                  className={`chat-landing-workspace-tree-filename${isHighlighted ? " chat-landing-workspace-tree-filename--highlighted" : ""}`}
                  role="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    void panel.handleFileClick(node);
                  }}
                >
                  {node.name}
                </span>
                <IconButton
                  className="chat-landing-workspace-tree-download"
                  label={`Download ${node.name}`}
                  kind="ghost"
                  size="sm"
                  onClick={(event) => {
                    event.stopPropagation();
                    void panel.handleWorkspaceFileDownload(node);
                  }}
                >
                  <Download size={14} />
                </IconButton>
              </span>
            )
          }
          renderIcon={isDir ? (dirOpen ? FolderOpen : Folder) : DocumentBlank}
          isExpanded={isDir ? dirOpen : false}
          onToggle={
            isDir
              ? (first: unknown, second?: unknown) => panel.handleWorkspaceDirToggle(node.path, first, second)
              : undefined
          }
        >
          {isDir && node.children?.map((child) => renderFileNode(child))}
        </TreeNode>
      );
    },
    [highlightedFile, panel],
  );

  return (
    <>
      <div
        className={`chat-landing-workspace-panel${panel.workspaceDragOver ? " chat-landing-workspace-panel--drag-over" : ""} ${className}`.trim()}
        onDragEnter={panel.handleDragEnter}
        onDragLeave={panel.handleDragLeave}
        onDragOver={panel.handleDragOver}
        onDrop={panel.handleDrop}
      >
        {showUploadControls && (
          <div className="workspace-panel-content-toolbar">
            <input
              ref={fileInputRef}
              type="file"
              accept={JSON_UPLOAD_ACCEPT}
              multiple
              style={{ display: "none" }}
              onChange={panel.handleWorkspaceFileInputChange}
            />
            <IconButton
              label="Upload JSON files"
              kind="ghost"
              size="sm"
              onClick={() => fileInputRef.current?.click()}
            >
              <Upload size={16} />
            </IconButton>
            <IconButton
              label="Refresh workspace"
              kind="ghost"
              size="sm"
              onClick={() => void panel.fetchWorkspaceTree(true)}
            >
              <Renew size={16} />
            </IconButton>
          </div>
        )}

        {panel.error && (
          <div className="workspace-panel-content-error">
            <p>{panel.error}</p>
            <button type="button" onClick={() => void panel.fetchWorkspaceTree(true)}>
              Retry
            </button>
          </div>
        )}

        {panel.workspaceTreeLoading ? (
          <div style={{ padding: "1rem" }}>
            <SkeletonText paragraph lineCount={5} />
          </div>
        ) : panel.workspaceTree.length === 0 ? (
          <div className="workspace-panel-content-empty">
            <Folder size={32} style={{ opacity: 0.25, display: "block", margin: "0 auto 0.75rem" }} />
            {emptyMessage}
            <br />
            <span style={{ fontSize: "0.75rem" }}>
              Upload JSON files — they appear under {workspaceFilesystemRoot}/uploads/
            </span>
          </div>
        ) : (
          <TreeView label="Workspace" hideLabel className="chat-landing-workspace-tree">
            {panel.workspaceTree.map((node) => renderFileNode(node))}
          </TreeView>
        )}

        {panel.workspaceDragOver && (
          <div className="chat-landing-workspace-drag-overlay">Drop JSON files here to upload</div>
        )}
      </div>

      <ComposedModal
        open={!!panel.filePreview}
        onClose={panel.closeFilePreview}
        size="lg"
        isFullWidth
      >
        <ModalHeader title={panel.filePreview?.name ?? ""} buttonOnClick={panel.closeFilePreview} />
        <ModalBody hasScrollingContent className="chat-landing-file-modal-body">
          {panel.filePreview && (
            <div className="chat-landing-file-modal-markdown">
              <Markdown>
                {panel.filePreview.name.toLowerCase().endsWith(".md")
                  ? panel.filePreview.content
                  : `\`\`\`\n${panel.filePreview.content}\n\`\`\``}
              </Markdown>
            </div>
          )}
        </ModalBody>
        {panel.filePreview && (
          <ModalFooter className="chat-landing-file-modal-footer">
            <Button kind="secondary" renderIcon={Download} onClick={() => void panel.downloadFilePreview()}>
              Download
            </Button>
            <Button kind="primary" onClick={panel.closeFilePreview}>
              Close
            </Button>
          </ModalFooter>
        )}
      </ComposedModal>
    </>
  );
}
