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

  const send = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    setError(null);
    setBusy(true);
    setMessages((m) => [...m, { role: "user", text }]);
    setDraft("");
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
      setMessages((m) => [...m, { role: "concierge", text: reply, meta }]);
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
            <pre className="studio-msg-text">{m.text}</pre>
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
          placeholder="Ask the concierge…"
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
          <Button renderIcon={Send} onClick={send} disabled={busy || !draft.trim()}>
            Send
          </Button>
        </div>
      </div>
    </div>
  );
}
