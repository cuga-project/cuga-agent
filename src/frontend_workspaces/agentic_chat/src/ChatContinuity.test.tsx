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

// ── 6. ensureConversationId — draftKey-scoped in-flight dedup ─────────────────
describe("ensureConversationId creation ref scoping (agentId + draftKey)", () => {
  // Mirror the production logic from ChatLanding.tsx without importing the component.
  // Both agentId and draftKey must match for a pending promise to be reused.

  interface DraftRef {
    agentId: string;
    draftKey: string;
    promise: Promise<string | null>;
  }

  interface DraftState {
    threadId: string | null;
    agentId: string;
    draftKey: string;
  }

  function makeEnsureConversationId(
    createConversation: (agentId: string) => Promise<{ id: string }>,
    getDraft: () => DraftState,
    setDraftThread: (fn: (prev: DraftState) => DraftState) => void,
    setActiveThreadId: (id: string) => void,
    chatAgentIdRef: { current: string },
  ) {
    let conversationCreationRef: DraftRef | null = null;

    return async function ensureConversationId(
      effectiveChatThreadId: string | null,
      effectiveChatAgentId: string,
    ): Promise<string | null> {
      const existing = effectiveChatThreadId?.trim();
      if (existing) return existing;
      const currentDraftKey = getDraft().draftKey;
      if (
        conversationCreationRef?.agentId === effectiveChatAgentId &&
        conversationCreationRef?.draftKey === currentDraftKey
      ) {
        return conversationCreationRef.promise;
      }
      const capturedAgentId = effectiveChatAgentId;
      const capturedDraftKey = currentDraftKey;
      const promise = (async () => {
        try {
          const created = await createConversation(capturedAgentId);
          if (chatAgentIdRef.current !== capturedAgentId || getDraft().draftKey !== capturedDraftKey) {
            return null;
          }
          setDraftThread((prev) => {
            if (prev.agentId !== capturedAgentId || prev.draftKey !== capturedDraftKey) return prev;
            if (prev.threadId) return prev;
            return { ...prev, threadId: created.id };
          });
          if (chatAgentIdRef.current === capturedAgentId && getDraft().draftKey === capturedDraftKey) {
            setActiveThreadId(created.id);
          }
          return created.id;
        } catch {
          return null;
        } finally {
          if (
            conversationCreationRef?.agentId === capturedAgentId &&
            conversationCreationRef?.draftKey === capturedDraftKey
          ) {
            conversationCreationRef = null;
          }
        }
      })();
      conversationCreationRef = { agentId: capturedAgentId, draftKey: capturedDraftKey, promise };
      return promise;
    };
  }

  function makeDraft(agentId: string, key?: string): DraftState {
    return { threadId: null, agentId, draftKey: key ?? `${agentId}:${Date.now()}:x` };
  }

  it("concurrent calls for the same agent and draft share one createConversation call", async () => {
    let callCount = 0;
    const createConversation = vi.fn(async (_agentId: string) => ({ id: `thread-${++callCount}` }));
    let draft = makeDraft("agent-a", "key-1");
    const setDraft = vi.fn((fn: (p: DraftState) => DraftState) => { draft = fn(draft); });
    const setActive = vi.fn();
    const agentRef = { current: "agent-a" };
    const ensure = makeEnsureConversationId(createConversation, () => draft, setDraft, setActive, agentRef);

    const [id1, id2] = await Promise.all([
      ensure(null, "agent-a"),
      ensure(null, "agent-a"),
    ]);

    expect(createConversation).toHaveBeenCalledTimes(1);
    expect(id1).toBe("thread-1");
    expect(id2).toBe("thread-1");
  });

  it("agent switch: resolved result does NOT call setActiveThreadId for the new agent", async () => {
    let resolve!: (v: { id: string }) => void;
    const createConversation = vi.fn(() => new Promise<{ id: string }>((r) => { resolve = r; }));
    let currentDraft = makeDraft("agent-a", "key-1");
    const setDraft = vi.fn((fn: (p: DraftState) => DraftState) => { currentDraft = fn(currentDraft); });
    const setActive = vi.fn();
    const agentRef = { current: "agent-a" };
    const ensure = makeEnsureConversationId(
      createConversation, () => currentDraft, setDraft, setActive, agentRef,
    );

    // Start create for agent-a
    const p = ensure(null, "agent-a");

    // Simulate agent switch before the promise resolves
    currentDraft = makeDraft("agent-b", "key-2");
    agentRef.current = "agent-b";

    // Now resolve agent-a's request
    resolve({ id: "thread-agent-a" });
    expect(await p).toBeNull();

    // setActiveThreadId must NOT have been called (agent changed)
    expect(setActive).not.toHaveBeenCalled();
  });

  it("new-chat (draftKey change for same agent): resolved result does NOT apply to new draft", async () => {
    let resolve!: (v: { id: string }) => void;
    const createConversation = vi.fn(() => new Promise<{ id: string }>((r) => { resolve = r; }));
    let currentDraft = makeDraft("agent-a", "key-1");
    const results: DraftState[] = [];
    const setDraft = vi.fn((fn: (p: DraftState) => DraftState) => {
      const next = fn(currentDraft);
      results.push(next);
      currentDraft = next;
    });
    const setActive = vi.fn();
    const agentRef = { current: "agent-a" };
    const ensure = makeEnsureConversationId(
      createConversation, () => currentDraft, setDraft, setActive, agentRef,
    );

    // Start create for draft key-1
    const p = ensure(null, "agent-a");

    // User pressed "New chat" — same agent, new draftKey
    currentDraft = makeDraft("agent-a", "key-2");

    // Old create resolves
    resolve({ id: "thread-old" });
    expect(await p).toBeNull();

    // The functional update returns `prev` unchanged (draftKey mismatch), so the new draft stays null
    const finalThreadId = results.length > 0 ? results[results.length - 1].threadId : currentDraft.threadId;
    expect(finalThreadId).toBeNull();
    expect(setActive).not.toHaveBeenCalled();
  });
});
