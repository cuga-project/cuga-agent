import { apiFetch } from "../api";
import type { FileNode, WorkspaceData } from "./types";

class WorkspaceService {
  private static instance: WorkspaceService;
  private lastFetchTime = 0;
  private cachedData: WorkspaceData | null = null;
  private cachedForThreadId: string | undefined = undefined;
  private pendingRequest: Promise<WorkspaceData> | null = null;
  private readonly MIN_INTERVAL_MS = 3000;
  private listeners = new Set<(data: WorkspaceData) => void>();

  private constructor() {}

  static getInstance(): WorkspaceService {
    if (!WorkspaceService.instance) {
      WorkspaceService.instance = new WorkspaceService();
    }
    return WorkspaceService.instance;
  }

  subscribe(callback: (data: WorkspaceData) => void): () => void {
    this.listeners.add(callback);
    if (this.cachedData) {
      callback(this.cachedData);
    }
    return () => {
      this.listeners.delete(callback);
    };
  }

  private notifyListeners(data: WorkspaceData): void {
    this.listeners.forEach((callback) => {
      try {
        callback(data);
      } catch (error) {
        console.error("Error in workspace listener:", error);
      }
    });
  }

  async getWorkspaceTree(forceRefresh = false, threadId?: string): Promise<WorkspaceData> {
    const tidKey = threadId ?? "";
    if (this.cachedForThreadId !== tidKey) {
      this.cachedData = null;
      this.cachedForThreadId = tidKey;
      this.lastFetchTime = 0;
    }

    if (forceRefresh) {
      this.cachedData = null;
      this.lastFetchTime = 0;
    }

    const now = Date.now();
    const timeSinceLastFetch = now - this.lastFetchTime;

    if (!forceRefresh && this.cachedData && timeSinceLastFetch < this.MIN_INTERVAL_MS) {
      return this.cachedData;
    }

    if (this.pendingRequest && !forceRefresh) {
      return this.pendingRequest;
    }

    if (!forceRefresh && timeSinceLastFetch < this.MIN_INTERVAL_MS) {
      const waitTime = this.MIN_INTERVAL_MS - timeSinceLastFetch;
      await new Promise((resolve) => setTimeout(resolve, waitTime));
    }

    this.pendingRequest = this.fetchWorkspaceData(threadId, forceRefresh);

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

  private async fetchWorkspaceData(threadId?: string, forceRefresh = false): Promise<WorkspaceData> {
    try {
      const params = new URLSearchParams();
      if (threadId) params.set("thread_id", threadId);
      if (forceRefresh) params.set("_", String(Date.now()));
      const q = params.toString();
      const response = await apiFetch(`/api/workspace/tree${q ? `?${q}` : ""}`);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      return {
        tree: (data.tree || []) as FileNode[],
        timestamp: Date.now(),
      };
    } catch (error) {
      console.error("Error fetching workspace tree:", error);
      if (!forceRefresh && this.cachedData) {
        return this.cachedData;
      }
      throw error;
    }
  }

  getCachedData(): WorkspaceData | null {
    return this.cachedData;
  }

  clearCache(): void {
    this.cachedData = null;
    this.cachedForThreadId = undefined;
    this.lastFetchTime = 0;
  }
}

export const workspaceService = WorkspaceService.getInstance();
