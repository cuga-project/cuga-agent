"use strict";
(self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] = self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] || []).push([["agentic_chat_src_workspaceService_ts"],{

/***/ "../agentic_chat/src/workspaceService.ts":
/*!***********************************************!*\
  !*** ../agentic_chat/src/workspaceService.ts ***!
  \***********************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   workspaceService: function() { return /* binding */ workspaceService; }
/* harmony export */ });
// Shared workspace service with enforced throttling
// Ensures /api/workspace/tree is NEVER called more frequently than once per 3 seconds

class WorkspaceService {
  lastFetchTime = 0;
  cachedData = null;
  pendingRequest = null;
  MIN_INTERVAL_MS = 3000; // 3 seconds minimum between requests
  listeners = new Set();
  constructor() {}
  static getInstance() {
    if (!WorkspaceService.instance) {
      WorkspaceService.instance = new WorkspaceService();
    }
    return WorkspaceService.instance;
  }

  /**
   * Subscribe to workspace updates
   */
  subscribe(callback) {
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
  notifyListeners(data) {
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
  async getWorkspaceTree(forceRefresh = false) {
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
    this.pendingRequest = this.fetchWorkspaceData();
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
  async fetchWorkspaceData() {
    try {
      const response = await fetch('/api/workspace/tree');
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
      return {
        tree: [],
        timestamp: Date.now()
      };
    }
  }

  /**
   * Get cached data without making a request
   */
  getCachedData() {
    return this.cachedData;
  }

  /**
   * Clear the cache (useful for testing)
   */
  clearCache() {
    this.cachedData = null;
    this.lastFetchTime = 0;
  }
}

// Export singleton instance
const workspaceService = WorkspaceService.getInstance();

/***/ })

}]);
//# sourceMappingURL=agentic_chat_src_workspaceService_ts.c8f14ca9886c620ac587.bundle.js.map