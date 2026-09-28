/*
 *  Copyright IBM Corp. 2025
 *
 *  This source code is licensed under the Apache-2.0 license found in the
 *  LICENSE file in the root directory of this source tree.
 *
 *  @license
 */
import { useEffect } from "react";
import { MessageResponseTypes, type ChatInstance, type MessageResponse } from "@carbon/ai-chat";
import * as api from "../api";

/** Drain armed-flow fires into a chat transcript.
 *
 * THE EVENTS LAYER'S ONLY FOOTPRINT IN CUGA'S MAIN CHAT. Arming a flow is half the loop — it fires
 * LATER, when no request is in flight to answer into. Slack and Discord get pushed into; a browser
 * can only be drained, so the server writes each fire to a per-thread mailbox and this polls it.
 * Without it the flow runs, the dashboard knows, and the chat that armed it never hears back.
 *
 * Lives here rather than inside CarbonChat so the events UI is one directory: the chat calls this
 * in a single line and knows nothing about mailboxes, cursors or `/api/events/*`. Delete the events
 * layer and the one call goes with it.
 *
 * Deliberately the ONLY renderer of fires — history does not include them, so a reopened tab starts
 * at cursor 0, drains the backlog and appends it. Fires land at the END of the transcript rather
 * than interleaved by time, which is nearly always the true order anyway.
 */
export function useEventsInbox(
  threadId: string | null,
  chatInstanceRef: React.MutableRefObject<ChatInstance | null>,
  getResponseUserProfile: (useDraft?: boolean) => Promise<unknown>,
  useDraft?: boolean,
): void {
  useEffect(() => {
    // EVERY failure below used to be silent. `if (!threadId) return` and a bare `.catch(() => {})`
    // meant a poller that never started looked exactly like a poller with nothing to deliver: the
    // flow fired, the answer was written to the mailbox, and the chat showed nothing, with no way
    // to tell which of the two it was without reading the source. So each exit says so once.
    const say = (...a: unknown[]) => console.info("[events:inbox]", ...a);
    if (!threadId) {
      say("poller NOT started — this chat has no threadId yet; a fire will land in the mailbox but");
      say("nothing polls for it. Send a message first, then re-open the conversation.");
      return;
    }
    say("poller starting for thread", threadId);
    let cancelled = false;
    let cursor = 0;

    const drain = async () => {
      const instance = chatInstanceRef.current;
      if (!instance || cancelled) return;
      try {
        const res = await api.getEventsInbox(threadId, cursor);
        if (!res.ok || cancelled) return;
        const data = await res.json();
        const messages: any[] = data?.messages ?? [];
        if (!messages.length) return;
        cursor = data.cursor ?? cursor;
        const profile = await getResponseUserProfile(useDraft);
        for (const m of messages) {
          if (cancelled) break;
          await instance.messaging.addMessage({
            id: `fire-${m.id}`,
            output: {
              generic: [{ response_type: MessageResponseTypes.TEXT, text: String(m.text ?? '') }],
            },
            message_options: { response_user_profile: profile },
          } as MessageResponse);
        }
      } catch {
        /* an unreachable mailbox must never break the chat */
      }
    };

    // Only poll when the events layer is mounted — vanilla CUGA has no /api/events/*.
    let timer: ReturnType<typeof setInterval> | null = null;
    let first: ReturnType<typeof setTimeout> | null = null;
    api.getEventsStatus().then((s) => {
      if (cancelled) return;
      if (!s) {
        say("poller NOT started — /api/events/status did not answer with enabled:true.");
        say("Check it by hand:  await (await fetch(`${location.origin}/api/ui/config`)).json()");
        say("then GET <events_api_url>/api/events/status from this tab. A CORS error here is the");
        say("split-origin case: the events service must allow this origin (EVENTS_CORS_ORIGINS).");
        return;
      }
      // Hold the first drain briefly. Switching threads runs clearConversation() → insertHistory()
      // asynchronously; injecting a fire into that window gets it wiped by the clear.
      first = setTimeout(drain, 2500);
      timer = setInterval(drain, 15000);
    }).catch((e) => say("poller NOT started — /api/events/status threw:", e));
    return () => {
      cancelled = true;
      if (first) clearTimeout(first);
      if (timer) clearInterval(timer);
    };
  }, [threadId, useDraft]);
}
