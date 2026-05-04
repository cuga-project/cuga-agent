// streamStateManager.ts
import { apiFetch } from "../../frontend/src/api";
type StreamStateListener = (isProcessing: boolean) => void;

class StreamStateManager {
  private isStreaming = false;
  private turnInFlight = false;
  private listeners: Set<StreamStateListener> = new Set();
  private currentAbortController: AbortController | null = null;

  // Combined processing state: either a turn is in-flight or tokens are streaming
  getIsProcessing() {
    return this.turnInFlight || this.isStreaming;
  }

  setStreaming(streaming: boolean) {
    this.isStreaming = streaming;
    console.log("listeners", this.listeners);
    const combined = this.getIsProcessing();
    this.listeners.forEach((listener) => listener(combined));
  }

  getIsStreaming() {
    return this.isStreaming;
  }

  setTurnInFlight(inFlight: boolean) {
    this.turnInFlight = inFlight;
    const combined = this.getIsProcessing();
    this.listeners.forEach((listener) => listener(combined));
  }

  getIsTurnInFlight() {
    return this.turnInFlight;
  }

  subscribe(listener: StreamStateListener): () => void {
    this.listeners.add(listener);
    // Immediately notify the new subscriber of current combined state
    try {
      listener(this.getIsProcessing());
    } catch (e) {
      // noop
    }
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
    this.setTurnInFlight(false);
  }
}

export const streamStateManager = new StreamStateManager();
