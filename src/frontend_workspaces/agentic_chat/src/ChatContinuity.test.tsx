/**
 * Continuing a conversation from the web UI (#774): server-issued IDs.
 * Environment: node (see vite.config.ts — no jsdom installed)
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

// ── 1. createConversation calls the correct endpoint ─────────────────────────
describe("createConversation", () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    vi.resetModules();
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("POSTs to /api/conversations with agent_id and returns the parsed thread", async () => {
    const mockThread = { id: "srv-thread-001", title: "", timestamp: 0, preview: "", status: "new" };
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(mockThread), { status: 200, headers: { "Content-Type": "application/json" } })
    );

    const { createConversation } = await import("../../frontend/src/api");
    const result = await createConversation("my-agent", "Hello");

    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/conversations"),
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
        body: JSON.stringify({ agent_id: "my-agent", title: "Hello" }),
      })
    );
    expect(result.id).toBe("srv-thread-001");
    expect(result.status).toBe("new");
  });

  it("throws when the server returns a non-ok status", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response("Internal Server Error", { status: 500 })
    );

    const { createConversation } = await import("../../frontend/src/api");
    await expect(createConversation("my-agent")).rejects.toThrow("HTTP 500");
  });
});

describe("adopting the server-issued thread ID", () => {
  beforeEach(() => vi.resetModules());

  it("a new chat adopts the ID from thread_created", async () => {
    const state = await import("../../frontend/src/carbon-chat/threadState");
    state.showThread(null);
    const startedAt = state.getThreadGeneration();

    state.adoptIssuedThreadId("srv-1", startedAt);

    expect(state.getCurrentThreadId()).toBe("srv-1");
  });

  it("an ID arriving after the user moved to another chat is ignored", async () => {
    const state = await import("../../frontend/src/carbon-chat/threadState");
    state.showThread(null);
    const startedAt = state.getThreadGeneration();
    state.showThread(null); // New chat while the first send is still streaming

    state.adoptIssuedThreadId("srv-1", startedAt);

    expect(state.getCurrentThreadId()).toBeNull();
  });

  it("never replaces the ID of a conversation already on screen", async () => {
    const state = await import("../../frontend/src/carbon-chat/threadState");
    state.showThread("existing");

    state.adoptIssuedThreadId("srv-1", state.getThreadGeneration());

    expect(state.getCurrentThreadId()).toBe("existing");
  });
});
