// The conversation the chat is showing, shared by CarbonChat and customSendMessage.
// Null on a new chat: /stream then issues the ID and announces it with thread_created.

let currentThreadId: string | null = null;
// Bumped whenever the visible conversation changes, so a send started in one
// conversation cannot adopt its server-issued ID into another.
let generation = 0;

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

/** Adopt the ID /stream issued for a new chat, if the user is still on that chat. */
export function adoptIssuedThreadId(threadId: string, startedAt: number): void {
  if (startedAt === generation && !currentThreadId) currentThreadId = threadId;
}
