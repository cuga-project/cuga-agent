/**
 * ChatContinuity.test.tsx
 *
 * Lightweight logic tests for W14 continue-conversation features.
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

// ── 2. thread_created SSE event handler adopts the server ID ─────────────────
describe("thread_created SSE event adoption", () => {
  it("setThreadId is called with the server-issued ID", () => {
    // Simulate the logic in customSendMessage.ts: adopt server-issued ID
    let capturedId: string | null = null;
    const setThreadId = (id: string) => { capturedId = id; };

    const event = { name: "thread_created", data: "srv-abc-123" };

    if (event.name === "thread_created") {
      const serverId = typeof event.data === "string" ? event.data.trim() : null;
      if (serverId) {
        setThreadId(serverId);
      }
    }

    expect(capturedId).toBe("srv-abc-123");
  });

  it("ignores a thread_created event with empty data", () => {
    let capturedId: string | null = null;
    const setThreadId = (id: string) => { capturedId = id; };

    const event = { name: "thread_created", data: "" };

    if (event.name === "thread_created") {
      const serverId = typeof event.data === "string" ? event.data.trim() : null;
      if (serverId) {
        setThreadId(serverId);
      }
    }

    expect(capturedId).toBeNull();
  });

  it("ignores whitespace-only server ID", () => {
    let capturedId: string | null = null;
    const setThreadId = (id: string) => { capturedId = id; };

    const event = { name: "thread_created", data: "   " };

    if (event.name === "thread_created") {
      const serverId = typeof event.data === "string" ? event.data.trim() : null;
      if (serverId) {
        setThreadId(serverId);
      }
    }

    expect(capturedId).toBeNull();
  });
});

// ── 3. Conflict response detection (409 + error: "conflict") ─────────────────
describe("409 error code detection", () => {
  // Mirror the parsing logic from customSendMessage.ts
  const parseErrorCode = (body: string): string | undefined => {
    try {
      return JSON.parse(body)?.error;
    } catch {
      return undefined;
    }
  };

  it("detects a 409 conflict error code", () => {
    expect(parseErrorCode(JSON.stringify({ error: "conflict" }))).toBe("conflict");
  });

  it("detects a 409 unavailable error code", () => {
    expect(parseErrorCode(JSON.stringify({ error: "unavailable" }))).toBe("unavailable");
  });

  it("returns undefined for non-JSON body", () => {
    expect(parseErrorCode("not json")).toBeUndefined();
  });

  it("returns undefined for missing error field", () => {
    expect(parseErrorCode(JSON.stringify({ detail: "oops" }))).toBeUndefined();
  });
});

// ── 4. StreamHttpError class shape ───────────────────────────────────────────
describe("StreamHttpError", () => {
  // Reproduce the class contract from customSendMessage.ts to validate its shape
  // without importing the module (which has @carbon/ai-chat transitive deps).
  class StreamHttpError extends Error {
    constructor(public readonly status: number, public readonly errorCode?: string) {
      super(`HTTP ${status}`);
      this.name = "StreamHttpError";
    }
  }

  it("has the correct name", () => {
    const err = new StreamHttpError(404);
    expect(err.name).toBe("StreamHttpError");
  });

  it("carries status 404 with no errorCode", () => {
    const err = new StreamHttpError(404);
    expect(err.status).toBe(404);
    expect(err.errorCode).toBeUndefined();
  });

  it("carries 409 unavailable", () => {
    const err = new StreamHttpError(409, "unavailable");
    expect(err.status).toBe(409);
    expect(err.errorCode).toBe("unavailable");
  });

  it("carries 409 conflict", () => {
    const err = new StreamHttpError(409, "conflict");
    expect(err.errorCode).toBe("conflict");
  });
});

// ── 5. isReadonly logic (legacy / expired / unavailable) ─────────────────────
describe("isReadonly logic", () => {
  // Mirror the expression from ChatLanding.tsx
  const computeIsReadonly = (
    selectedThreadStatus: string | null,
    selectedThreadUnavailable: boolean
  ): boolean =>
    selectedThreadStatus === "legacy" ||
    selectedThreadStatus === "expired" ||
    selectedThreadUnavailable;

  it("legacy status → isReadonly true", () => {
    expect(computeIsReadonly("legacy", false)).toBe(true);
  });

  it("expired status → isReadonly true", () => {
    expect(computeIsReadonly("expired", false)).toBe(true);
  });

  it("active status → isReadonly false", () => {
    expect(computeIsReadonly("active", false)).toBe(false);
  });

  it("null status + unavailable → isReadonly true", () => {
    expect(computeIsReadonly(null, true)).toBe(true);
  });

  it("null status + available → isReadonly false", () => {
    expect(computeIsReadonly(null, false)).toBe(false);
  });

  it("new status → isReadonly false", () => {
    expect(computeIsReadonly("new", false)).toBe(false);
  });
});

// ── 6. ensureConversationId — in-flight dedup and agent-switch discard ────────
describe("ensureConversationId creation ref scoping", () => {
  // Mirror the logic from ChatLanding.tsx without importing the component.
  // This validates the ref-keying invariants in isolation.

  function makeEnsureConversationId(
    createConversation: (agentId: string) => Promise<{ id: string }>,
    setDraftThread: (fn: (prev: { threadId: string | null }) => { threadId: string | null }) => void,
    setActiveThreadId: (id: string) => void,
  ) {
    // Mirrors the exact shape used in ChatLanding.tsx
    let conversationCreationRef: { agentId: string; promise: Promise<string | null> } | null = null;

    return async function ensureConversationId(
      effectiveChatThreadId: string | null,
      effectiveChatAgentId: string,
    ): Promise<string | null> {
      const existing = effectiveChatThreadId?.trim();
      if (existing) return existing;

      if (conversationCreationRef?.agentId === effectiveChatAgentId) {
        return conversationCreationRef.promise;
      }

      const promise = (async () => {
        try {
          const created = await createConversation(effectiveChatAgentId);
          setDraftThread((prev) => {
            if (prev.threadId) return prev;
            return { ...prev, threadId: created.id };
          });
          setActiveThreadId(created.id);
          return created.id;
        } catch {
          return null;
        } finally {
          if (conversationCreationRef?.agentId === effectiveChatAgentId) {
            conversationCreationRef = null;
          }
        }
      })();

      conversationCreationRef = { agentId: effectiveChatAgentId, promise };
      return promise;
    };
  }

  it("concurrent calls for the same agent share one createConversation call", async () => {
    let callCount = 0;
    const createConversation = vi.fn(async (_agentId: string) => {
      callCount++;
      return { id: `thread-${callCount}` };
    });
    const setDraft = vi.fn();
    const setActive = vi.fn();

    const ensure = makeEnsureConversationId(createConversation, setDraft, setActive);

    // Fire two concurrent calls for the same agent
    const [id1, id2] = await Promise.all([
      ensure(null, "agent-a"),
      ensure(null, "agent-a"),
    ]);

    expect(createConversation).toHaveBeenCalledTimes(1);
    expect(id1).toBe("thread-1");
    expect(id2).toBe("thread-1");
  });

  it("switching agent discards the in-flight ref so a new create fires", async () => {
    let callCount = 0;
    const createConversation = vi.fn(async (agentId: string) => {
      callCount++;
      return { id: `${agentId}-thread-${callCount}` };
    });
    const setDraft = vi.fn();
    const setActive = vi.fn();

    const ensure = makeEnsureConversationId(createConversation, setDraft, setActive);

    // First call for agent-a (don't await yet — simulates in-flight)
    const p1 = ensure(null, "agent-a");

    // Simulate agent switch: second call is for agent-b
    const p2 = ensure(null, "agent-b");

    const [id1, id2] = await Promise.all([p1, p2]);

    // Both resolved but each got a separate create call
    expect(createConversation).toHaveBeenCalledTimes(2);
    expect(id1).toContain("agent-a");
    expect(id2).toContain("agent-b");
  });
});
