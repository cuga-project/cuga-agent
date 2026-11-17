/** Older builds persisted client-generated IDs without this provenance marker. */
export function restoredServerThreadId(value: unknown, agentId: string): string | null {
  if (!value || typeof value !== "object") return null;
  const draft = value as Record<string, unknown>;
  return draft.serverIssued === true && draft.agentId === agentId
    && draft.hasSentFirstMessage === false && typeof draft.threadId === "string"
    && draft.threadId.trim() !== "" ? draft.threadId : null;
}

/** Share validation/creation between uploads and the first send for one draft. */
export function createDraftConversation(
  restoredId: string | null,
  validateId: (id: string) => Promise<boolean>,
  createId: () => Promise<string>,
  publish: (id: string) => void,
) {
  let id = restoredId;
  let validated = !restoredId;
  let checking: Promise<string | null> | null = null;
  let pending: Promise<string> | null = null;
  const listeners = new Set<(id: string) => void>();
  const validate = (): Promise<string | null> => {
    if (validated) return Promise.resolve(id);
    if (!checking) {
      checking = validateId(id!).then((valid) => {
        if (!valid) id = null;
        validated = true;
        return id;
      }).finally(() => { checking = null; });
    }
    return checking;
  };
  const resolve = (beforePublish?: (id: string) => void): Promise<string> => {
    if (beforePublish) listeners.add(beforePublish);
    if (!pending) {
      pending = validate().then(async (existing) => existing ?? await createId()).then((resolved) => {
        id = resolved;
        // The send adopts the ID before publishing it as a prop triggers history reconciliation.
        listeners.forEach((listener) => listener(resolved));
        publish(resolved);
        return resolved;
      }).finally(() => { pending = null; listeners.clear(); });
    }
    return pending;
  };
  const prepareSend = async (beforePublish?: (id: string) => void): Promise<string | null> => {
    if (pending) return resolve(beforePublish);
    const existing = await validate();
    // An upload can start while restored ownership is being checked.
    if (pending) return resolve(beforePublish);
    if (existing) beforePublish?.(existing);
    return existing;
  };
  return { validate, resolve, prepareSend };
}
