/*
 *  Copyright IBM Corp. 2026
 *
 *  Renders the slash-suggestions chip that appears inline in the chat
 *  transcript:
 *
 *    - SlashSuggestionsChip: clickable suggestion chips emitted when an
 *      unknown slash command produced semantic matches.
 *
 *  The resolved-skill invocation no longer renders as a separate bubble —
 *  it is now pushed into the assistant message's reasoning panel as a
 *  "Skill invoked: /<name>" step (see customSendMessage.ts /
 *  customLoadHistory.ts). That keeps invocation audit info adjacent to the
 *  planner reasoning it triggered instead of looking like tool-call debug.
 *
 *  RENDERING APPROACH — custom message items via `renderUserDefinedResponse`.
 *  Carbon AI Chat supports a `user_defined` generic response type whose
 *  rendering is delegated back to the host app. We push a `user_defined` item
 *  (tagged with a `cuga_kind` discriminator) from both the live turn
 *  (customSendMessage.ts) and history reload (customLoadHistory.ts), and
 *  Carbon calls `renderCugaUserDefinedResponse` below to render it.
 *
 *  Content that belongs in the message stream goes through this custom-item
 *  path rather than the portal/MutationObserver overlay pattern used by the
 *  autocomplete dropdown: Carbon owns the lifecycle, the chip renders inline
 *  in message order, and history reload replays it through the exact same
 *  renderer with zero shadow-DOM traversal.
 */
import React from "react";
import type { ChatInstance, RenderUserDefinedState } from "@carbon/ai-chat";
import { findComposerTextarea, setComposerTextareaValue } from "./composerTextarea";

/** Discriminator stored on the `user_defined` payload of our custom items. */
export const CUGA_USER_DEFINED_KIND = {
  SLASH_SUGGESTIONS: "cuga_slash_suggestions",
} as const;

/** A single semantic suggestion for an unknown slash command. */
export interface SlashSuggestion {
  name: string;
  kind: "skill" | "builtin";
  description: string;
  score: number;
}

/** Payload shape for the `SlashSuggestions` chip. */
export interface SlashSuggestionsChipData {
  cuga_kind: typeof CUGA_USER_DEFINED_KIND.SLASH_SUGGESTIONS;
  raw_input: string;
  suggestions: SlashSuggestion[];
}

type CugaUserDefinedData = SlashSuggestionsChipData;

/**
 * Clickable suggestion chips for an unknown slash command. Clicking a chip
 * drops `/<name> ` (trailing space) into the composer so the user can
 * correct their typo in one click. The composer is located + written via
 * the shared ./composerTextarea helpers, sharing the traversal used by the
 * autocomplete dropdown.
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
  return kind === CUGA_USER_DEFINED_KIND.SLASH_SUGGESTIONS;
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
  return <SlashSuggestionsChip data={payload} />;
}
