export interface DraftIdentity {
  agentId: string;
  draftKey: string;
}

export function isCurrentDraft(
  started: DraftIdentity,
  current: DraftIdentity,
): boolean {
  return started.agentId === current.agentId && started.draftKey === current.draftKey;
}

export interface ConversationCreationRef {
  current: (DraftIdentity & { promise: Promise<string | null> }) | null;
}

export async function ensureDraftConversationId(
  existingThreadId: string | null,
  identity: DraftIdentity,
  creationRef: ConversationCreationRef,
  create: (agentId: string) => Promise<{ id: string }>,
  getCurrent: () => DraftIdentity,
  apply: (threadId: string, identity: DraftIdentity) => void,
): Promise<string | null> {
  const existing = existingThreadId?.trim();
  if (existing) return existing;
  if (creationRef.current && isCurrentDraft(identity, creationRef.current)) {
    return creationRef.current.promise;
  }
  const promise = (async () => {
    let applied = false;
    try {
      const created = await create(identity.agentId);
      if (!isCurrentDraft(identity, getCurrent())) return null;
      apply(created.id, identity);
      applied = true;
      return created.id;
    } catch (error) {
      console.error("Failed to create conversation", error);
      return null;
    } finally {
      // Keep a successful result until React has rendered the new thread ID.
      // A send can arrive in the gap after creation resolves but before that
      // render; clearing the ref here would create a second server thread.
      if (!applied && creationRef.current && isCurrentDraft(identity, creationRef.current)) {
        creationRef.current = null;
      }
    }
  })();
  creationRef.current = { ...identity, promise };
  return promise;
}

/** The conversation a send was started in is no longer on screen; drop the send. */
export class SendSupersededError extends Error {
  constructor(message = "Conversation is no longer available for this message") {
    super(message);
    this.name = "SendSupersededError";
  }
}

export async function resolveThreadIdForSend(
  resolve: (() => Promise<string | null>) | undefined,
  currentThreadId: string | null,
): Promise<string | null> {
  if (!resolve) return currentThreadId;
  const threadId = await resolve();
  if (!threadId) throw new SendSupersededError();
  return threadId;
}

export type HistoryLoadDecision = "load" | "skip" | "reset";

/**
 * Decides what a `threadId` prop change means for the visible transcript.
 *
 * A first send on a draft allocates the conversation ID while the message is
 * already on screen; the parent then passes that ID back as a new prop. That
 * promotion must keep the live transcript, while navigation to another
 * conversation must still clear it and load that conversation's history.
 */
export class HistoryLoadGuard {
  private skipThreadId: string | null = null;
  // Only the most recent promotion may end it; an older send that settles
  // late (e.g. after New chat and another first send) must not release it.
  private activeToken: object | null = null;
  private deferredThreadId: string | null = null;

  /** Call before resolving the thread ID for a send on a draft without one. */
  beginPromotion(): object {
    const token = {};
    this.activeToken = token;
    this.deferredThreadId = null;
    return token;
  }

  /**
   * Ends the promotion owned by `token`. Returns a thread ID whose history
   * still has to be loaded (the prop moved to a different conversation
   * meanwhile), or null. A superseded token changes nothing.
   */
  endPromotion(token: object, promotedThreadId: string | null): string | null {
    if (token !== this.activeToken) return null;
    this.activeToken = null;
    const deferred = this.deferredThreadId;
    this.deferredThreadId = null;
    if (deferred === null) {
      // The prop change has not rendered yet; let it through without a reload.
      if (promotedThreadId) this.skipThreadId = promotedThreadId;
      return null;
    }
    return deferred === promotedThreadId ? null : deferred;
  }

  /** Keep the transcript when the parent echoes this ID back after a send. */
  skipNext(threadId: string): void {
    this.skipThreadId = threadId;
  }

  onThreadIdChange(threadId: string | null): HistoryLoadDecision {
    if (!threadId) {
      // A fresh draft: any pending promotion belongs to the old one.
      this.activeToken = null;
      this.skipThreadId = null;
      this.deferredThreadId = null;
      return "reset";
    }
    if (this.activeToken) {
      this.deferredThreadId = threadId;
      return "skip";
    }
    if (this.skipThreadId === threadId) {
      this.skipThreadId = null;
      return "skip";
    }
    this.skipThreadId = null;
    return "load";
  }
}

/**
 * Resolves the stream thread ID for a send. On a draft without an ID, the
 * guard holds history reloads while the ID is allocated. If the user opened
 * another conversation meanwhile, `loadHistory` shows it and the send is
 * dropped with `SendSupersededError` so it cannot stream into that view.
 */
export async function resolveSendThreadIdKeepingTranscript(
  guard: HistoryLoadGuard,
  resolve: (() => Promise<string | null>) | undefined,
  currentThreadId: string | null,
  loadHistory: (threadId: string) => void,
): Promise<string | null> {
  if (currentThreadId || !resolve) return resolveThreadIdForSend(resolve, currentThreadId);
  const token = guard.beginPromotion();
  let promoted: string | null = null;
  let failure: unknown = null;
  try {
    promoted = await resolveThreadIdForSend(resolve, currentThreadId);
  } catch (error) {
    failure = error;
  }
  const navigatedTo = guard.endPromotion(token, promoted);
  if (navigatedTo) {
    loadHistory(navigatedTo);
    throw new SendSupersededError("Conversation changed before this message was sent");
  }
  if (failure) throw failure;
  return promoted;
}
