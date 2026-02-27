import { fetchEventSource } from "@microsoft/fetch-event-source";
import { ChatInstance } from "@carbon/ai-chat";
import { API_BASE_URL } from "./constants";
import { apiFetch } from "../../frontend/src/api";

// Configuration constants
// When built without a bundler define, `FAKE_STREAM` may be absent. Declare and compute safely.
declare const FAKE_STREAM: boolean | undefined;
const USE_FAKE_STREAM =
  typeof FAKE_STREAM !== "undefined" ? !!FAKE_STREAM : !!(globalThis as any).FAKE_STREAM;
const FAKE_STREAM_FILE = "/fake_data.json";
const FAKE_STREAM_DELAY = 1000;

interface StreamingManagerOptions {
  onAddStep: (title: string, content: string) => void;
  onComplete: () => void;
  onStop: () => void;
  onError: (error: Error) => void;
}

export class StreamingManager {
  private isStreaming = false;
  private shouldStop = false;
  private abortController: AbortController | null = null;
  private fakeStreamTimeout: NodeJS.Timeout | null = null;

  constructor(private options: StreamingManagerOptions) {}

  async startStream(query: string, chatInstance: ChatInstance): Promise<void> {
    if (this.isStreaming) {
      console.warn("Stream already in progress");
      return;
    }

    console.log("Starting new stream...");
    this.isStreaming = true;
    this.shouldStop = false;
    this.abortController = new AbortController();

    try {
      if (USE_FAKE_STREAM) {
        await this.simulateFakeStream(query);
      } else {
        await this.performRealStreaming(query);
      }
    } catch (error) {
      if (error.name !== "AbortError") {
        console.error("Stream error:", error);
        this.options.onError(error as Error);
      } else {
        console.log("Stream was aborted");
      }
    } finally {
      this.cleanup();
    }
  }

  async stopStream(): Promise<void> {
    if (!this.isStreaming) {
      console.log("No stream to stop");
      return;
    }

    console.log("Stopping stream...");
    this.shouldStop = true;

    // Clear fake stream timeout if running
    if (this.fakeStreamTimeout) {
      clearTimeout(this.fakeStreamTimeout);
      this.fakeStreamTimeout = null;
    }

    if (USE_FAKE_STREAM) {
      // For fake stream, just call the stop callback
      this.options.onStop();
      this.cleanup();
    } else {
      try {
        // Abort the current fetch request first
        if (this.abortController) {
          this.abortController.abort();
        }

        // Send stop request to backend for real stream
        const stopResponse = await apiFetch('/stop', {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          // Use a separate abort controller for the stop request
          signal: AbortSignal.timeout(5000), // 5 second timeout for stop request
        });

        if (!stopResponse.ok) {
          console.warn("Stop request failed:", stopResponse.status);
        }
      } catch (error) {
        console.error("Error stopping stream:", error);
      }

      // Always call onStop and cleanup, regardless of backend response
      this.options.onStop();
      this.cleanup();
    }
  }

  private cleanup(): void {
    console.log("Cleaning up stream...");
    this.isStreaming = false;
    this.shouldStop = false;
    this.abortController = null;

    if (this.fakeStreamTimeout) {
      clearTimeout(this.fakeStreamTimeout);
      this.fakeStreamTimeout = null;
    }
  }

  private async simulateFakeStream(query: string): Promise<void> {
    console.log("Starting fake stream simulation with query:", query.substring(0, 50));

    try {
      // Load the fake stream data from JSON file
      const response = await fetch(FAKE_STREAM_FILE);
      if (!response.ok) {
        throw new Error(`Failed to load fake stream data: ${response.status} ${response.statusText}`);
      }

      const fakeStreamData = await response.json();

      if (!fakeStreamData.steps || !Array.isArray(fakeStreamData.steps)) {
        throw new Error("Invalid fake stream data format. Expected { steps: [{ name: string, data: any }] }");
      }

      // Add initial step
      if (!this.shouldStop) {
        this.options.onAddStep("simple_text", "Simulating stream from JSON file...");
      }

      // Process each step from the fake data
      for (let i = 0; i < fakeStreamData.steps.length && !this.shouldStop; i++) {
        const step = fakeStreamData.steps[i];

        // Wait for the specified delay using a promise-based approach
        await new Promise<void>((resolve, reject) => {
          this.fakeStreamTimeout = setTimeout(() => {
            if (this.shouldStop) {
              resolve(); // Exit gracefully if stopped
              return;
            }

            try {
              // Simulate the event
              const fakeEvent = {
                event: step.name,
                data: step.data,
              };

              console.log("Simulating fake stream event:", fakeEvent);

              const stepData = this.parseStreamEvent(fakeEvent);
              if (stepData && !this.shouldStop) {
                // Special handling for the Stopped event
                if (step.name === "Stopped") {
                  this.options.onStop();
                } else {
                  this.options.onAddStep(stepData.title, stepData.content);
                }
              }
              resolve();
            } catch (error) {
              reject(error);
            }
          }, FAKE_STREAM_DELAY);
        });
      }

      // Signal completion if not stopped
      if (!this.shouldStop) {
        this.options.onComplete();
        console.log("Fake stream simulation completed");
      }
    } catch (error) {
      console.error("Fake streaming error:", error);
      this.options.onError(error as Error);
    }
  }

  private async performRealStreaming(query: string): Promise<void> {
    console.log("Starting real stream...");

    return new Promise<void>((resolve, reject) => {
      fetchEventSource(`${API_BASE_URL}/stream`, {
        headers: { "Content-Type": "application/json" },
        method: "POST",
        body: JSON.stringify({ query }),
        signal: this.abortController?.signal,

        onopen: (response) => {
          console.log("Stream opened:", response.status);
          if (!this.shouldStop) {
            this.options.onAddStep("simple_text", "Processing request...");
          }
        },

        onmessage: (event) => {
          // Early return if stream should stop
          if (this.shouldStop) {
            console.log("Ignoring message - stream stopped");
            return;
          }

          console.log("Received stream event:", event.event);

          // Handle special stop event from backend
          if (event.event === "Stopped" || event.event === "stop") {
            console.log("Received stop event from backend");
            this.shouldStop = true;
            this.options.onStop();
            resolve();
            return;
          }

          // Handle completion event
          if (event.event === "Complete" || event.event === "complete") {
            console.log("Received completion event from backend");
            this.options.onComplete();
            resolve();
            return;
          }

          // Process regular events
          const stepData = this.parseStreamEvent(event);
          if (stepData && !this.shouldStop) {
            this.options.onAddStep(stepData.title, stepData.content);
          }
        },

        onclose: () => {
          console.log("Stream closed");
          if (!this.shouldStop) {
            this.options.onComplete();
          }
          resolve();
        },

        onerror: (error) => {
          console.error("Stream error:", error);
          if (error.name === "AbortError") {
            console.log("Stream was aborted");
            resolve(); // Don't treat abort as an error
          } else {
            this.options.onError(error as Error);
            reject(error);
          }
        },
      }).catch((error) => {
        if (error.name === "AbortError") {
          console.log("fetchEventSource was aborted");
          resolve();
        } else {
          reject(error);
        }
      });
    });
  }

  private parseStreamEvent(event: any): { title: string; content: string } | null {
    try {
      return {
        title: event.event,
        content: typeof event.data === "string" ? event.data : JSON.stringify(event.data),
      };
    } catch (error) {
      console.error("Failed to parse stream event:", error);
      return null;
    }
  }

  // Utility methods
  get isCurrentlyStreaming(): boolean {
    return this.isStreaming;
  }

  get isStopRequested(): boolean {
    return this.shouldStop;
  }

  // Utility method to check if using fake stream
  static get isFakeStreamEnabled(): boolean {
    return USE_FAKE_STREAM;
  }

  // Utility method to set fake stream configuration
  static setFakeStreamConfig(enabled: boolean, file: string = FAKE_STREAM_FILE, delay: number = FAKE_STREAM_DELAY) {
    // Note: This would require modifying the constants above or using a different approach
    // For now, these are compile-time constants
    console.log(`Fake stream config: enabled=${enabled}, file=${file}, delay=${delay}`);
  }
}
