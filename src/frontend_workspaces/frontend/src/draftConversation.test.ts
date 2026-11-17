import { describe, expect, it, vi } from "vitest";
import { createDraftConversation, restoredServerThreadId } from "./draftConversation";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

describe("draft conversation continuity", () => {
  it("restores only explicitly server-issued IDs for the same agent", () => {
    const draft = { threadId: "uploaded-draft", agentId: "agent-a", hasSentFirstMessage: false };
    expect(restoredServerThreadId(draft, "agent-a")).toBeNull();
    expect(restoredServerThreadId({ ...draft, serverIssued: true }, "agent-a")).toBe("uploaded-draft");
    expect(restoredServerThreadId({ ...draft, serverIssued: true }, "agent-b")).toBeNull();
    expect(restoredServerThreadId({ ...draft, serverIssued: true, hasSentFirstMessage: true }, "agent-a")).toBeNull();
  });

  it("validates an empty restored draft and reuses it for send and upload", async () => {
    const validate = vi.fn(async () => true);
    const create = vi.fn(async () => "unexpected");
    const draft = createDraftConversation("uploaded-draft", validate, create, vi.fn());
    expect(await draft.validate()).toBe("uploaded-draft");
    expect(await draft.resolve()).toBe("uploaded-draft");
    expect(validate).toHaveBeenCalledWith("uploaded-draft");
    expect(validate).toHaveBeenCalledTimes(1);
    expect(create).not.toHaveBeenCalled();
  });

  it("waits for an upload's creation before sending, and adopts before publishing", async () => {
    const creation = deferred<string>();
    const order: string[] = [];
    const create = vi.fn(() => creation.promise);
    const draft = createDraftConversation(null, vi.fn(), create, (id) => order.push(`publish:${id}`));
    const upload = draft.resolve();
    const send = draft.resolve((id) => order.push(`send:${id}`));
    await Promise.resolve();
    expect(order).toEqual([]);
    creation.resolve("server-id");
    expect(await Promise.all([upload, send])).toEqual(["server-id", "server-id"]);
    expect(create).toHaveBeenCalledTimes(1);
    expect(order).toEqual(["send:server-id", "publish:server-id"]);
  });

  it("shares a reload validation still in flight with the first send", async () => {
    const check = deferred<boolean>();
    const validate = vi.fn(() => check.promise);
    const create = vi.fn();
    const draft = createDraftConversation("saved", validate, create, vi.fn());
    const restoring = draft.validate();
    const sending = draft.resolve();
    check.resolve(true);
    expect(await Promise.all([restoring, sending])).toEqual(["saved", "saved"]);
    expect(validate).toHaveBeenCalledTimes(1);
    expect(create).not.toHaveBeenCalled();
  });

  it("retries a failed creation without reserving a second ID concurrently", async () => {
    const create = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue("retry-id");
    const draft = createDraftConversation(null, vi.fn(), create, vi.fn());
    await expect(draft.resolve()).rejects.toThrow("offline");
    expect(await draft.resolve()).toBe("retry-id");
    expect(create).toHaveBeenCalledTimes(2);
  });

  it("replaces a missing/retired ID only after validation", async () => {
    const publish = vi.fn();
    const create = vi.fn(async () => "replacement");
    const draft = createDraftConversation("retired", async () => false, create, publish);
    expect(await draft.validate()).toBeNull();
    expect(create).not.toHaveBeenCalled();
    expect(await draft.resolve()).toBe("replacement");
    expect(publish).toHaveBeenCalledWith("replacement");
  });

  it("preserves the ID and retries validation on a transient server failure", async () => {
    const validate = vi.fn().mockRejectedValueOnce(new Error("503")).mockResolvedValue(true);
    const create = vi.fn();
    const draft = createDraftConversation("saved", validate, create, vi.fn());
    await expect(draft.resolve()).rejects.toThrow("503");
    expect(await draft.resolve()).toBe("saved");
    expect(create).not.toHaveBeenCalled();
  });

  it("an older draft failure cannot clear a new draft's pending creation", async () => {
    const oldCreation = deferred<string>();
    const newCreation = deferred<string>();
    const oldDraft = createDraftConversation(null, vi.fn(), () => oldCreation.promise, vi.fn());
    const create = vi.fn(() => newCreation.promise);
    const newDraft = createDraftConversation(null, vi.fn(), create, vi.fn());
    const oldUpload = oldDraft.resolve();
    const oldFailure = expect(oldUpload).rejects.toThrow("old failure");
    const newUpload = newDraft.resolve();
    oldCreation.reject(new Error("old failure"));
    await oldFailure;
    const newSend = newDraft.resolve();
    newCreation.resolve("new-id");
    expect(await Promise.all([newUpload, newSend])).toEqual(["new-id", "new-id"]);
    expect(create).toHaveBeenCalledTimes(1);
  });
});
