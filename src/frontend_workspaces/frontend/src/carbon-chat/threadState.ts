// The conversation the chat is showing, shared by CarbonChat and customSendMessage.
// Null on a new chat: /stream then issues the ID and announces it with thread_created.

let currentThreadId: string | null = null;
// Bumped whenever the visible conversation changes, so a send started in one
// conversation cannot adopt its server-issued ID into another.
let generation = 0;
const issuedListeners = new Set<(threadId: string) => void>();

export function getCurrentThreadId(): string | null {
  return currentThreadId;
}

export function getThreadGeneration(): number {
  return generation;
}

/** Switch to another conversation (or a new chat, with null). */
export function showThread(threadId: string | null): void {
  currentThreadId = threadId;
  generation += 1;
}

/** Record the ID of the conversation already on screen, e.g. echoed back by the parent. */
export function keepThread(threadId: string | null): void {
  currentThreadId = threadId;
}

/** Listen for a new chat adopting its server-issued ID; returns the unsubscribe function. */
export function onThreadIssued(listener: (threadId: string) => void): () => void {
  issuedListeners.add(listener);
  return () => {
    issuedListeners.delete(listener);
  };
}

/**
 * Adopt the ID /stream issued for a new chat, if the user is still on that chat. Listeners hear
 * about it at once, not at stream end, so uploads and "New conversation" during the first answer
 * see the real conversation.
 */
export function adoptIssuedThreadId(threadId: string, startedAt: number): boolean {
  if (startedAt !== generation || currentThreadId) return false;
  currentThreadId = threadId;
  issuedListeners.forEach((listener) => listener(threadId));
  return true;
}
