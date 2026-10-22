import { describe, expect, it, vi } from "vitest";
import {
  ensureDraftConversationId,
  HistoryLoadGuard,
  isCurrentDraft,
  resolveSendThreadIdKeepingTranscript,
  resolveThreadIdForSend,
  SendSupersededError,
} from "../../frontend/src/conversationIdentity";

describe("conversation identity", () => {
  it("rejects a late creation result after New chat under the same agent", () => {
    const pending = { agentId: "agent-a", draftKey: "old-draft" };
    const current = { agentId: "agent-a", draftKey: "new-draft" };
    expect(isCurrentDraft(pending, current)).toBe(false);
    expect(isCurrentDraft(current, current)).toBe(true);
  });

  it("waits for a pending create before selecting the stream thread", async () => {
    let completeCreate!: (id: string) => void;
    const create = new Promise<string>((resolve) => { completeCreate = resolve; });
    const ensure = vi.fn(async () => create);
    const sendThread = resolveThreadIdForSend(ensure, null);

    expect(ensure).toHaveBeenCalledTimes(1);
    completeCreate("created-thread");
    expect(await sendThread).toBe("created-thread");
  });

  it("aborts a send when its draft was replaced during creation", async () => {
    await expect(resolveThreadIdForSend(async () => null, null)).rejects.toThrow(
      "Conversation is no longer available",
    );
  });

  it("deduplicates an upload and first send for the same draft", async () => {
    let completeCreate!: (value: { id: string }) => void;
    const create = vi.fn(() => new Promise<{ id: string }>((resolve) => { completeCreate = resolve; }));
    const identity = { agentId: "agent-a", draftKey: "draft-a" };
    const ref = { current: null };
    const apply = vi.fn();

    const upload = ensureDraftConversationId(null, identity, ref, create, () => identity, apply);
    const send = ensureDraftConversationId(null, identity, ref, create, () => identity, apply);
    expect(create).toHaveBeenCalledTimes(1);

    completeCreate({ id: "one-server-thread" });
    expect(await upload).toBe("one-server-thread");
    expect(await send).toBe("one-server-thread");
    expect(await ensureDraftConversationId(null, identity, ref, create, () => identity, apply)).toBe(
      "one-server-thread",
    );
    expect(create).toHaveBeenCalledTimes(1);
    expect(apply).toHaveBeenCalledOnce();
    expect(apply).toHaveBeenCalledWith("one-server-thread", identity);
  });

  it("ignores a late create from the previous draft of the same agent", async () => {
    let completeCreate!: (value: { id: string }) => void;
    const create = vi.fn(() => new Promise<{ id: string }>((resolve) => { completeCreate = resolve; }));
    const oldDraft = { agentId: "agent-a", draftKey: "old" };
    let currentDraft = oldDraft;
    const apply = vi.fn();
    const ref = { current: null };

    const pending = ensureDraftConversationId(null, oldDraft, ref, create, () => currentDraft, apply);
    currentDraft = { agentId: "agent-a", draftKey: "new" };
    completeCreate({ id: "stale-thread" });

    expect(await pending).toBeNull();
    expect(apply).not.toHaveBeenCalled();
  });
});

// Mirrors CarbonChat: the history effect clears the transcript on "load" and
// "reset"; a first send resolves its thread ID through the guard.
function chatHarness() {
  const guard = new HistoryLoadGuard();
  const clearConversation = vi.fn();
  const loadHistory = vi.fn((threadId: string) => { clearConversation(threadId); });
  const renderThreadId = (threadId: string | null) => {
    const decision = guard.onThreadIdChange(threadId);
    if (decision === "load" && threadId) loadHistory(threadId);
    if (decision === "reset") clearConversation(null);
    return decision;
  };
  return { guard, clearConversation, loadHistory, renderThreadId };
}

describe("first-send ID promotion keeps the live transcript", () => {
  it("does not clear when the created ID renders while the send is pending", async () => {
    const chat = chatHarness();
    chat.renderThreadId(null);
    chat.clearConversation.mockClear();

    let completeCreate!: (value: { id: string }) => void;
    const identity = { agentId: "agent-a", draftKey: "draft-a" };
    const ensure = () => ensureDraftConversationId(
      null,
      identity,
      { current: null },
      () => new Promise<{ id: string }>((resolve) => { completeCreate = resolve; }),
      () => identity,
      // Parent sets activeThreadId; CarbonChat re-renders with the new prop.
      (threadId) => { chat.renderThreadId(threadId); },
    );

    const send = resolveSendThreadIdKeepingTranscript(chat.guard, ensure, null, chat.loadHistory);
    completeCreate({ id: "created-thread" });

    expect(await send).toBe("created-thread");
    expect(chat.clearConversation).not.toHaveBeenCalled();
    // Post-stream echo of the same ID still keeps the transcript.
    chat.guard.skipNext("created-thread");
    expect(chat.renderThreadId("created-thread")).toBe("skip");
    expect(chat.clearConversation).not.toHaveBeenCalled();
  });

  it("does not clear when the created ID renders after the send resolved it", async () => {
    const chat = chatHarness();
    const sendThreadId = await resolveSendThreadIdKeepingTranscript(
      chat.guard, async () => "created-thread", null, chat.loadHistory,
    );

    expect(sendThreadId).toBe("created-thread");
    expect(chat.renderThreadId("created-thread")).toBe("skip");
    expect(chat.clearConversation).not.toHaveBeenCalled();
  });

  it("drops the send and shows the opened conversation when the user navigates during creation", async () => {
    const chat = chatHarness();
    const stream = vi.fn();
    let completeCreate!: (id: string) => void;
    const ensure = () => new Promise<string | null>((resolve) => { completeCreate = resolve; });

    // Mirrors CarbonChat.handleCustomSendMessage: stream only with a resolved ID.
    const send = resolveSendThreadIdKeepingTranscript(chat.guard, ensure, null, chat.loadHistory)
      .then((threadId) => stream(threadId));
    expect(chat.renderThreadId("other-thread")).toBe("skip");
    completeCreate("created-thread");

    await expect(send).rejects.toBeInstanceOf(SendSupersededError);
    expect(stream).not.toHaveBeenCalled();
    expect(chat.loadHistory).toHaveBeenCalledOnce();
    expect(chat.loadHistory).toHaveBeenCalledWith("other-thread");
  });

  it("an older draft's creation settling late cannot clear the newer draft's transcript", async () => {
    const chat = chatHarness();
    let settleA!: (id: string | null) => void;
    let settleB!: (id: string | null) => void;

    const sendA = resolveSendThreadIdKeepingTranscript(
      chat.guard, () => new Promise((resolve) => { settleA = resolve; }), null, chat.loadHistory,
    );
    // New chat: CarbonChat already shows null, so the prop does not change; draft B sends.
    const sendB = resolveSendThreadIdKeepingTranscript(
      chat.guard, () => new Promise((resolve) => { settleB = resolve; }), null, chat.loadHistory,
    );

    settleA(null); // A's draft was replaced
    await expect(sendA).rejects.toBeInstanceOf(SendSupersededError);

    // B's ID renders while B is still pending: must not reload.
    expect(chat.renderThreadId("thread-b")).toBe("skip");
    settleB("thread-b");
    expect(await sendB).toBe("thread-b");
    expect(chat.clearConversation).not.toHaveBeenCalled();
    expect(chat.loadHistory).not.toHaveBeenCalled();
  });

  it("an older promotion that resolves late cannot arm a skip for the newer draft", async () => {
    const chat = chatHarness();
    let settleA!: (id: string | null) => void;
    const sendA = resolveSendThreadIdKeepingTranscript(
      chat.guard, () => new Promise((resolve) => { settleA = resolve; }), null, chat.loadHistory,
    );
    const sendB = resolveSendThreadIdKeepingTranscript(
      chat.guard, async () => "thread-b", null, chat.loadHistory,
    );
    expect(await sendB).toBe("thread-b");
    settleA("thread-a");
    expect(await sendA).toBe("thread-a");

    // A superseded token changes nothing: a later visit to thread-a loads it.
    expect(chat.renderThreadId("thread-b")).toBe("skip");
    expect(chat.renderThreadId("thread-a")).toBe("load");
  });

  it("does not arm a skip when creation fails", async () => {
    const chat = chatHarness();
    await expect(
      resolveSendThreadIdKeepingTranscript(chat.guard, async () => null, null, chat.loadHistory),
    ).rejects.toThrow("Conversation is no longer available");

    expect(chat.renderThreadId("some-thread")).toBe("load");
    expect(chat.loadHistory).toHaveBeenCalledWith("some-thread");
  });

  it("still reloads history on ordinary navigation", async () => {
    const chat = chatHarness();
    const ensure = vi.fn(async () => "existing-thread");
    expect(
      await resolveSendThreadIdKeepingTranscript(chat.guard, ensure, "existing-thread", chat.loadHistory),
    ).toBe("existing-thread");

    expect(chat.renderThreadId("another-thread")).toBe("load");
    expect(chat.loadHistory).toHaveBeenCalledWith("another-thread");
  });
});
