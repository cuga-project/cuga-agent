import React, { useState } from "react";
import {
  TextArea,
  Button,
  Toggle,
  InlineLoading,
  InlineNotification,
  Tag,
} from "@carbon/react";
import { Send } from "@carbon/icons-react";
import * as api from "./api";

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
}

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

  const send = async (override?: string) => {
    const text = (override ?? draft).trim();
    if (!text || busy) return;
    setError(null);
    setBusy(true);
    setMessages((m) => [...m, { role: "user", text }]);
    if (override === undefined) setDraft("");
    try {
      const res = await api.postConcierge(text, { threadId: "web:studio", dryRun });
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
          <div key={i} className={`studio-msg studio-msg-${m.role}`}>
            <div className="studio-msg-role">
              {m.role === "user" ? "You" : "Concierge"}
              {m.meta && (
                <Tag type={m.role === "user" ? "gray" : "green"} size="sm" style={{ marginLeft: 8 }}>
                  {m.meta}
                </Tag>
              )}
            </div>
            {m.state === "confirm" && m.summary ? (
              <ArmConfirmCard summary={m.summary} busy={busy} live={i === messages.length - 1}
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
 * fire (events_docs/plans/SPLIT_AND_HITL_ARMING_SPEC.md §5). The card exists so that prompt is
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
