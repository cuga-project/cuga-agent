/*
 *  Copyright IBM Corp. 2026
 *
 *  Renders the two slash-command chips that appear inline in the chat
 *  transcript:
 *
 *    - SlashSkillChip (slice #22): a collapsed "⚡ /skill" chip emitted when a
 *      slash command resolved to a skill. Click to expand the audit details.
 *    - SlashSuggestionsChip (slice #23): clickable suggestion chips emitted
 *      when an unknown slash command produced semantic matches.
 *
 *  RENDERING APPROACH — custom message items via `renderUserDefinedResponse`.
 *  Carbon AI Chat supports a `user_defined` generic response type whose
 *  rendering is delegated back to the host app. We push a `user_defined` item
 *  (tagged with a `cuga_kind` discriminator) from both the live turn
 *  (customSendMessage.ts) and history reload (customLoadHistory.ts), and
 *  Carbon calls `renderCugaUserDefinedResponse` below to render it.
 *
 *  This is preferred over the slice #18 portal/MutationObserver decoration
 *  pattern: that pattern is the right tool for *overlays on the composer*,
 *  but for *content that belongs in the message stream* the custom-item path
 *  is far more robust — Carbon owns the lifecycle, the chip renders inline in
 *  message order, and history reload replays it through the exact same
 *  renderer with zero shadow-DOM traversal.
 */
import React, { useState } from "react";
import type { ChatInstance, RenderUserDefinedState } from "@carbon/ai-chat";
import { findComposerTextarea, setComposerTextareaValue } from "./composerTextarea";

/** Discriminator stored on the `user_defined` payload of our custom items. */
export const CUGA_USER_DEFINED_KIND = {
  SLASH_SKILL: "cuga_slash_skill",
  SLASH_SUGGESTIONS: "cuga_slash_suggestions",
} as const;

/** Payload shape for the `SlashSkillInvoked` chip (slice #22). */
export interface SlashSkillChipData {
  cuga_kind: typeof CUGA_USER_DEFINED_KIND.SLASH_SKILL;
  resolved_name: string;
  raw_input: string;
  raw_args: string;
}

/** A single semantic suggestion (slice #23). */
export interface SlashSuggestion {
  name: string;
  kind: "skill" | "builtin";
  description: string;
  score: number;
}

/** Payload shape for the `SlashSuggestions` chip (slice #23). */
export interface SlashSuggestionsChipData {
  cuga_kind: typeof CUGA_USER_DEFINED_KIND.SLASH_SUGGESTIONS;
  raw_input: string;
  suggestions: SlashSuggestion[];
}

type CugaUserDefinedData = SlashSkillChipData | SlashSuggestionsChipData;

/**
 * Slice #22 — collapsed chip for a resolved slash skill invocation.
 *
 * Collapsed by default; clicking toggles an expanded panel showing the
 * verbatim raw_input, the resolved skill name, and raw_args.
 *
 * NOTE: the full wrapped skill body is intentionally NOT shipped to the
 * browser (it is large). Surfacing the full body in the expanded view is a
 * deferred enhancement — raw_input / resolved_name / raw_args is enough to
 * audit what was invoked.
 */
function SlashSkillChip({ data }: { data: SlashSkillChipData }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="cuga-slash-skill-chip">
      <button
        type="button"
        className="cuga-slash-skill-chip__summary"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
        title={expanded ? "Hide invocation details" : "Show invocation details"}
      >
        <span className="cuga-slash-skill-chip__bolt" aria-hidden="true">
          ⚡
        </span>
        <span className="cuga-slash-skill-chip__name">/{data.resolved_name}</span>
        <span className="cuga-slash-skill-chip__caret" aria-hidden="true">
          {expanded ? "▾" : "▸"}
        </span>
      </button>
      {expanded && (
        <dl className="cuga-slash-skill-chip__details">
          <dt>Input</dt>
          <dd>
            <code>{data.raw_input}</code>
          </dd>
          <dt>Resolved skill</dt>
          <dd>
            <code>{data.resolved_name}</code>
          </dd>
          <dt>Arguments</dt>
          <dd>
            <code>{data.raw_args || "(none)"}</code>
          </dd>
        </dl>
      )}
    </div>
  );
}

/**
 * Slice #23 — clickable suggestion chips for an unknown slash command.
 *
 * Clicking a chip drops `/<name> ` (trailing space) into the composer
 * textarea so the user can correct their typo in one click. The textarea is
 * located + written via the shared ./composerTextarea helpers — the exact
 * mechanism slice #18's autocomplete dropdown uses.
 */
function SlashSuggestionsChip({ data }: { data: SlashSuggestionsChipData }) {
  const applySuggestion = (name: string) => {
    const textarea = findComposerTextarea(null);
    setComposerTextareaValue(textarea, `/${name} `);
  };

  if (!data.suggestions || data.suggestions.length === 0) {
    return null;
  }

  return (
    <div className="cuga-slash-suggestions">
      <div className="cuga-slash-suggestions__lead">
        Unknown command <code>{data.raw_input}</code>. Did you mean:
      </div>
      <div className="cuga-slash-suggestions__chips">
        {data.suggestions.map((s) => (
          <button
            key={`${s.kind}-${s.name}`}
            type="button"
            className={`cuga-slash-suggestion cuga-slash-suggestion--${s.kind}`}
            onClick={() => applySuggestion(s.name)}
            title={s.description || `Use /${s.name}`}
          >
            <span className="cuga-slash-suggestion__name">/{s.name}</span>
            {s.description && (
              <span className="cuga-slash-suggestion__description">
                {s.description}
              </span>
            )}
            <span
              className={`cuga-slash-suggestion__kind cuga-slash-suggestion__kind--${s.kind}`}
            >
              {s.kind === "skill" ? "skill" : "built-in"}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

/** Type guard for our tagged `user_defined` payloads. */
function isCugaUserDefined(value: unknown): value is CugaUserDefinedData {
  if (!value || typeof value !== "object") return false;
  const kind = (value as { cuga_kind?: unknown }).cuga_kind;
  return (
    kind === CUGA_USER_DEFINED_KIND.SLASH_SKILL ||
    kind === CUGA_USER_DEFINED_KIND.SLASH_SUGGESTIONS
  );
}

/**
 * Dispatcher wired into `ChatCustomElement`'s `renderUserDefinedResponse`.
 * Carbon calls this for every `user_defined` response item (both live and
 * replayed from history); we render our chips and return `null` for anything
 * that isn't ours so Carbon's default handling is unaffected.
 */
export function renderCugaUserDefinedResponse(
  state: RenderUserDefinedState,
  _instance: ChatInstance,
): React.ReactNode {
  const payload = state.messageItem?.user_defined;
  if (!isCugaUserDefined(payload)) {
    return null;
  }
  if (payload.cuga_kind === CUGA_USER_DEFINED_KIND.SLASH_SKILL) {
    return <SlashSkillChip data={payload} />;
  }
  return <SlashSuggestionsChip data={payload} />;
}
