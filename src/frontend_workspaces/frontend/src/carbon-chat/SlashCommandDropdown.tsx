/*
 *  Copyright IBM Corp. 2026
 *
 *  Slash-command autocomplete dropdown for the Carbon AI Chat composer.
 *
 *  The Carbon Chat input is rendered inside a shadow DOM, so this component
 *  reaches into the chat element's shadow tree to locate the composer,
 *  attaches input/keydown listeners, and renders a positioned overlay via a
 *  React portal in the light DOM.
 *
 *  Slash-command semantics are SOFT — a ``/skill`` mention anywhere in a
 *  message is a suggestion to the agent, not a forced command — so
 *  autocomplete is caret-scoped rather than message-scoped: the dropdown
 *  engages whenever the caret sits inside a token that starts with ``/`` at
 *  position 0 or right after whitespace (``hi can I use /ec`` reopens it
 *  filtered to ``ec``), and accepting a suggestion replaces just that token
 *  (inserting the name + trailing space) at its position — not the whole
 *  composer content. Once a token is completed and a space typed, the caret
 *  leaves the token and the dropdown closes until a new ``/``-token starts.
 */
import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { getCommands, type SlashCommandInfo } from "../api";
import {
  clearComposerAriaAttributes,
  findComposerTextarea,
  getComposerCaretOffset,
  getComposerInputValue,
  isComposerStale,
  replaceComposerRange,
  setComposerAriaAttributes,
  setComposerTextareaValue,
} from "./composerTextarea";

/** The slash token under the caret, in composer-value character offsets. */
export interface SlashToken {
  /** Offset of the leading ``/``. */
  start: number;
  /** Offset just past the last non-whitespace character of the token. */
  end: number;
  /** Full token text after the ``/`` (may extend past the caret). */
  name: string;
  /** Text between the ``/`` and the caret — the autocomplete filter. */
  queryToCaret: string;
  /** The caret offset the token was derived from. */
  caret: number;
}

/**
 * Finds the slash token the caret is inside of, if any. A slash token is a
 * maximal non-whitespace run whose first character is ``/`` and which starts
 * at position 0 or right after whitespace. The caret counts as "inside" from
 * the leading ``/`` through the position just past the token's last
 * character. Deliberately stateless: derived from (value, caret) on every
 * event, so moving the caret out of the token closes the dropdown and moving
 * it back in reopens it.
 */
export function findSlashTokenAtCaret(
  value: string,
  caret: number,
): SlashToken | null {
  const c = Math.max(0, Math.min(caret, value.length));
  let start = c;
  while (start > 0 && !/\s/.test(value[start - 1])) start -= 1;
  if (value[start] !== "/") return null;
  let end = c;
  while (end < value.length && !/\s/.test(value[end])) end += 1;
  return {
    start,
    end,
    name: value.slice(start + 1, end),
    queryToCaret: value.slice(start + 1, Math.max(c, start + 1)),
    caret: c,
  };
}

interface DropdownPosition {
  left: number;
  top: number;
  /** Bottom edge of the composer — anchor when the dropdown flips below. */
  bottom: number;
  width: number;
}

interface SlashCommandDropdownProps {
  /** Element whose shadow tree houses the composer textarea. */
  chatElement: HTMLElement | null;
  /** Light-DOM container the dropdown portal renders into. */
  portalContainer: HTMLElement | null;
}

const MAX_DROPDOWN_WIDTH = 520;
const MIN_DROPDOWN_WIDTH = 280;
const DROPDOWN_GAP = 8;
// Mirrors the .cuga-slash-dropdown max-height in CarbonChat.css; used to
// decide whether the dropdown fits above the composer.
const MAX_DROPDOWN_HEIGHT = 320;
const FETCH_DEBOUNCE_MS = 150;

export const SlashCommandDropdown: React.FC<SlashCommandDropdownProps> = ({
  chatElement,
  portalContainer,
}) => {
  const [textarea, setTextarea] = useState<HTMLElement | null>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [commands, setCommands] = useState<SlashCommandInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [highlightIndex, setHighlightIndex] = useState(0);
  const [position, setPosition] = useState<DropdownPosition | null>(null);
  const dropdownRef = useRef<HTMLDivElement | null>(null);
  const fetchTimerRef = useRef<number | null>(null);
  const openRef = useRef(open);
  // Escape is an explicit dismissal: remember (value, token start) so a
  // stray selectionchange can't immediately reopen the dropdown over the
  // same token. Any edit or caret departure invalidates the dismissal.
  const dismissedRef = useRef<{ value: string; tokenStart: number } | null>(null);
  const optionsListId = "cuga-slash-options";

  useEffect(() => {
    openRef.current = open;
  }, [open]);

  useEffect(() => {
    let cancelled = false;
    // MutationObserver does not cross the shadow boundary, so observe the shadowRoot when present.
    const observeTarget: Node =
      chatElement?.shadowRoot ?? chatElement ?? document.body;

    const tryFind = () => {
      if (cancelled) return;
      const found = findComposerTextarea(chatElement);
      if (found && found !== textarea) setTextarea(found);
    };

    tryFind();

    const observer = new MutationObserver(() => {
      if (cancelled) return;
      // Carbon Chat sometimes replaces the composer node entirely after a
      // submit while leaving the old (now zero-rect) one attached. We need
      // to re-resolve whenever the current reference is stale by *either*
      // criterion (detached OR no longer laid out).
      if (isComposerStale(textarea)) {
        tryFind();
      }
    });
    observer.observe(observeTarget, { childList: true, subtree: true });
    // Interval is a bootstrap fallback for composer-mounts-before-observer; clear it once the textarea is live.
    // Cap with a wall-clock budget so embedded / shadow-rooted / content-
    // blocked hosts where the textarea never resolves don't poll forever.
    let attempts = 0;
    const MAX_ATTEMPTS = 30; // ~15s at 500ms
    let interval: number | null = window.setInterval(() => {
      if (cancelled) return;
      attempts += 1;
      tryFind();
      if (textarea && document.contains(textarea) && interval !== null) {
        window.clearInterval(interval);
        interval = null;
      } else if (attempts >= MAX_ATTEMPTS && interval !== null) {
        console.warn("[cuga] composer textarea never resolved; giving up");
        window.clearInterval(interval);
        interval = null;
      }
    }, 500);

    return () => {
      cancelled = true;
      observer.disconnect();
      if (interval !== null) window.clearInterval(interval);
    };
  }, [chatElement, textarea]);

  const closeDropdown = useCallback(() => {
    setOpen(false);
    setQuery("");
    setHighlightIndex(0);
    setError(null);
  }, []);

  const fetchCommands = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getCommands();
      setCommands(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load commands");
      setCommands([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const scheduleFetch = useCallback(() => {
    if (fetchTimerRef.current !== null) {
      window.clearTimeout(fetchTimerRef.current);
    }
    fetchTimerRef.current = window.setTimeout(() => {
      fetchTimerRef.current = null;
      void fetchCommands();
    }, FETCH_DEBOUNCE_MS);
  }, [fetchCommands]);

  useEffect(() => {
    return () => {
      if (fetchTimerRef.current !== null) {
        window.clearTimeout(fetchTimerRef.current);
        fetchTimerRef.current = null;
      }
    };
  }, []);

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    if (!q) return commands;
    return commands.filter((c) => c.name.toLowerCase().startsWith(q));
  }, [commands, query]);

  useEffect(() => {
    setHighlightIndex(0);
  }, [query]);

  useEffect(() => {
    if (highlightIndex >= filtered.length) {
      setHighlightIndex(filtered.length > 0 ? filtered.length - 1 : 0);
    }
  }, [filtered.length, highlightIndex]);

  // Per-node original-role capture (composer ships role=textbox; we transiently overwrite with role=combobox).
  const originalRoleRef = useRef<{ node: HTMLElement | null; role: string | null }>(
    { node: null, role: null },
  );

  // Wire WAI-ARIA combobox attrs onto the textbox (see composerTextarea.setComposerAriaAttributes for the why).
  useEffect(() => {
    if (!textarea) return;

    if (originalRoleRef.current.node !== textarea) {
      originalRoleRef.current = {
        node: textarea,
        role: textarea.getAttribute("role"),
      };
    }

    if (open) {
      const activeOption = filtered[highlightIndex];
      setComposerAriaAttributes(textarea, {
        role: "combobox",
        controls: optionsListId,
        expanded: true,
        activedescendant: activeOption
          ? `cuga-slash-option-${activeOption.name}`
          : null,
      });
    } else {
      // Per APG, keep ``aria-controls`` set even when collapsed — the listbox id stays meaningful to AT users.
      const originalRole = originalRoleRef.current.role;
      if (originalRole !== null) {
        textarea.setAttribute("role", originalRole);
      } else {
        textarea.removeAttribute("role");
      }
      setComposerAriaAttributes(textarea, {
        controls: optionsListId,
        expanded: false,
        activedescendant: null,
      });
    }

    return () => {
      const captured = originalRoleRef.current;
      clearComposerAriaAttributes(textarea);
      if (captured.node === textarea && captured.role !== null) {
        textarea.setAttribute("role", captured.role);
      }
    };
  }, [textarea, open, highlightIndex, filtered, optionsListId]);

  // Update dropdown position relative to the textarea (fixed positioning).
  const updatePosition = useCallback(() => {
    if (!textarea) {
      setPosition(null);
      return;
    }
    const rect = textarea.getBoundingClientRect();
    const width = Math.min(
      MAX_DROPDOWN_WIDTH,
      Math.max(MIN_DROPDOWN_WIDTH, rect.width),
    );
    setPosition({
      left: rect.left,
      top: rect.top,
      bottom: rect.bottom,
      width,
    });
  }, [textarea]);

  // Keep position in sync while open.
  useLayoutEffect(() => {
    if (!open) return;
    updatePosition();
    const handle = () => updatePosition();
    window.addEventListener("resize", handle);
    window.addEventListener("scroll", handle, true);
    return () => {
      window.removeEventListener("resize", handle);
      window.removeEventListener("scroll", handle, true);
    };
  }, [open, updatePosition]);

  const acceptCommand = useCallback(
    (command: SlashCommandInfo) => {
      const value = getComposerInputValue(textarea);
      const caret = getComposerCaretOffset(textarea) ?? value.length;
      const token = findSlashTokenAtCaret(value, caret);
      if (token) {
        // Replace just the caret's token. The insertion carries a trailing
        // space (the caret lands after it, outside any token, so the
        // dropdown closes); when the token is already followed by a plain
        // space, consume it so mid-text acceptance doesn't double up
        // whitespace.
        const end = value[token.end] === " " ? token.end + 1 : token.end;
        replaceComposerRange(textarea, token.start, end, `/${command.name} `);
      } else {
        // No resolvable token (e.g. click-accept after focus was lost and
        // the selection with it): fall back to the whole-composer write.
        setComposerTextareaValue(textarea, `/${command.name} `);
      }
      closeDropdown();
    },
    [closeDropdown, textarea],
  );

  /**
   * Re-derive the dropdown state from the live (value, caret) pair. Called
   * on every composer input event (``assumeEndWhenUnresolved`` — typing
   * happens at the caret, so an unresolvable selection can safely default
   * to end-of-text) and on document selectionchange (no fallback: an
   * unresolvable selection there just means the caret isn't in the
   * composer, so we must not guess).
   */
  const evaluateComposer = useCallback(
    (assumeEndWhenUnresolved: boolean) => {
      if (!textarea) return;
      const value = getComposerInputValue(textarea);
      let caret = getComposerCaretOffset(textarea);
      if (caret === null) {
        if (!assumeEndWhenUnresolved) return;
        caret = value.length;
      }
      const token = findSlashTokenAtCaret(value, caret);
      if (!token) {
        dismissedRef.current = null;
        if (openRef.current) closeDropdown();
        return;
      }
      const dismissed = dismissedRef.current;
      if (
        dismissed &&
        dismissed.value === value &&
        dismissed.tokenStart === token.start
      ) {
        // Explicitly dismissed via Escape and nothing has changed since —
        // stay closed until the user edits or moves the caret elsewhere.
        return;
      }
      dismissedRef.current = null;
      setQuery(token.queryToCaret);
      if (!openRef.current) {
        setOpen(true);
        scheduleFetch();
      }
    },
    [closeDropdown, scheduleFetch, textarea],
  );

  // Textarea input/keydown listeners: drive the dropdown from the caret.
  useEffect(() => {
    if (!textarea) return;

    const handleInput = () => evaluateComposer(true);

    const handleKeyDown = (event: KeyboardEvent) => {
      if (!openRef.current) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        event.stopPropagation();
        setHighlightIndex((idx) => {
          const len = filteredRef.current.length;
          if (len === 0) return 0;
          return (idx + 1) % len;
        });
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        event.stopPropagation();
        setHighlightIndex((idx) => {
          const len = filteredRef.current.length;
          if (len === 0) return 0;
          return (idx - 1 + len) % len;
        });
      } else if (event.key === "Enter" || event.key === "Tab") {
        // The dropdown is only open while the caret sits inside a
        // ``/``-token, so Enter/Tab there always mean "accept the
        // highlighted suggestion" — message submission happens once the
        // caret is outside any slash token (dropdown closed) and Enter
        // falls through to Carbon. An empty option list also falls
        // through: there is nothing to accept.
        const list = filteredRef.current;
        if (list.length === 0) return;
        const idx = Math.min(highlightIndexRef.current, list.length - 1);
        event.preventDefault();
        event.stopPropagation();
        acceptCommand(list[idx]);
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        const value = getComposerInputValue(textarea);
        const caret = getComposerCaretOffset(textarea) ?? value.length;
        const token = findSlashTokenAtCaret(value, caret);
        dismissedRef.current = token
          ? { value, tokenStart: token.start }
          : null;
        closeDropdown();
      }
    };

    const handleBlur = () => {
      // Close on a short delay so click handlers inside the dropdown still fire.
      window.setTimeout(() => {
        if (
          dropdownRef.current &&
          dropdownRef.current.contains(document.activeElement)
        ) {
          return;
        }
        closeDropdown();
      }, 120);
    };

    textarea.addEventListener("input", handleInput);
    textarea.addEventListener("keydown", handleKeyDown, true);
    textarea.addEventListener("blur", handleBlur);

    // If the caret already sits in a slash token on mount, open immediately.
    handleInput();

    return () => {
      textarea.removeEventListener("input", handleInput);
      textarea.removeEventListener("keydown", handleKeyDown, true);
      textarea.removeEventListener("blur", handleBlur);
    };
  }, [acceptCommand, closeDropdown, evaluateComposer, textarea]);

  // Caret moves without input events (arrow keys, clicks) must also engage /
  // disengage the dropdown — that's what makes mid-text ``/``-tokens live
  // targets. selectionchange only fires on document, so throttle with rAF.
  useEffect(() => {
    if (!textarea) return;
    let raf = 0;
    const handleSelectionChange = () => {
      if (raf) return;
      raf = window.requestAnimationFrame(() => {
        raf = 0;
        evaluateComposer(false);
      });
    };
    document.addEventListener("selectionchange", handleSelectionChange);
    return () => {
      document.removeEventListener("selectionchange", handleSelectionChange);
      if (raf) window.cancelAnimationFrame(raf);
    };
  }, [evaluateComposer, textarea]);

  const filteredRef = useRef(filtered);
  const highlightIndexRef = useRef(highlightIndex);
  useEffect(() => {
    filteredRef.current = filtered;
  }, [filtered]);
  useEffect(() => {
    highlightIndexRef.current = highlightIndex;
  }, [highlightIndex]);

  // Click-outside dismissal.
  useEffect(() => {
    if (!open) return;
    const handleDocClick = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (dropdownRef.current && dropdownRef.current.contains(target)) return;
      // Composed path covers shadow DOM clicks (e.g. clicks on the textarea).
      const path = event.composedPath();
      if (textarea && path.includes(textarea)) return;
      closeDropdown();
    };
    document.addEventListener("mousedown", handleDocClick, true);
    return () => document.removeEventListener("mousedown", handleDocClick, true);
  }, [closeDropdown, open, textarea]);

  // Don't render anything when closed or when we don't know where to anchor.
  if (!open || !portalContainer || !position) {
    return null;
  }

  // position: fixed so overflow:hidden ancestors don't clip; anchored above
  // the textarea's top, unless the viewport is too short to fit the dropdown
  // there (e.g. embedded/short viewports), in which case it flips below.
  const dropdownHeight = dropdownRef.current?.offsetHeight ?? MAX_DROPDOWN_HEIGHT;
  const fitsAbove = position.top - DROPDOWN_GAP - dropdownHeight >= 0;
  const dropdownStyle: React.CSSProperties = {
    position: "fixed",
    left: position.left,
    ...(fitsAbove
      ? { bottom: window.innerHeight - position.top + DROPDOWN_GAP }
      : { top: position.bottom + DROPDOWN_GAP }),
    width: position.width,
    zIndex: 9999,
  };

  return createPortal(
    <div
      ref={dropdownRef}
      className="cuga-slash-dropdown"
      role="listbox"
      id={optionsListId}
      aria-label="Slash commands"
      style={dropdownStyle}
      onMouseDown={(e) => {
        // Prevent the textarea blur from firing before our click handler.
        e.preventDefault();
      }}
    >
      {loading && (
        <div className="cuga-slash-dropdown__status">Loading commands…</div>
      )}
      {!loading && error && (
        <div className="cuga-slash-dropdown__status cuga-slash-dropdown__status--error">
          {error}
        </div>
      )}
      {!loading && !error && filtered.length === 0 && (
        <div className="cuga-slash-dropdown__status">No matching commands</div>
      )}
      {!loading && !error && filtered.length > 0 && (
        <ul className="cuga-slash-dropdown__list" role="presentation">
          {filtered.map((command, index) => {
            const isActive = index === highlightIndex;
            return (
              <li
                key={command.name}
                id={`cuga-slash-option-${command.name}`}
                role="option"
                aria-selected={isActive}
                className={`cuga-slash-option${isActive ? " cuga-slash-option--active" : ""}`}
                onMouseEnter={() => setHighlightIndex(index)}
                onClick={() => acceptCommand(command)}
              >
                <div className="cuga-slash-option__main">
                  <span className="cuga-slash-option__name">/{command.name}</span>
                  {command.argument_hint && (
                    <span className="cuga-slash-option__hint">
                      {command.argument_hint}
                    </span>
                  )}
                  <span
                    className={`cuga-slash-option__kind cuga-slash-option__kind--${command.kind}`}
                  >
                    skill
                  </span>
                </div>
                {command.description && (
                  <div className="cuga-slash-option__description">
                    {command.description}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>,
    portalContainer,
  );
};

export default SlashCommandDropdown;
