import { useState, useEffect, useCallback, useRef } from "react";
import * as api from "../api";
import { filterJsonUploadFiles, isTextWorkspaceFile } from "./constants";
import { collectDirectoryPaths } from "./utils";
import { workspaceService } from "./workspaceService";
import type { FileNode, WorkspaceFilePreview, WorkspaceNotify } from "./types";

interface UseWorkspacePanelOptions {
  threadId?: string;
  pollIntervalMs?: number;
  enabled?: boolean;
  onNotify?: WorkspaceNotify;
}

export function useWorkspacePanel({
  threadId,
  pollIntervalMs = 2500,
  enabled = true,
  onNotify,
}: UseWorkspacePanelOptions) {
  const [workspaceTree, setWorkspaceTree] = useState<FileNode[]>([]);
  const [workspaceExpandedDirs, setWorkspaceExpandedDirs] = useState<Set<string>>(() => new Set());
  const workspaceTreeDirPathsPrevRef = useRef<Set<string>>(new Set());
  const [workspaceTreeLoading, setWorkspaceTreeLoading] = useState(true);
  const [workspaceDragOver, setWorkspaceDragOver] = useState(false);
  const [filePreview, setFilePreview] = useState<WorkspaceFilePreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const notify = useCallback(
    (kind: Parameters<WorkspaceNotify>[0], title: string, subtitle?: string) => {
      if (onNotify) {
        onNotify(kind, title, subtitle);
        return;
      }
      if (kind === "error" || kind === "warning") {
        setError(subtitle ? `${title}: ${subtitle}` : title);
      }
    },
    [onNotify],
  );

  const effectiveThreadId = threadId?.trim() || undefined;

  const fetchWorkspaceTree = useCallback(
    async (forceRefresh = false) => {
      if (!enabled) return;
      try {
        if (forceRefresh) setWorkspaceTreeLoading(true);
        setError(null);
        const data = await workspaceService.getWorkspaceTree(forceRefresh, effectiveThreadId);
        setWorkspaceTree(data.tree || []);
      } catch (err) {
        console.error("Error fetching workspace tree:", err);
        if (forceRefresh) {
          notify(
            "error",
            "Workspace refresh failed",
            err instanceof Error ? err.message : "Unknown error",
          );
        }
      } finally {
        setWorkspaceTreeLoading(false);
      }
    },
    [effectiveThreadId, enabled, notify],
  );

  useEffect(() => {
    if (!enabled) return;
    void fetchWorkspaceTree();
    const interval = setInterval(() => void fetchWorkspaceTree(), pollIntervalMs);
    return () => clearInterval(interval);
  }, [enabled, fetchWorkspaceTree, pollIntervalMs]);

  useEffect(() => {
    workspaceTreeDirPathsPrevRef.current = new Set();
    setWorkspaceExpandedDirs(new Set());
    setWorkspaceTree([]);
    setWorkspaceTreeLoading(true);
  }, [effectiveThreadId]);

  useEffect(() => {
    const valid = collectDirectoryPaths(workspaceTree);
    const prevValid = workspaceTreeDirPathsPrevRef.current;
    setWorkspaceExpandedDirs((expanded) => {
      const next = new Set<string>();
      for (const path of expanded) {
        if (valid.has(path)) next.add(path);
      }
      for (const path of valid) {
        if (!prevValid.has(path)) next.add(path);
      }
      return next;
    });
    workspaceTreeDirPathsPrevRef.current = valid;
  }, [workspaceTree]);

  const handleWorkspaceDirToggle = useCallback((path: string, ...args: unknown[]) => {
    const first = args[0];
    const second = args[1];
    let nextExpanded: boolean | undefined;
    if (typeof first === "boolean") {
      nextExpanded = first;
    } else if (second && typeof second === "object" && second !== null && "isExpanded" in second) {
      nextExpanded = Boolean((second as { isExpanded?: boolean }).isExpanded);
    }
    if (typeof nextExpanded !== "boolean") return;
    setWorkspaceExpandedDirs((prev) => {
      const next = new Set(prev);
      if (nextExpanded) next.add(path);
      else next.delete(path);
      return next;
    });
  }, []);

  const handleFileClick = useCallback(
    async (node: FileNode) => {
      if (node.type !== "file") return;
      if (!isTextWorkspaceFile(node.name)) {
        notify("info", "Preview not available", "Only text and markdown files can be previewed.");
        return;
      }
      try {
        const res = await api.getWorkspaceFile(node.path, effectiveThreadId);
        if (res.ok) {
          const data = await res.json();
          setFilePreview({ path: node.path, content: data.content, name: node.name });
        } else {
          notify("error", "Failed to load file", res.statusText);
        }
      } catch (err) {
        notify("error", "Error loading file", err instanceof Error ? err.message : "Unknown error");
      }
    },
    [effectiveThreadId, notify],
  );

  const handleWorkspaceFileDownload = useCallback(
    async (node: FileNode) => {
      if (node.type !== "file") return;
      try {
        const res = await api.getWorkspaceDownload(node.path, effectiveThreadId);
        if (!res.ok) {
          notify("error", "Download failed", res.statusText || `HTTP ${res.status}`);
          return;
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = node.name;
        anchor.click();
        URL.revokeObjectURL(url);
      } catch (err) {
        notify("error", "Download failed", err instanceof Error ? err.message : "Unknown error");
      }
    },
    [effectiveThreadId, notify],
  );

  const handleWorkspaceUpload = useCallback(
    async (files: File[]) => {
      const tid = effectiveThreadId;
      if (!tid) {
        notify("warning", "Upload unavailable", "Start a chat before uploading files.");
        return;
      }
      try {
        setError(null);
        await Promise.all(
          files.map(async (file) => {
            const res = await api.uploadWorkspaceFile(file, tid);
            if (!res.ok) {
              let detail = res.statusText;
              try {
                const body = await res.json();
                detail = body.detail || detail;
              } catch {
                // ignore
              }
              throw new Error(`${file.name}: ${detail}`);
            }
          }),
        );
        notify(
          "success",
          "Upload complete",
          `${files.length} file${files.length !== 1 ? "s" : ""} uploaded to workspace/uploads/`,
        );
        await fetchWorkspaceTree();
      } catch (err) {
        notify("error", "Upload failed", err instanceof Error ? err.message : "Unknown error");
      }
    },
    [effectiveThreadId, fetchWorkspaceTree, notify],
  );

  const handleWorkspaceFileInputChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const files = event.target.files ? Array.from(event.target.files) : [];
      if (files.length > 0) {
        void handleWorkspaceUpload(files);
      }
      event.target.value = "";
    },
    [handleWorkspaceUpload],
  );

  const handleDragEnter = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.stopPropagation();
    if (event.dataTransfer?.types.includes("Files")) {
      setWorkspaceDragOver(true);
    }
  }, []);

  const handleDragLeave = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    const { clientX: x, clientY: y } = event;
    if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) {
      setWorkspaceDragOver(false);
    }
  }, []);

  const handleDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.stopPropagation();
  }, []);

  const handleDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      event.stopPropagation();
      setWorkspaceDragOver(false);
      const files = filterJsonUploadFiles(Array.from(event.dataTransfer.files));
      if (files.length > 0) {
        void handleWorkspaceUpload(files);
      }
    },
    [handleWorkspaceUpload],
  );

  const closeFilePreview = useCallback(() => {
    setFilePreview(null);
  }, []);

  const downloadFilePreview = useCallback(async () => {
    if (!filePreview) return;
    try {
      const res = await api.getWorkspaceDownload(filePreview.path, effectiveThreadId);
      if (!res.ok) {
        notify("error", "Download failed", res.statusText || `HTTP ${res.status}`);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filePreview.name;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      notify("error", "Download failed", err instanceof Error ? err.message : "Unknown error");
    }
  }, [effectiveThreadId, filePreview, notify]);

  return {
    workspaceTree,
    workspaceTreeLoading,
    workspaceExpandedDirs,
    workspaceDragOver,
    filePreview,
    error,
    fetchWorkspaceTree,
    handleWorkspaceDirToggle,
    handleFileClick,
    handleWorkspaceFileDownload,
    handleWorkspaceUpload,
    handleWorkspaceFileInputChange,
    handleDragEnter,
    handleDragLeave,
    handleDragOver,
    handleDrop,
    closeFilePreview,
    downloadFilePreview,
    setError,
  };
}

export type WorkspacePanelState = ReturnType<typeof useWorkspacePanel>;
