/*
 *  Copyright IBM Corp. 2026
 *
 *  Slash-command autocomplete dropdown that overlays the Carbon AI Chat
 *  composer.
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
import {
  clearComposerAriaAttributes,
  findComposerTextarea,
  getComposerInputValue,
  isComposerStale,
  setComposerAriaAttributes,
  setComposerTextareaValue,
} from "./composerTextarea";

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
    let interval: number | null = window.setInterval(() => {
      if (cancelled) return;
      tryFind();
      if (textarea && document.contains(textarea) && interval !== null) {
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

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    if (!q) return commands;
    return commands.filter((c) => c.name.toLowerCase().startsWith(q));
  }, [commands, query]);

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
      const value = getComposerInputValue(textarea);
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
        // If the user already typed arguments after the command name
        // (``/echo hello world``), Enter must submit the message — not
        // overwrite the composer with ``/<name> `` and drop the args. We
        // detect "already has args" by looking for whitespace after the
        // leading ``/word`` in the live composer value.
        const liveValue = getComposerInputValue(textarea);
        const trimmed = liveValue.replace(/^\s+/, "");
        const hasArgs = /^\/\S+\s/.test(trimmed);
        if (hasArgs) {
          // Close the dropdown but let Enter bubble to Carbon's submit path.
          closeDropdown();
          return;
        }
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

  // position: fixed so overflow:hidden ancestors don't clip; anchored to the textarea's top.
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
