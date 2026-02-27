import React, { useState, useEffect, useCallback } from "react";
import { Folder, File, ChevronRight, ChevronDown, X, Download, FileText, RefreshCw, Trash2, Info } from "lucide-react";
import { apiFetch } from "../../frontend/src/api";
import "./WorkspacePanel.css";

interface FileNode {
  name: string;
  path: string;
  type: "file" | "directory";
  children?: FileNode[];
}

interface WorkspacePanelProps {
  isOpen: boolean;
  onToggle: () => void;
  highlightedFile?: string | null;
}

export function WorkspacePanel({ isOpen, onToggle, highlightedFile }: WorkspacePanelProps) {
  const [fileTree, setFileTree] = useState<FileNode[]>([]);
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());
  const [selectedFile, setSelectedFile] = useState<{ path: string; content: string; name: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState<{ file: FileNode; isOpen: boolean } | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);

  const loadWorkspaceTree = useCallback(async () => {
    try {
      setError(null);
      const { workspaceService } = await import('./workspaceService');
      const data = await workspaceService.getWorkspaceTree();
      setFileTree(data.tree || []);
    } catch (err) {
      console.error("Error loading workspace:", err);
      setError("Error loading workspace");
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      loadWorkspaceTree();
      // Set up polling for live updates
      const interval = setInterval(loadWorkspaceTree, 15000);
      return () => clearInterval(interval);
    }
  }, [isOpen, loadWorkspaceTree]);

  const toggleFolder = (path: string) => {
    const newExpanded = new Set(expandedFolders);
    if (newExpanded.has(path)) {
      newExpanded.delete(path);
    } else {
      newExpanded.add(path);
    }
    setExpandedFolders(newExpanded);
  };

  const handleFileClick = async (file: FileNode) => {
    if (file.type === "directory") {
      toggleFolder(file.path);
      return;
    }

    // Check if it's a text or markdown file
    const textExtensions = ['.txt', '.md', '.json', '.yaml', '.yml', '.log', '.csv', '.html', '.css', '.js', '.ts', '.py'];
    const isTextFile = textExtensions.some(ext => file.name.toLowerCase().endsWith(ext));

    if (!isTextFile) {
      alert("Only text and markdown files can be previewed");
      return;
    }

    setLoading(true);
    try {
      const response = await apiFetch(`/api/workspace/file?path=${encodeURIComponent(file.path)}`);
      if (response.ok) {
        const data = await response.json();
        setSelectedFile({
          path: file.path,
          content: data.content,
          name: file.name
        });
      } else {
        alert("Failed to load file");
      }
    } catch (err) {
      console.error("Error loading file:", err);
      alert("Error loading file");
    } finally {
      setLoading(false);
    }
  };

  const handleDownload = async (filePath: string, fileName: string) => {
    try {
      const response = await apiFetch(`/api/workspace/download?path=${encodeURIComponent(filePath)}`);
      if (response.ok) {
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = fileName;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
      } else {
        alert("Failed to download file");
      }
    } catch (err) {
      console.error("Error downloading file:", err);
      alert("Error downloading file");
    }
  };

  const handleDeleteClick = (file: FileNode) => {
    // Disabled: Delete functionality is not available
    // setDeleteConfirmation({ file, isOpen: true });
  };

  const handleDeleteConfirm = async () => {
    if (!deleteConfirmation) return;

    const { file } = deleteConfirmation;
    setLoading(true);

    try {
      const response = await apiFetch(`/api/workspace/file?path=${encodeURIComponent(file.path)}`, {
        method: 'DELETE'
      });

      if (response.ok) {
        // Refresh the workspace tree after successful deletion
        await loadWorkspaceTree();
        // Close any open file viewer if the deleted file was being viewed
        if (selectedFile && selectedFile.path === file.path) {
          setSelectedFile(null);
        }
      } else {
        alert("Failed to delete file");
      }
    } catch (err) {
      console.error("Error deleting file:", err);
      alert("Error deleting file");
    } finally {
      setLoading(false);
      setDeleteConfirmation(null);
    }
  };

  const handleDeleteCancel = () => {
    setDeleteConfirmation(null);
  };

  // Drag and drop handlers - DISABLED
  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // Disabled: Upload functionality is not available
    // if (e.dataTransfer?.types.includes('Files')) {
    //   setIsDragOver(true);
    // }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // Disabled: Upload functionality is not available
    // const rect = e.currentTarget.getBoundingClientRect();
    // const x = e.clientX;
    // const y = e.clientY;
    // if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) {
    //   setIsDragOver(false);
    // }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // Disabled: Upload functionality is not available
    // setIsDragOver(false);
    // const files = Array.from(e.dataTransfer.files);
    // if (files.length > 0) {
    //   await handleFileUpload(files);
    // }
  };

  const handleFileUpload = async (files: File[]) => {
    setLoading(true);
    setError(null);

    try {
      const uploadPromises = files.map(async (file) => {
        const formData = new FormData();
        formData.append('file', file);

        // Upload to cuga_workspace directory
        const response = await apiFetch('/api/workspace/upload', {
          method: 'POST',
          body: formData,
        });

        if (!response.ok) {
          throw new Error(`Failed to upload ${file.name}: ${response.statusText}`);
        }

        return await response.json();
      });

      await Promise.all(uploadPromises);

      // Refresh the workspace tree after successful uploads
      await loadWorkspaceTree();
    } catch (err) {
      console.error('Error uploading files:', err);
      setError(`Upload failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setLoading(false);
    }
  };

  const renderFileTree = (nodes: FileNode[], level: number = 0) => {
    return nodes.map((node) => (
      <div key={node.path} style={{ marginLeft: `${level * 16}px` }}>
        <div
          className={`file-tree-item ${node.type === "directory" ? "directory" : "file"} ${highlightedFile === node.path ? "highlighted" : ""}`}
          onClick={() => handleFileClick(node)}
        >
          {node.type === "directory" ? (
            <>
              {expandedFolders.has(node.path) ? (
                <ChevronDown size={16} className="folder-icon" />
              ) : (
                <ChevronRight size={16} className="folder-icon" />
              )}
              <Folder size={16} className="item-icon" />
            </>
          ) : (
            <>
              <span className="folder-icon-spacer" />
              <File size={16} className="item-icon" />
            </>
          )}
          <span className="item-name">{node.name}</span>
          {node.type === "file" && (
            <div className="file-actions">
              <button
                className="download-icon-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  handleDownload(node.path, node.name);
                }}
                title="Download file"
              >
                <Download size={14} />
              </button>
              {/* Delete button disabled */}
              {/* <button
                className="delete-icon-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  handleDeleteClick(node);
                }}
                title="Delete file"
              >
                <Trash2 size={14} />
              </button> */}
            </div>
          )}
        </div>
        {node.type === "directory" && expandedFolders.has(node.path) && node.children && (
          <div className="folder-children">
            {renderFileTree(node.children, level + 1)}
          </div>
        )}
      </div>
    ));
  };

  return (
    <>
      <div
        className={`workspace-panel ${isOpen ? "open" : "closed"} ${isDragOver ? "drag-over" : ""}`}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        <div className="workspace-panel-header">
          <div className="workspace-panel-title">
            <Folder size={18} />
            <span>Workspace</span>
            <div 
              className="workspace-info-tooltip-wrapper"
              onClick={(e) => e.stopPropagation()}
              onMouseEnter={(e) => e.stopPropagation()}
            >
              <Info size={16} className="info-icon" />
              <div className="workspace-info-tooltip">
                This is the CUGA workspace. Tag files directly from your working directory using <code>@</code>
              </div>
            </div>
          </div>
          <div className="workspace-panel-actions">
            <button
              className="workspace-refresh-btn"
              onClick={loadWorkspaceTree}
              title="Refresh"
            >
              <RefreshCw size={16} />
            </button>
            <button
              className="workspace-close-btn"
              onClick={onToggle}
              title="Close"
            >
              <ChevronRight size={18} />
            </button>
          </div>
        </div>

        <div className="workspace-panel-content">
          {error ? (
            <div className="workspace-error">
              <p>{error}</p>
              <button onClick={loadWorkspaceTree}>Retry</button>
            </div>
          ) : fileTree.length === 0 ? (
            <div className="workspace-empty">
              <Folder size={48} className="empty-icon" />
              <p>Workspace is empty</p>
              <small>Files created by agents will appear here</small>
            </div>
          ) : (
            <div className="file-tree">
              {renderFileTree(fileTree)}
            </div>
          )}
        </div>
      </div>

      {!isOpen && (
        <button
          className="workspace-toggle-btn"
          onClick={onToggle}
          title="Open Workspace"
        >
          <Folder size={18} />
        </button>
      )}

      {selectedFile && (
        <div className="file-viewer-overlay" onClick={() => setSelectedFile(null)}>
          <div className="file-viewer-modal" onClick={(e) => e.stopPropagation()}>
            <div className="file-viewer-header">
              <div className="file-viewer-title">
                <FileText size={18} />
                <span>{selectedFile.name}</span>
              </div>
              <div className="file-viewer-actions">
                <button
                  className="file-viewer-btn"
                  onClick={() => handleDownload(selectedFile.path, selectedFile.name)}
                >
                  <Download size={16} />
                  Download
                </button>
                <button
                  className="file-viewer-close"
                  onClick={() => setSelectedFile(null)}
                >
                  <X size={18} />
                </button>
              </div>
            </div>
            <div className="file-viewer-content">
              <pre>{selectedFile.content}</pre>
            </div>
          </div>
        </div>
      )}

      {loading && (
        <div className="workspace-loading-overlay">
          <div className="workspace-spinner" />
        </div>
      )}

      {/* Drag overlay disabled */}
      {/* {isDragOver && (
        <div className="workspace-drag-overlay">
          <div className="workspace-drag-content">
            <div className="workspace-drag-icon">📁</div>
            <div className="workspace-drag-text">Drop files here to upload</div>
          </div>
        </div>
      )} */}

      {/* Delete confirmation modal disabled */}
      {/* {deleteConfirmation?.isOpen && (
        <div className="delete-confirmation-overlay" onClick={handleDeleteCancel}>
          <div className="delete-confirmation-modal" onClick={(e) => e.stopPropagation()}>
            <div className="delete-confirmation-header">
              <Trash2 size={24} className="delete-icon" />
              <h3>Delete File</h3>
            </div>
            <div className="delete-confirmation-content">
              <p>Are you sure you want to delete <strong>{deleteConfirmation.file.name}</strong>?</p>
              <p className="delete-warning">This action cannot be undone.</p>
            </div>
            <div className="delete-confirmation-actions">
              <button
                className="delete-cancel-btn"
                onClick={handleDeleteCancel}
                disabled={loading}
              >
                Cancel
              </button>
              <button
                className="delete-confirm-btn"
                onClick={handleDeleteConfirm}
                disabled={loading}
              >
                {loading ? "Deleting..." : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )} */}
    </>
  );
}


