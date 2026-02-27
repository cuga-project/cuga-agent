// streamStateManager.ts
import { apiFetch } from "../../frontend/src/api";
type StreamStateListener = (isStreaming: boolean) => void;

class StreamStateManager {
  private isStreaming = false;
  private listeners: Set<StreamStateListener> = new Set();
  private currentAbortController: AbortController | null = null;

  setStreaming(streaming: boolean) {
    this.isStreaming = streaming;
    console.log("listeners", this.listeners);
    this.listeners.forEach((listener) => listener(streaming));
  }

  getIsStreaming() {
    return this.isStreaming;
  }

  subscribe(listener: StreamStateListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  setAbortController(controller: AbortController | null) {
    this.currentAbortController = controller;
  }

  async stopStream() {
    if (this.currentAbortController) {
      this.currentAbortController.abort();
    }

    try {
      const response = await apiFetch('/stop', {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
      });

      if (!response.ok) {
        console.error("Failed to stop stream on server");
      }
    } catch (error) {
      console.error("Error stopping stream:", error);
    }

    this.setStreaming(false);
  }
}

export const streamStateManager = new StreamStateManager();
