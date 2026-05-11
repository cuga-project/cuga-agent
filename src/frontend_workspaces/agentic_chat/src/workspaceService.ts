// Shared workspace service with enforced throttling
// Ensures /api/workspace/tree is NEVER called more frequently than once per 3 seconds
import { apiFetch } from "../../frontend/src/api";

interface FileNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: FileNode[];
}

interface WorkspaceData {
  tree: FileNode[];
  timestamp: number;
}

class WorkspaceService {
  private static instance: WorkspaceService;
  private lastFetchTime: number = 0;
  private cachedData: WorkspaceData | null = null;
  private cachedForThreadId: string | undefined = undefined;
  private pendingRequest: Promise<WorkspaceData> | null = null;
  private readonly MIN_INTERVAL_MS = 3000; // 3 seconds minimum between requests
  private listeners: Set<(data: WorkspaceData) => void> = new Set();

  private constructor() {}

  static getInstance(): WorkspaceService {
    if (!WorkspaceService.instance) {
      WorkspaceService.instance = new WorkspaceService();
    }
    return WorkspaceService.instance;
  }

  /**
   * Subscribe to workspace updates
   */
  subscribe(callback: (data: WorkspaceData) => void): () => void {
    this.listeners.add(callback);
    // Immediately send cached data if available
    if (this.cachedData) {
      callback(this.cachedData);
    }
    // Return unsubscribe function
    return () => {
      this.listeners.delete(callback);
    };
  }

  /**
   * Notify all subscribers of new data
   */
  private notifyListeners(data: WorkspaceData): void {
    this.listeners.forEach(callback => {
      try {
        callback(data);
      } catch (error) {
        console.error('Error in workspace listener:', error);
      }
    });
  }

  /**
   * Fetch workspace tree with enforced throttling
   * Returns cached data if called too soon after last fetch
   */
  async getWorkspaceTree(forceRefresh: boolean = false, threadId?: string): Promise<WorkspaceData> {
    const tidKey = threadId ?? '';
    if (this.cachedForThreadId !== tidKey) {
      this.cachedData = null;
      this.cachedForThreadId = tidKey;
      this.lastFetchTime = 0;
    }

    const now = Date.now();
    const timeSinceLastFetch = now - this.lastFetchTime;

    // If we have cached data and haven't exceeded the minimum interval, return cache
    if (!forceRefresh && this.cachedData && timeSinceLastFetch < this.MIN_INTERVAL_MS) {
      return this.cachedData;
    }

    // If there's already a pending request, wait for it instead of making a new one
    if (this.pendingRequest) {
      return this.pendingRequest;
    }

    // Enforce minimum interval even for forced refresh
    if (timeSinceLastFetch < this.MIN_INTERVAL_MS) {
      const waitTime = this.MIN_INTERVAL_MS - timeSinceLastFetch;
      await new Promise(resolve => setTimeout(resolve, waitTime));
    }

    // Make the actual request
    this.pendingRequest = this.fetchWorkspaceData(threadId);
    
    try {
      const data = await this.pendingRequest;
      this.cachedData = data;
      this.lastFetchTime = Date.now();
      this.notifyListeners(data);
      return data;
    } finally {
      this.pendingRequest = null;
    }
  }

  /**
   * Internal method to actually fetch data from the API
   */
  private async fetchWorkspaceData(threadId?: string): Promise<WorkspaceData> {
    try {
      const q = threadId ? `?thread_id=${encodeURIComponent(threadId)}` : '';
      const response = await apiFetch(`/api/workspace/tree${q}`);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      return {
        tree: data.tree || [],
        timestamp: Date.now()
      };
    } catch (error) {
      console.error('Error fetching workspace tree:', error);
      // Return cached data if available, otherwise return empty tree
      if (this.cachedData) {
        return this.cachedData;
      }
      return { tree: [], timestamp: Date.now() };
    }
  }

  /**
   * Get cached data without making a request
   */
  getCachedData(): WorkspaceData | null {
    return this.cachedData;
  }

  /**
   * Clear the cache (useful for testing)
   */
  clearCache(): void {
    this.cachedData = null;
    this.cachedForThreadId = undefined;
    this.lastFetchTime = 0;
  }
}

// Export singleton instance
export const workspaceService = WorkspaceService.getInstance();
export type { FileNode, WorkspaceData };





