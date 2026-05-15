/*
 *  Copyright IBM Corp. 2026
 *
 *  Slash-command autocomplete dropdown that overlays the Carbon AI Chat
 *  composer. Acceptance criteria are documented in issue #18.
 *
 *  The Carbon Chat input is rendered inside a shadow DOM, so this component
 *  reaches into the chat element's shadow tree to locate the textarea,
 *  attaches input/keydown listeners, and renders a positioned overlay via a
 *  React portal in the light DOM. The dropdown only opens when '/' is the
 *  first non-whitespace character of the input.
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
import { findComposerTextarea, setComposerTextareaValue } from "./composerTextarea";

/** Returns true when the slash is the first non-whitespace character. */
function isSlashLeadingInput(value: string): boolean {
  const trimmed = value.replace(/^\s+/, "");
  return trimmed.startsWith("/");
}

/** Extracts the query portion after the leading slash (no whitespace before /). */
function extractSlashQuery(value: string): string | null {
  const match = /^\s*\/(\S*)/.exec(value);
  if (!match) return null;
  return match[1] ?? "";
}

interface DropdownPosition {
  left: number;
  top: number;
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
const FETCH_DEBOUNCE_MS = 150;

export const SlashCommandDropdown: React.FC<SlashCommandDropdownProps> = ({
  chatElement,
  portalContainer,
}) => {
  const [textarea, setTextarea] = useState<HTMLTextAreaElement | null>(null);
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
  const optionsListId = "cuga-slash-options";

  useEffect(() => {
    openRef.current = open;
  }, [open]);

  // Discover the textarea once the chat element mounts; retry with a
  // MutationObserver until it appears.
  useEffect(() => {
    if (!chatElement) return;
    let cancelled = false;

    const tryFind = () => {
      if (cancelled) return;
      const found = findComposerTextarea(chatElement);
      if (found) {
        setTextarea(found);
      }
    };

    tryFind();

    const observer = new MutationObserver(() => {
      if (cancelled) return;
      if (!textarea || !document.contains(textarea)) {
        tryFind();
      }
    });
    observer.observe(chatElement, { childList: true, subtree: true });
    const interval = window.setInterval(tryFind, 500);

    return () => {
      cancelled = true;
      observer.disconnect();
      window.clearInterval(interval);
    };
    // Re-run if textarea is unmounted; we recapture below by checking document.contains.
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

  // Filter commands case-insensitively by name prefix.
  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    if (!q) return commands;
    return commands.filter((c) => c.name.toLowerCase().startsWith(q));
  }, [commands, query]);

  // Clamp highlight index whenever the filtered list shrinks.
  useEffect(() => {
    if (highlightIndex >= filtered.length) {
      setHighlightIndex(filtered.length > 0 ? filtered.length - 1 : 0);
    }
  }, [filtered.length, highlightIndex]);

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

  // Replace the textarea value programmatically and fire an input event so
  // the underlying Carbon framework picks the change up. The traversal +
  // value-setting mechanism lives in ./composerTextarea so the unknown-command
  // suggestion chips (slice #23) can reuse it.
  const setTextareaValue = useCallback(
    (value: string) => {
      setComposerTextareaValue(textarea, value);
    },
    [textarea],
  );

  const acceptCommand = useCallback(
    (command: SlashCommandInfo) => {
      setTextareaValue(`/${command.name} `);
      closeDropdown();
    },
    [closeDropdown, setTextareaValue],
  );

  // Textarea input listener: decides whether to open the dropdown.
  useEffect(() => {
    if (!textarea) return;

    const scheduleFetch = () => {
      if (fetchTimerRef.current !== null) {
        window.clearTimeout(fetchTimerRef.current);
      }
      fetchTimerRef.current = window.setTimeout(() => {
        fetchTimerRef.current = null;
        void fetchCommands();
      }, FETCH_DEBOUNCE_MS);
    };

    const handleInput = () => {
      const value = textarea.value;
      if (!isSlashLeadingInput(value)) {
        if (openRef.current) closeDropdown();
        return;
      }
      const q = extractSlashQuery(value) ?? "";
      const wasOpen = openRef.current;
      setQuery(q);
      setHighlightIndex(0);
      if (!wasOpen) {
        setOpen(true);
        scheduleFetch();
      }
    };

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
      } else if (event.key === "Enter") {
        const list = filteredRef.current;
        if (list.length === 0) return;
        const idx = Math.min(highlightIndexRef.current, list.length - 1);
        event.preventDefault();
        event.stopPropagation();
        acceptCommand(list[idx]);
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
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

    // If the textarea already starts with '/', open immediately on mount.
    handleInput();

    return () => {
      textarea.removeEventListener("input", handleInput);
      textarea.removeEventListener("keydown", handleKeyDown, true);
      textarea.removeEventListener("blur", handleBlur);
      if (fetchTimerRef.current !== null) {
        window.clearTimeout(fetchTimerRef.current);
        fetchTimerRef.current = null;
      }
    };
  }, [acceptCommand, closeDropdown, fetchCommands, textarea]);

  // Keep refs of mutable state for the keydown listener (which is attached
  // once but needs current values).
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
      const path = typeof event.composedPath === "function" ? event.composedPath() : [];
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

  // Position the dropdown ABOVE the textarea, anchored to its top-left.
  // Use fixed positioning so we don't get clipped by overflow:hidden ancestors.
  // We render bottom-up: dropdown bottom == textarea.top - gap.
  const dropdownStyle: React.CSSProperties = {
    position: "fixed",
    left: position.left,
    bottom: window.innerHeight - position.top + DROPDOWN_GAP,
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
      aria-activedescendant={
        filtered[highlightIndex]
          ? `cuga-slash-option-${filtered[highlightIndex].name}`
          : undefined
      }
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
                    {command.kind === "skill" ? "skill" : "built-in"}
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
