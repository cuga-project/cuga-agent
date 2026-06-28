import { useState, useEffect, useCallback } from 'react';

export interface FileNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: FileNode[];
}

export function useWorkspaceTree(pollInterval: number = 15000) {
  const [fileTree, setFileTree] = useState<FileNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadWorkspaceTree = useCallback(async (forceRefresh = false) => {
    try {
      setLoading(true);
      setError(null);
      const { workspaceService } = await import('./workspaceService');
      const data = await workspaceService.getWorkspaceTree(forceRefresh);
      setFileTree(data.tree || []);
    } catch (err) {
      console.error("Error loading workspace:", err);
      setError("Error loading workspace");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Load immediately
    loadWorkspaceTree();

    // Set up polling
    const interval = setInterval(loadWorkspaceTree, pollInterval);

    return () => clearInterval(interval);
  }, [loadWorkspaceTree, pollInterval]);

  return {
    fileTree,
    loading,
    error,
    refresh: () => loadWorkspaceTree(true),
  };
}
