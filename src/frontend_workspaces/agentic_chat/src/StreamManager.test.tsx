import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../frontend/src/api", () => ({ apiFetch: vi.fn() }));
vi.mock("@microsoft/fetch-event-source", () => ({ fetchEventSource: vi.fn() }));
vi.mock("./constants", () => ({ API_BASE_URL: "http://test", RESPONSE_USER_PROFILE: {} }));
import { fetchEventSource } from "@microsoft/fetch-event-source";
import { fetchStreamingData } from "./StreamingWorkflow";
import { apiFetch } from "../../frontend/src/api";
import { streamStateManager } from "./StreamManager";

describe("thread-scoped stop", () => {
  afterEach(() => vi.useRealTimers());
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiFetch).mockResolvedValue(new Response(null, { status: 200 }));
    streamStateManager.setAbortController(null);
    streamStateManager.setStreaming(false);
  });

  it("sends the active thread even when abort clears the stream state", async () => {
    const controller = new AbortController();
    controller.signal.addEventListener("abort", () => streamStateManager.setAbortController(null));
    streamStateManager.setAbortController(controller, "active-thread");
    streamStateManager.setStreaming(true);
    await streamStateManager.stopStream();
    expect(controller.signal.aborted).toBe(true);
    expect(apiFetch).toHaveBeenCalledWith("/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Thread-ID": "active-thread" },
      body: JSON.stringify({ thread_id: "active-thread" }),
    });
    expect(streamStateManager.getIsStreaming()).toBe(false);
  });

  it("does not send a server stop for a simulated stream without a thread", async () => {
    const controller = new AbortController();
    streamStateManager.setAbortController(controller);
    streamStateManager.setStreaming(true);
    await streamStateManager.stopStream();
    expect(controller.signal.aborted).toBe(true);
    expect(apiFetch).not.toHaveBeenCalled();
    expect(streamStateManager.getIsStreaming()).toBe(false);
  });

  it.each(["client-thread", undefined])("uses the stream thread ID for stop (%s)", async (threadId) => {
    vi.useFakeTimers();
    let startedThread: string | undefined;
    vi.mocked(fetchEventSource).mockImplementation(async (_url, options) => {
      startedThread = (options.headers as Record<string, string>)["X-Thread-ID"];
      await streamStateManager.stopStream();
    });
    const streaming = fetchStreamingData({} as any, "task", null, threadId);
    await vi.advanceTimersByTimeAsync(300);
    await streaming;
    expect(startedThread).toBeTruthy();
    if (threadId) expect(startedThread).toBe(threadId);
    expect(apiFetch).toHaveBeenCalledWith("/stop", expect.objectContaining({
      headers: { "Content-Type": "application/json", "X-Thread-ID": startedThread },
      body: JSON.stringify({ thread_id: startedThread }),
    }));
  });

});
