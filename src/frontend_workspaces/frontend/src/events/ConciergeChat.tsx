import React, { useEffect, useRef, useState } from "react";
import {
  TextArea,
  Button,
  Toggle,
  InlineLoading,
  InlineNotification,
  Tag,
} from "@carbon/react";
import { Send } from "@carbon/icons-react";
import * as api from "../api";

/**
 * ConciergeChat — a DUMB chat surface over POST /api/concierge.
 *
 * It carries no business logic: it sends the text, shows the reply (or the
 * dry-run plan), and lists the exchange. All reuse/create/classify/arm
 * decisions happen server-side in the concierge. ``draft``/``setDraft`` are
 * lifted so the Examples tab can prefill this box.
 */
export interface ConciergeMessage {
  role: "user" | "concierge";
  text: string;
  meta?: string;
  /** HITL arming state: needs_input | confirm | armed | cancelled (absent for plain chat). */
  state?: string;
  /** The proposal shown at the CONFIRM gate: what will run, when, where it goes. */
  summary?: { trigger?: string; prompt?: string; delivery?: string; agent?: string };
  /** True for a message the server pushed later — an armed flow firing, not a reply to anything. */
  fire?: boolean;
}

/** The thread this surface talks on. It is also the delivery address a fire comes back to. */
const THREAD_ID = "web:studio";

export function ConciergeChat({
  draft,
  setDraft,
}: {
  draft: string;
  setDraft: (v: string) => void;
}) {
  const [messages, setMessages] = useState<ConciergeMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dryRun, setDryRun] = useState(false);
  /** Mailbox cursor — the ts of the last fire rendered. Ref, not state: it must not re-trigger the poll. */
  const cursor = useRef(0);

  // ── Asynchronous flow fires ─────────────────────────────────────────────────
  // Arming is only half the loop. The flow fires later — a cron tick at 09:05, a poll that finally
  // saw a change — with no request in flight to answer. Slack gets a push; a browser can only be
  // drained, so the server delivers into a per-thread mailbox and we poll it. Without this the flow
  // ran, the dashboard knew, and this chat never heard back.
  //
  // `since=0` on the first pass is deliberate: it recovers everything that fired while the tab was
  // closed, so a reopened Studio shows the fires it missed instead of losing them.
  useEffect(() => {
    let cancelled = false;
    const drain = async () => {
      try {
        const res = await api.getEventsInbox(THREAD_ID, cursor.current);
        if (!res.ok || cancelled) return;
        const data = await res.json();
        const msgs: any[] = data?.messages ?? [];
        if (!msgs.length || cancelled) return;
        cursor.current = data.cursor ?? cursor.current;
        setMessages((m) => [
          ...m,
          ...msgs.map((x) => ({
            role: "concierge" as const,
            text: String(x.text ?? ""),
            meta: x.flow_name ? `flow · ${x.flow_name}` : "flow fired",
            fire: true,
          })),
        ]);
      } catch {
        /* a mailbox that is unreachable must never break the chat */
      }
    };
    drain();
    const id = setInterval(drain, 15000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const send = async (override?: string) => {
    const text = (override ?? draft).trim();
    if (!text || busy) return;
    setError(null);
    setBusy(true);
    setMessages((m) => [...m, { role: "user", text }]);
    if (override === undefined) setDraft("");
    try {
      const res = await api.postConcierge(text, { threadId: THREAD_ID, dryRun });
      const data = await res.json();
      if (!res.ok && !data?.plan && !data?.reply) {
        throw new Error(data?.error || res.statusText);
      }
      // The server owns the shape: live → {reply}, dry-run → {decision, ...}.
      let reply: string;
      let meta: string | undefined;
      if (dryRun) {
        const mode = data?.decision?.mode ?? data?.plan?.decision?.mode ?? "?";
        reply = "```json\n" + JSON.stringify(data.decision ?? data.plan ?? data, null, 2) + "\n```";
        meta = `dry-run · mode=${mode}`;
      } else {
        reply = typeof data.reply === "string" ? data.reply : JSON.stringify(data.reply ?? data, null, 2);
        meta = data?.scope ? `scope=${data.scope}` : undefined;
      }
      // HITL arming: `state` says whether this thread is mid-dialogue. When the server is at the
      // CONFIRM gate it also sends the proposal, which we render as a card with real buttons —
      // the whole point is that the human sees the exact fire-time prompt before anything arms.
      setMessages((m) => [
        ...m,
        { role: "concierge", text: reply, meta, state: data?.state, summary: data?.summary },
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Concierge request failed");
    } finally {
      setBusy(false);
    }
  };

  // The newest message that is part of the arming dialogue (fires arrive out-of-band and don't count).
  const lastDialogueIndex = messages.reduce((best, m, i) => (m.fire ? best : i), -1);

  return (
    <div className="studio-chat">
      <div className="studio-chat-log">
        {messages.length === 0 && (
          <p className="studio-muted">
            Tell the concierge what you want — e.g. <em>"every 1 minute send me new arXiv
            papers on mixture-of-experts"</em>. It reuses or creates a worker and arms the
            trigger. Toggle <strong>Preview</strong> to see the plan without side effects.
            <br />
            Or type <code>/automate &lt;what&gt;</code> — one command whose router picks
            push / cron / poll for you: <em>"/automate summarize new emails"</em> (push),
            <em>"/automate the market brief every weekday 8am"</em> (cron),
            <em>"/automate check bitcoin every 5 min on a move"</em> (poll).
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`studio-msg studio-msg-${m.role}${m.fire ? " studio-msg-fire" : ""}`}>
            <div className="studio-msg-role">
              {m.role === "user" ? "You" : m.fire ? "⚡ Flow" : "Concierge"}
              {m.meta && (
                <Tag type={m.role === "user" ? "gray" : m.fire ? "purple" : "green"} size="sm"
                     style={{ marginLeft: 8 }}>
                  {m.meta}
                </Tag>
              )}
            </div>
            {m.state === "confirm" && m.summary ? (
              // "live" is the newest message of the DIALOGUE — a flow firing mid-arming is not a
              // reply and must not retire the card the human is still looking at.
              <ArmConfirmCard summary={m.summary} busy={busy} live={i === lastDialogueIndex}
                              onSay={(t) => send(t)} setDraft={setDraft} />
            ) : (
              <pre className="studio-msg-text">{m.text}</pre>
            )}
          </div>
        ))}
        {busy && <InlineLoading description="Concierge is thinking…" />}
      </div>

      {error && (
        <InlineNotification kind="error" title="Error" subtitle={error} lowContrast
          onCloseButtonClick={() => setError(null)} />
      )}

      <div className="studio-chat-input">
        <TextArea
          labelText=""
          hideLabel
          placeholder="Ask the concierge…  (or /automate <what> to arm a flow)"
          rows={2}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              send();
            }
          }}
        />
        <div className="studio-chat-actions">
          <Toggle
            id="studio-dryrun"
            size="sm"
            labelText=""
            labelA="Live"
            labelB="Preview"
            toggled={dryRun}
            onToggle={(v: boolean) => setDryRun(v)}
          />
          <Button renderIcon={Send} onClick={() => send()} disabled={busy || !draft.trim()}>
            Send
          </Button>
        </div>
      </div>
    </div>
  );
}

/**
 * ArmConfirmCard — the CONFIRM gate, rendered.
 *
 * Nothing is armed until the human approves the exact prompt the agent will be handed on every
 * fire (the events docs (plans/SPLIT_AND_HITL_ARMING_SPEC.md) §5). The card exists so that prompt is
 * impossible to miss: it is the one thing a bad automation gets wrong, forever, silently.
 *
 * The buttons are shortcuts, not a separate protocol — each sends the same plain text a user could
 * type ("yes" / "cancel"), so web, Slack, Discord and Telegram all drive one dialogue. Only the
 * newest card stays interactive; older ones are history.
 */
function ArmConfirmCard({
  summary,
  busy,
  live,
  onSay,
  setDraft,
}: {
  summary: { trigger?: string; prompt?: string; delivery?: string; agent?: string };
  busy: boolean;
  live: boolean;
  onSay: (text: string) => void;
  setDraft: (v: string) => void;
}) {
  const rows: [string, string | undefined][] = [
    ["When", summary.trigger],
    ["Results go to", summary.delivery],
    ["Agent", summary.agent],
  ];
  return (
    <div className="studio-arm-card">
      <div className="studio-arm-title">Ready to arm — check this first</div>
      <div className="studio-arm-prompt-label">The agent will be asked, every time:</div>
      <blockquote className="studio-arm-prompt">{summary.prompt}</blockquote>
      <dl className="studio-arm-facts">
        {rows.map(([k, v]) =>
          v ? (
            <React.Fragment key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </React.Fragment>
          ) : null,
        )}
      </dl>
      {live ? (
        <div className="studio-arm-actions">
          <Button size="sm" disabled={busy} onClick={() => onSay("yes")}>
            Arm it
          </Button>
          <Button
            size="sm"
            kind="tertiary"
            disabled={busy}
            onClick={() => setDraft(`change the prompt to ${summary.prompt ?? ""}`)}
          >
            Edit prompt
          </Button>
          <Button size="sm" kind="ghost" disabled={busy} onClick={() => onSay("cancel")}>
            Cancel
          </Button>
        </div>
      ) : (
        <p className="studio-muted studio-arm-stale">Superseded by a later message.</p>
      )}
    </div>
  );
}
