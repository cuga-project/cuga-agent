/**
 * Knowledge harness panel — operator-supplied rules appended to the
 * knowledge-agent system prompt.
 *
 * (Internally this file is still ``ClientAdaptationPanel.tsx`` /
 * ``client_adaptation_*`` config keys — the backend/API names are
 * stable. Only the user-facing strings say "Knowledge harness", which
 * is the framing we ship to operators.)
 */

import React, { useMemo, useRef, useState } from "react";
import {
  Button,
  TextArea,
  TextInput,
  Tile,
  Tag,
  IconButton,
  InlineNotification,
  ActionableNotification,
  AILabel,
  AILabelContent,
} from "@carbon/react";
import { TrashCan, Add, Checkmark, Reset } from "@carbon/icons-react";
import ReactMarkdown from "react-markdown";

// Must match CLIENT_ADAPTATION_MAX_CHARS in src/cuga/backend/knowledge/config.py.
// Enforced server-side; mirrored here for instant feedback only.
export const CLIENT_ADAPTATION_MAX_CHARS = 3000;

// Unicode bidi-override codepoints — see _BIDI_OVERRIDE_CODEPOINTS in
// config.py. Mirrored client-side because it's a cheap, deterministic check
// that catches the prompt-smuggling vector at type-time before the operator
// hits Save.
const BIDI_OVERRIDE_RE = /[\u202A-\u202E\u2066-\u2069]/;

// Curated starting templates. Each one is an editable seed — picking a
// template appends it to the textarea; the result is plain editable text
// (no lock-in). ``marker`` is the substring used to detect "already added"
// for the small ✓ indicator; it's the body's section heading.
const TEMPLATES: Array<{
  id: string;
  title: string;
  description: string;
  body: string;
  marker: string;
}> = [
  {
    id: "token-preservation",
    title: "Token preservation",
    description:
      "Echo bracketed labels, menu paths, codes, and version strings verbatim.",
    marker: "## Token preservation",
    body:
      "## Token preservation\n\nWhen retrieval contains literal tokens shaped as `[Bracketed]`\n" +
      "labels, `A > B > C` paths, codes (`K3`, `9.150.1`), or quoted field\n" +
      "names, reproduce them character-for-character. Don't translate.\n",
  },
  {
    id: "answer-policy",
    title: "Answer policy (anti-hedging)",
    description:
      "Contrastive WRONG/RIGHT pairs that drill the verbatim-quote habit.",
    marker: "## Answer policy",
    body:
      "## Answer policy\n\nWhen retrieval contains a procedure, answer with the procedure.\n\n" +
      '- WRONG: "We did not find an explicit process — contact support."\n' +
      '- RIGHT: "Per <file>: open [Settings] > [Network], set Mode = Manual."\n' +
      '- WRONG: "click the save button"\n' +
      '- RIGHT: "click [Save]"\n' +
      '- WRONG: "code 3 or 4 — verify"\n' +
      '- RIGHT: "[code 3] for case A; [code 4] for case B (per docs)"\n\n' +
      "Hedging is correct ONLY when retrieval returned zero hits.\n",
  },
  {
    // Generic English starter built on WRONG/RIGHT contrastive pairs
    // against retrieved support docs. Without explicit examples, the
    // few_shot_wrong_right lever delivers ~0% of its modeled +0.40
    // quote-rate gain — keep them ON by default in the gallery.
    id: "support-runbook",
    title: "Support runbook quoting",
    description:
      "WRONG/RIGHT pairs for tech support — preserve [labels], codes, and menu paths.",
    marker: "## Support runbook",
    body:
      "## Support runbook — verbatim discipline\n\n" +
      '- WRONG: "use the network troubleshooter"\n' +
      '- RIGHT: "open [Settings] > [Network] > [Diagnostics]; click [Run Test]"\n' +
      '- WRONG: "error code 0x80004005 — check the docs"\n' +
      '- RIGHT: "error code 0x80004005 means TLS handshake failed; per <runbook.pdf>: rotate the cert via Vault and restart nginx"\n' +
      '- WRONG: "the procedure is not documented"\n' +
      "- RIGHT: quote the procedure from the retrieved chunk verbatim — bracketed labels, codes, and menu paths intact.\n",
  },
  {
    // Rewritten to mirror the WRONG/RIGHT shape used in Answer policy and
    // Support runbook. Same intent (don't fabricate when retrieval is empty)
    // but now visually consistent with the rest of the gallery — the prior
    // prose-only version stood out as "different" without earning the
    // distinction.
    id: "empty-context",
    title: "Empty-context discipline",
    description:
      "WRONG/RIGHT pairs for zero-result retrieval — honest, no fabrication.",
    marker: "## Empty-context discipline",
    body:
      "## Empty-context discipline\n\n" +
      "When retrieval returns zero hits, say so plainly — don't fall back to general knowledge.\n\n" +
      '- WRONG: "Click [Save] to commit the change."   (guessed, no retrieval hit)\n' +
      '- RIGHT: "I didn\'t find a documented procedure for this. Can you share the runbook or narrow the question?"\n' +
      '- WRONG: "The setting lives under [Admin] > [Permissions]."   (no source)\n' +
      '- RIGHT: "No matching documentation. Try the exact feature name, or share the spec."\n' +
      '- WRONG: "code 503 likely means the cache failed."   (general knowledge, not the docs)\n' +
      "- RIGHT: name what you searched, report zero hits, and ask for a narrower query or a source link.\n",
  },
  {
    // Guides CUGA on retrieval-time vocabulary handling. Operators with
    // domain jargon (acronyms, cross-language synonyms) get the biggest
    // recall lift from this template — it tells the LLM which terms are
    // equivalent so query expansion finds chunks indexed under either form.
    id: "vocabulary-hints",
    title: "Vocabulary hints",
    description:
      "Tell CUGA which terms to treat as equivalent when searching the knowledge base.",
    marker: "## Vocabulary hints",
    body:
      "## Vocabulary hints\n\n" +
      "When the question uses one of these terms, also consider the others as matches in retrieved content:\n\n" +
      "- PTO ↔ paid time off ↔ vacation ↔ leave\n" +
      "- 401(k) ↔ 401k ↔ retirement plan\n" +
      "- SLA ↔ service level agreement\n" +
      "- credentials ↔ creds ↔ secret ↔ token\n\n" +
      "Treat acronyms and spelled-out forms as equivalent. Cite whichever form actually appears in the retrieved chunk.\n",
  },
  {
    // Pairs with the contract-block citation rule in knowledge_instructions.md
    // — repeating it here under operator control means it survives even when
    // an operator opts to ship a tighter custom contract.
    id: "source-citation",
    title: "Source citation",
    description:
      "Cite the source filename for every procedural claim, code, or button name.",
    marker: "## Source citation",
    body:
      "## Source citation\n\n" +
      "After every procedural claim — steps, codes, paths, button names — append `(source: <filename>)` in parentheses.\n\n" +
      '- WRONG: "Click [Save] to commit."\n' +
      '- RIGHT: "Click [Save] to commit (source: handbook.pdf)."\n\n' +
      "If you can't point to a retrieved chunk for a claim, omit the claim entirely — don't pad with general knowledge.\n",
  },
];

// Structured server error matching ClientAdaptationError.to_dict() in config.py.
export interface AdaptationServerError {
  error: "length_exceeded" | "bidi_override" | "control_char" | "contract_override_phrase" | "type_error" | "null_byte";
  message: string;
  // Per-error extras
  phrase?: string;
  pattern?: string;
  codepoint?: string;
  length?: number;
  max?: number;
}

// Client-side validation result — only the deterministic subset of the server checks.
export type LocalValidation =
  | { kind: "ok" }
  | { kind: "length"; length: number; max: number }
  | { kind: "bidi" };

export function validateClientAdaptation(text: string): LocalValidation {
  if (text.length > CLIENT_ADAPTATION_MAX_CHARS) {
    return { kind: "length", length: text.length, max: CLIENT_ADAPTATION_MAX_CHARS };
  }
  if (BIDI_OVERRIDE_RE.test(text)) {
    return { kind: "bidi" };
  }
  return { kind: "ok" };
}

function stripInvisibleChars(text: string): string {
  // Remove only the bidi-override range; leave all other characters intact.
  return text.replace(/[\u202A-\u202E\u2066-\u2069]/g, "");
}

interface DriftState {
  driftFromPublished: boolean;
  publishedHashShort: string; // first 7 chars of SHA prefix
  draftHashShort: string;
}

// Mirrors the backend schema in src/cuga/backend/knowledge/config.py
// (_validate_glossary). Definition is optional; aliases is a flat list of
// synonyms / abbreviations / cross-language equivalents used both for prompt
// rendering AND for query expansion at search time.
export interface GlossaryEntry {
  term: string;
  aliases: string[];
  definition?: string;
}

// Mirrors backend constants for client-side validation only — server is
// authoritative.
export const GLOSSARY_MAX_ENTRIES = 50;
export const GLOSSARY_MAX_TERM_LEN = 100;
export const GLOSSARY_MAX_ALIAS_LEN = 100;
export const GLOSSARY_MAX_ALIASES_PER_ENTRY = 10;
export const GLOSSARY_MAX_DEFINITION_LEN = 200;

interface Props {
  value: string;
  onChange: (next: string) => void;
  glossary?: GlossaryEntry[];
  onGlossaryChange?: (next: GlossaryEntry[]) => void;
  // Single-shot atomic clear for BOTH ``value`` and ``glossary``. The
  // parent merges both deltas into one ``setState`` call so neither
  // overwrites the other (the two callbacks above close over the same
  // ``knowledgeConfig`` snapshot in the parent — calling them in
  // sequence makes the second clobber the first). If omitted, Reset
  // falls back to the dual-call path (best-effort; glossary will clear
  // but text may not).
  onReset?: () => void;
  saveState?: "idle" | "saving" | "saved" | "error";
  drift?: DriftState;
  serverError?: AdaptationServerError | null;
}

/**
 * AILabel slug used in the panel header + the Knowledge harness tab.
 * Carbon-for-AI primitive that marks this surface as AI-affecting. The
 * `AILabelContent` body explains the model effect so users see the rationale
 * on hover/click, not just an unexplained "AI" badge.
 */
function AdaptationAILabel({ size = "xs" }: { size?: "mini" | "2xs" | "xs" | "sm" | "md" | "lg" | "xl" }) {
  return (
    <AILabel autoAlign size={size} aiText="AI" textLabel="Knowledge harness">
      <AILabelContent>
        <h6 style={{ marginTop: 0 }}>Knowledge harness</h6>
        <p style={{ fontSize: "0.8125rem", margin: "0.5rem 0 0" }}>
          Text on this surface is appended to the knowledge-agent system
          prompt for every query. It steers how the LLM phrases answers,
          handles hedging, and preserves literal tokens. Glossary entries
          additionally expand the search query at retrieval time. Embeddings,
          chunking, and base ranking are unaffected.
        </p>
      </AILabelContent>
    </AILabel>
  );
}

export default function ClientAdaptationPanel({
  value,
  onChange,
  glossary,
  onGlossaryChange,
  onReset,
  saveState = "idle",
  // ``drift`` was a flashy "draft @ 8f3a2c1" tag — removed in the
  // simplification pass. Keeping the prop accepted (typed-noop) so the
  // parent's call site stays stable across designs.
  drift: _drift,
  serverError,
}: Props) {
  const [mode, setMode] = useState<0 | 1>(0); // 0 = Edit, 1 = Model preview
  const entries: GlossaryEntry[] = glossary ?? [];

  const localValidation = useMemo(() => validateClientAdaptation(value), [value]);
  const isEmpty = value.trim().length === 0 && entries.length === 0;

  // Single-click reset. Atomic via ``onReset`` when the parent provides
  // it (the only way to clear text + glossary without the stale-closure
  // overwrite that ``onChange("") + onGlossaryChange([])`` suffers from
  // — both callbacks close over the same parent ``knowledgeConfig``
  // snapshot, so calling them in sequence the second clobbers the
  // first). Sequenced fallback kept for older parents.
  const onResetClick = () => {
    if (isEmpty) return;
    if (onReset) {
      onReset();
      return;
    }
    onChange("");
    if (onGlossaryChange) onGlossaryChange([]);
  };

  // ---- Glossary mutation helpers (all immutable; parent owns the state) ----
  const updateEntry = (i: number, patch: Partial<GlossaryEntry>) => {
    if (!onGlossaryChange) return;
    const next = entries.map((e, idx) => (idx === i ? { ...e, ...patch } : e));
    onGlossaryChange(next);
  };
  const removeEntry = (i: number) => {
    if (!onGlossaryChange) return;
    onGlossaryChange(entries.filter((_, idx) => idx !== i));
  };
  const addEntry = () => {
    if (!onGlossaryChange) return;
    if (entries.length >= GLOSSARY_MAX_ENTRIES) return;
    onGlossaryChange([...entries, { term: "", aliases: [], definition: "" }]);
  };
  // Aliases use chip pills inside GlossaryRow (commit on Enter / comma,
  // remove on Backspace at empty input). Implementation lives in that
  // component rather than this parent so the input draft state is local
  // and doesn't trigger parent re-renders on every keystroke.

  const onTemplateClick = (templateBody: string) => {
    // If the editor already has content, append a separator; otherwise just set.
    // ``trimEnd()`` rather than ``replace(/\s+$/, "")`` — the regex form has
    // polynomial backtracking on adversarial inputs (CodeQL js/polynomial-redos).
    // String#trimEnd is O(n) and intent-revealing.
    onChange(isEmpty ? templateBody : value.trimEnd() + "\n\n" + templateBody);
  };

  // Friendly "saved" indicator that fades to muted after a beat — replaces
  // the loud blue/red Tag mess (Saving/Saved/Save failed). Linear-style:
  // "the system is fine" deserves whitespace, not a notification.
  const saveText =
    saveState === "saving"
      ? "Saving…"
      : saveState === "error"
        ? "Couldn't save"
        : saveState === "saved"
          ? "Saved"
          : "";
  const saveColor =
    saveState === "error" ? "var(--cds-support-error)" : "var(--cds-text-helper)";

  // Anything notable to surface? If nothing, render an inline-flow region
  // (no reserved empty slot — that's the enterprise-2015 antipattern).
  const hasNotification =
    localValidation.kind === "bidi" ||
    serverError?.error === "contract_override_phrase" ||
    serverError?.error === "control_char";

  return (
    <div
      role="region"
      aria-roledescription="AI behavior controls"
      aria-label="Knowledge harness editor"
      style={{ paddingTop: "1rem", maxWidth: "780px" }}
    >
      {/* ---- One calm header line: dot · title · AI slug · saved-text ---- */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "0.5rem",
          marginBottom: "1rem",
        }}
      >
        {/* Status dot — the entire Off/Active distinction in 8px */}
        <span
          aria-hidden
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: isEmpty
              ? "var(--cds-icon-disabled)"
              : "var(--cds-support-success)",
            display: "inline-block",
            flexShrink: 0,
          }}
          title={isEmpty ? "Off" : "Active"}
        />
        <h5 style={{ margin: 0, fontSize: "0.875rem", fontWeight: 600, lineHeight: 1.2 }}>
          Knowledge harness
        </h5>
        {/* The ONE AILabel on the whole surface — Carbon-for-AI identity. */}
        <AdaptationAILabel size="xs" />
        <span style={{ flex: 1 }} />
        {saveText && (
          <span
            aria-live="polite"
            style={{
              fontSize: "0.75rem",
              color: saveColor,
              transition: "color 0.3s ease",
            }}
          >
            {saveText}
          </span>
        )}
      </div>

      {/* ---- The hero: TextArea, generous breathing room ---- */}
      <TextArea
        id="knowledge-client-adaptation"
        labelText=""
        hideLabel
        aria-label="Knowledge harness markdown"
        placeholder={
          "# Domain rules\n\n" +
          "- Preserve [Save] verbatim\n" +
          "- Don't say 'contact support' when the docs describe a procedure"
        }
        value={value}
        rows={mode === 0 ? 10 : 0}
        maxCount={CLIENT_ADAPTATION_MAX_CHARS}
        enableCounter
        style={mode === 1 ? { display: "none" } : undefined}
        invalid={localValidation.kind !== "ok" || !!serverError}
        invalidText={
          localValidation.kind === "length"
            ? `Over by ${localValidation.length - localValidation.max} chars.`
            : localValidation.kind === "bidi"
              ? "Invisible direction-override character detected — strip it to save."
              : serverError?.message ?? ""
        }
        onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => onChange(e.target.value)}
      />

      {/* Rendered preview is a quiet inline panel, not a competing tab. */}
      {mode === 1 && (
        <Tile style={{ minHeight: "11rem", padding: "1rem", background: "var(--cds-layer-01)" }}>
          {isEmpty ? (
            <em style={{ color: "var(--cds-text-secondary)", fontSize: "0.8125rem" }}>(nothing yet)</em>
          ) : (
            <div className="ka-preview">
              <ReactMarkdown>{value}</ReactMarkdown>
            </div>
          )}
        </Tile>
      )}

      {/* Below-textarea utility row: ghost-link preview toggle on the left.
          Templates moved to their own gallery section below — they need
          more visual real estate than a comma-separated link list can give. */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "0.5rem",
          marginTop: "0.5rem",
          fontSize: "0.75rem",
          color: "var(--cds-text-helper)",
        }}
      >
        <Button
          kind="ghost"
          size="sm"
          onClick={() => setMode((m) => (m === 0 ? 1 : 0))}
          style={{ paddingInline: 0 }}
        >
          {mode === 0 ? "Preview rendered ›" : "‹ Back to edit"}
        </Button>
      </div>

      {/* ---- Inline notifications — render only when present ---- */}
      {hasNotification && (
        <div aria-live="polite" aria-atomic="true" style={{ marginTop: "0.75rem" }}>
          {localValidation.kind === "bidi" && (
            <ActionableNotification
              kind="warning"
              lowContrast
              hideCloseButton
              title="Invisible direction-override character"
              subtitle="A known prompt-smuggling vector. Strip to save."
              actionButtonLabel="Strip"
              onActionButtonClick={() => onChange(stripInvisibleChars(value))}
            />
          )}
          {serverError?.error === "contract_override_phrase" && (
            <ActionableNotification
              kind="error"
              lowContrast
              hideCloseButton
              title="Rejected by AI safety contract"
              subtitle={`Remove ${JSON.stringify(serverError.phrase)} to save.`}
              actionButtonLabel="Find"
              onActionButtonClick={() => {
                const el = document.getElementById("knowledge-client-adaptation") as HTMLTextAreaElement | null;
                const phrase = serverError.phrase;
                if (!el || !phrase) return;
                const idx = value.toLowerCase().indexOf(phrase.toLowerCase());
                if (idx >= 0) {
                  el.focus();
                  el.setSelectionRange(idx, idx + phrase.length);
                }
              }}
            />
          )}
          {serverError?.error === "control_char" && (
            <InlineNotification
              kind="error"
              lowContrast
              hideCloseButton
              title="Control character not allowed"
              subtitle={`Codepoint ${serverError.codepoint}.`}
            />
          )}
        </div>
      )}

      {/* ============================================================== */}
      {/* EXAMPLES GALLERY — persistent 2-col card grid + Reset bar       */}
      {/* -------------------------------------------------------------- */}
      {/* Always visible (replaces the prior "vanishes after first       */}
      {/* insert" Try-link bar). Inserting a template APPENDS to the     */}
      {/* current text, so the operator can pile several on top of each  */}
      {/* other; the ✓ Added pill is a passive hint, not a lock.         */}
      {/* ============================================================== */}
      <div style={{ marginTop: "1.75rem" }}>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: "0.5rem",
            marginBottom: "0.5rem",
          }}
        >
          <h6
            style={{
              margin: 0,
              fontSize: "0.8125rem",
              fontWeight: 600,
              color: "var(--cds-text-primary)",
            }}
          >
            Examples
          </h6>
          <span style={{ fontSize: "0.75rem", color: "var(--cds-text-helper)" }}>
            Click to append. You can edit afterwards.
          </span>
          <span style={{ flex: 1 }} />
          <Button
            kind="ghost"
            size="sm"
            renderIcon={Reset}
            disabled={isEmpty}
            onClick={onResetClick}
            title={isEmpty ? "Nothing to reset" : "Clear harness rules and glossary"}
            style={{ paddingInline: "0.5rem" }}
          >
            Reset
          </Button>
        </div>
        <div
          role="list"
          aria-label="Knowledge harness examples"
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
            gap: "0.5rem",
          }}
        >
          {TEMPLATES.map((t) => {
            const added = value.includes(t.marker);
            return (
              <button
                key={t.id}
                type="button"
                role="listitem"
                onClick={() => onTemplateClick(t.body)}
                title={`Append to the editor${added ? " (already in your rules)" : ""}`}
                style={{
                  textAlign: "left",
                  background: "var(--cds-layer-01)",
                  border: "1px solid var(--cds-border-subtle-01)",
                  borderRadius: 2,
                  padding: "0.625rem 0.75rem",
                  cursor: "pointer",
                  transition: "background 0.12s ease, border-color 0.12s ease",
                  font: "inherit",
                  color: "var(--cds-text-primary)",
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.background =
                    "var(--cds-layer-hover-01)";
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLButtonElement).style.background =
                    "var(--cds-layer-01)";
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "0.375rem",
                    marginBottom: "0.125rem",
                  }}
                >
                  <span style={{ fontSize: "0.8125rem", fontWeight: 600 }}>
                    {t.title}
                  </span>
                  {added && (
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 2,
                        fontSize: "0.6875rem",
                        color: "var(--cds-support-success)",
                      }}
                      title="Already inserted (click to insert again)"
                    >
                      <Checkmark size={12} />
                      Added
                    </span>
                  )}
                </div>
                <div
                  style={{
                    fontSize: "0.75rem",
                    color: "var(--cds-text-helper)",
                    lineHeight: 1.35,
                  }}
                >
                  {t.description}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* ============================================================== */}
      {/* GLOSSARY — borderless, chip-pill aliases                        */}
      {/* ============================================================== */}
      {onGlossaryChange && (
        <div style={{ marginTop: "2.25rem" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: "0.5rem", marginBottom: "0.5rem" }}>
            <h6 style={{ margin: 0, fontSize: "0.8125rem", fontWeight: 600, color: "var(--cds-text-primary)" }}>
              Glossary
            </h6>
            <span style={{ fontSize: "0.75rem", color: "var(--cds-text-helper)" }}>
              Synonyms appended to the retrieval query
            </span>
            <span style={{ flex: 1 }} />
            {entries.length > 0 && (
              <span style={{ fontSize: "0.75rem", color: "var(--cds-text-helper)" }}>
                {entries.length} / {GLOSSARY_MAX_ENTRIES}
              </span>
            )}
          </div>

          {entries.length === 0 ? (
            <Button kind="ghost" size="sm" renderIcon={Add} onClick={addEntry} style={{ paddingInline: 0 }}>
              Add term
            </Button>
          ) : (
            <div role="table" aria-label="Domain glossary entries">
              {entries.map((e, i) => (
                <GlossaryRow
                  key={`gloss-${i}`}
                  index={i}
                  entry={e}
                  showHeader={i === 0}
                  onTermChange={(v) => updateEntry(i, { term: v })}
                  onAliasesChange={(v) => updateEntry(i, { aliases: v })}
                  onDefinitionChange={(v) => updateEntry(i, { definition: v })}
                  onRemove={() => removeEntry(i)}
                />
              ))}
              <Button
                kind="ghost"
                size="sm"
                renderIcon={Add}
                onClick={addEntry}
                disabled={entries.length >= GLOSSARY_MAX_ENTRIES}
                style={{ paddingInline: 0, marginTop: "0.5rem" }}
              >
                Add term
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// GlossaryRow — borderless table-style row with chip-pill aliases.
// ---------------------------------------------------------------------------
// Linear/Notion vibe: per-row Tile borders are heavy; a borderless row with
// a thin bottom divider reads as a table, not a stack of cards. Aliases
// become chip pills with Enter/Comma to commit, Backspace at empty-input
// to remove the last chip.
// ===========================================================================

interface GlossaryRowProps {
  index: number;
  entry: GlossaryEntry;
  showHeader: boolean;
  onTermChange: (v: string) => void;
  onAliasesChange: (v: string[]) => void;
  onDefinitionChange: (v: string) => void;
  onRemove: () => void;
}

function GlossaryRow({
  index,
  entry,
  showHeader,
  onTermChange,
  onAliasesChange,
  onDefinitionChange,
  onRemove,
}: GlossaryRowProps) {
  const [aliasDraft, setAliasDraft] = useState("");
  const aliasInputRef = useRef<HTMLInputElement | null>(null);

  const commitChip = () => {
    const v = aliasDraft.trim();
    if (!v) return;
    if (entry.aliases.length >= GLOSSARY_MAX_ALIASES_PER_ENTRY) return;
    if (entry.aliases.some((a) => a.toLowerCase() === v.toLowerCase())) {
      setAliasDraft("");
      return;
    }
    onAliasesChange([...entry.aliases, v]);
    setAliasDraft("");
  };

  const removeChip = (i: number) => onAliasesChange(entry.aliases.filter((_, idx) => idx !== i));

  return (
    <div
      role="row"
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(120px, 1fr) minmax(220px, 2fr) minmax(180px, 2fr) 32px",
        columnGap: "0.75rem",
        alignItems: "center",
        padding: "0.5rem 0",
        borderBottom: "1px solid var(--cds-border-subtle-01)",
      }}
    >
      {/* Inline column headers shown only on first row */}
      {showHeader && (
        <>
          <ColumnHeader>Term</ColumnHeader>
          <ColumnHeader>Aliases</ColumnHeader>
          <ColumnHeader>Definition</ColumnHeader>
          <span />
        </>
      )}
      <TextInput
        id={`gloss-term-${index}`}
        labelText=""
        hideLabel
        size="sm"
        placeholder="K3"
        value={entry.term}
        maxLength={GLOSSARY_MAX_TERM_LEN}
        onChange={(ev: React.ChangeEvent<HTMLInputElement>) => onTermChange(ev.target.value)}
      />
      <div
        onClick={() => aliasInputRef.current?.focus()}
        style={{
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "0.25rem",
          minHeight: "2rem",
          padding: "0.25rem 0.5rem",
          background: "var(--cds-field-01)",
          borderBottom: "1px solid var(--cds-border-strong-01)",
          cursor: "text",
        }}
      >
        {entry.aliases.map((a, i) => (
          <Tag
            key={`a-${i}`}
            type="cool-gray"
            size="sm"
            filter
            onClose={() => removeChip(i)}
            title={`Remove ${a}`}
          >
            {a}
          </Tag>
        ))}
        <input
          ref={aliasInputRef}
          aria-label={`Add alias for ${entry.term || "this term"}`}
          value={aliasDraft}
          maxLength={GLOSSARY_MAX_ALIAS_LEN}
          onChange={(e) => setAliasDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              commitChip();
            } else if (e.key === "Backspace" && !aliasDraft && entry.aliases.length > 0) {
              e.preventDefault();
              removeChip(entry.aliases.length - 1);
            }
          }}
          onBlur={commitChip}
          placeholder={entry.aliases.length === 0 ? "K-3, severance code 3 — Enter to add" : ""}
          style={{
            border: "none",
            outline: "none",
            background: "transparent",
            flex: 1,
            minWidth: "8rem",
            fontSize: "0.875rem",
            color: "var(--cds-text-primary)",
          }}
        />
      </div>
      <TextInput
        id={`gloss-def-${index}`}
        labelText=""
        hideLabel
        size="sm"
        placeholder="(optional)"
        value={entry.definition ?? ""}
        maxLength={GLOSSARY_MAX_DEFINITION_LEN}
        onChange={(ev: React.ChangeEvent<HTMLInputElement>) => onDefinitionChange(ev.target.value)}
      />
      <IconButton label="Remove term" kind="ghost" size="sm" onClick={onRemove} align="left">
        <TrashCan />
      </IconButton>
    </div>
  );
}

function ColumnHeader({ children }: { children: React.ReactNode }) {
  return (
    <span
      role="columnheader"
      style={{
        fontSize: "0.6875rem",
        color: "var(--cds-text-helper)",
        textTransform: "uppercase",
        letterSpacing: "0.04em",
        gridRow: 1,
      }}
    >
      {children}
    </span>
  );
}
