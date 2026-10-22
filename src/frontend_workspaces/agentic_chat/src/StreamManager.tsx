// streamStateManager.ts
import { apiFetch } from "../../frontend/src/api";
type StreamStateListener = (isStreaming: boolean) => void;

class StreamStateManager {
  private isStreaming = false;
  private listeners: Set<StreamStateListener> = new Set();
  private currentThreadId: string | null = null;
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

  setAbortController(controller: AbortController | null, threadId?: string) {
    this.currentAbortController = controller;
    this.currentThreadId = controller ? threadId ?? null : null;
  }

  async stopStream() {
    const threadId = this.currentThreadId;
    if (this.currentAbortController) {
      this.currentAbortController.abort();
    }

    try {
      if (!threadId) return;
      const response = await apiFetch('/stop', {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Thread-ID": threadId,
        },
        body: JSON.stringify({ thread_id: threadId }),
      });

      if (!response.ok) {
        console.error("Failed to stop stream on server");
      }
    } catch (error) {
      console.error("Error stopping stream:", error);
    } finally {
      this.setStreaming(false);
    }
  }
}

export const streamStateManager = new StreamStateManager();
