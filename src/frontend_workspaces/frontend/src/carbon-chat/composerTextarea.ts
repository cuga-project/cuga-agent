/*
 *  Copyright IBM Corp. 2026
 *
 *  Shared helpers for locating and writing to the Carbon AI Chat composer
 *  input. The composer lives inside a (possibly nested) shadow DOM, and the
 *  underlying element varies across Carbon versions: older builds used a
 *  ``<textarea>``; ``@carbon/ai-chat@1.6.x`` uses a ``contenteditable`` host
 *  with ``role="textbox"``. This module treats both shapes uniformly so
 *  SlashCommandDropdown (autocomplete) and SlashChips (unknown-command
 *  suggestions) share one traversal + value-setting path.
 */

/** A composer input is either a textarea/input or a contenteditable host. */
export type ComposerInput = HTMLTextAreaElement | HTMLInputElement | HTMLElement;

/** Find every shadow root that may contain the chat composer. */
export function findShadowRoots(anchor: HTMLElement | null): ShadowRoot[] {
  const directCandidates = [
    anchor,
    document.querySelector("cds-custom-aichat-react"),
    document.querySelector("cds-custom-aichat-custom-element"),
    document.querySelector("cds-aichat-react"),
    document.querySelector("cds-aichat-custom-element"),
  ].filter(Boolean) as Array<HTMLElement & { shadowRoot?: ShadowRoot | null }>;

  const roots: ShadowRoot[] = [];
  for (const candidate of directCandidates) {
    const candidateShadow = candidate.shadowRoot;
    if (candidateShadow && !roots.includes(candidateShadow)) {
      roots.push(candidateShadow);
    }
    if (candidateShadow) {
      const nested = Array.from(
        candidateShadow.querySelectorAll(
          "cds-custom-aichat-container, cds-aichat-container",
        ),
      ) as Array<HTMLElement & { shadowRoot?: ShadowRoot | null }>;
      for (const n of nested) {
        if (n.shadowRoot && !roots.includes(n.shadowRoot)) {
          roots.push(n.shadowRoot);
        }
      }
    }
  }
  return roots;
}

const COMPOSER_SELECTOR =
  'textarea, input[type="text"], [contenteditable="true"], [contenteditable=""], [role="textbox"]';

/**
 * True if the element has a non-zero bounding rect — i.e. it's the *visible*
 * composer, not one of Carbon Chat's orphaned/hidden duplicates. After a
 * message is submitted Carbon may replace the composer DOM node entirely
 * while leaving the previous (zero-rect) node attached for a beat; if we
 * keep listening to that one the dropdown never reopens.
 */
function isVisibleComposer(el: Element): boolean {
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

/**
 * Walk every reachable shadow root looking for the composer input. Carbon's
 * exact element has evolved across versions (textarea -> contenteditable
 * div); we cast a wide net and return the first *visible* candidate found.
 * Falling back to the first match only when no visible candidate exists
 * preserves behaviour on first paint (before the composer has been laid out).
 */
export function findComposerInput(anchor: HTMLElement | null): ComposerInput | null {
  const visited = new Set<ShadowRoot>();
  const queue: ShadowRoot[] = findShadowRoots(anchor);
  let firstSeen: ComposerInput | null = null;
  while (queue.length) {
    const root = queue.shift()!;
    if (visited.has(root)) continue;
    visited.add(root);

    const candidates = Array.from(
      root.querySelectorAll(COMPOSER_SELECTOR),
    ) as ComposerInput[];
    for (const candidate of candidates) {
      if (!firstSeen) firstSeen = candidate;
      if (isVisibleComposer(candidate)) return candidate;
    }

    root.querySelectorAll("*").forEach((el) => {
      const sr = (el as HTMLElement & { shadowRoot?: ShadowRoot | null }).shadowRoot;
      if (sr && !visited.has(sr)) queue.push(sr);
    });
  }
  return firstSeen;
}

/** Returns true when the element is no longer the live composer — either
 *  detached from the document or laid out as zero-rect (Carbon's orphaned
 *  duplicate after a submit). Used by the dropdown to decide whether to
 *  re-resolve the composer reference. */
export function isComposerStale(el: ComposerInput | null): boolean {
  if (!el) return true;
  if (!document.contains(el)) return true;
  return !isVisibleComposer(el);
}

/** Alias used by SlashCommandDropdown and SlashChips. */
export const findComposerTextarea = findComposerInput;

function isFormField(el: ComposerInput): el is HTMLTextAreaElement | HTMLInputElement {
  return el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement;
}

/**
 * Read the composer's current text. Form fields expose ``value``;
 * contenteditable hosts (Carbon AI Chat 1.6+) only expose ``textContent``.
 * Returns an empty string when the element has no content.
 */
export function getComposerInputValue(el: ComposerInput | null): string {
  if (!el) return "";
  if (isFormField(el)) return el.value;
  return el.textContent ?? "";
}

/**
 * Replace the composer value programmatically and fire an input event so the
 * Carbon framework picks the change up. Handles both form-field composers
 * (textarea/input) and contenteditable composers — they need different
 * mutation paths.
 */
export function setComposerInputValue(el: ComposerInput | null, value: string): boolean {
  if (!el) return false;
  if (isFormField(el)) {
    const setter = Object.getOwnPropertyDescriptor(
      el instanceof HTMLTextAreaElement
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype,
      "value",
    )?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    try {
      el.setSelectionRange(value.length, value.length);
    } catch {}
    el.focus();
    return true;
  }
  // contenteditable / role=textbox host
  el.focus();
  el.textContent = value;
  try {
    const range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
  } catch {
    /* selection APIs can be flaky inside shadow DOM; non-fatal */
  }
  el.dispatchEvent(
    new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" }),
  );
  return true;
}

/** Alias used by SlashCommandDropdown and SlashChips. */
export const setComposerTextareaValue = setComposerInputValue;

/** Attributes the WAI-ARIA combobox pattern requires on the focused input. */
export interface ComposerAriaAttributes {
  /** ARIA role — typically ``"combobox"`` while the popup is open. */
  role?: string;
  /** ID of the listbox element controlled by the composer. */
  controls?: string;
  /** Whether the popup is currently expanded. */
  expanded?: boolean;
  /** ID of the currently highlighted option, or ``null`` to clear. */
  activedescendant?: string | null;
}

/**
 * Imperatively write the WAI-ARIA combobox attributes onto the composer
 * input. Per the APG combobox pattern, ``role``, ``aria-controls``,
 * ``aria-expanded`` and ``aria-activedescendant`` belong on the focused
 * textbox — not on the listbox. The composer lives inside Carbon's shadow
 * root and gets replaced on every submit, so React can't own these via
 * JSX; this helper writes them directly so the dropdown can re-apply them
 * whenever the composer identity changes.
 *
 * A ``null`` or empty ``activedescendant`` removes the attribute entirely
 * (the APG pattern forbids stale ``aria-activedescendant`` values pointing
 * at non-existent options).
 */
export function setComposerAriaAttributes(
  el: HTMLElement,
  attrs: ComposerAriaAttributes,
): void {
  if (attrs.role !== undefined) {
    el.setAttribute("role", attrs.role);
  }
  if (attrs.controls !== undefined) {
    el.setAttribute("aria-controls", attrs.controls);
  }
  if (attrs.expanded !== undefined) {
    el.setAttribute("aria-expanded", attrs.expanded ? "true" : "false");
  }
  if (attrs.activedescendant !== undefined) {
    if (attrs.activedescendant === null || attrs.activedescendant === "") {
      el.removeAttribute("aria-activedescendant");
    } else {
      el.setAttribute("aria-activedescendant", attrs.activedescendant);
    }
  }
}

/**
 * Remove every ARIA combobox attribute we own from the composer. Called when
 * the dropdown unmounts or when the composer identity changes (so we don't
 * leave stale ``aria-*`` on a detached node).
 *
 * Note: ``role`` restoration is the caller's responsibility — this helper
 * blindly removes the role attribute. The dropdown captures the original
 * role on first run and restores it on close.
 */
export function clearComposerAriaAttributes(el: HTMLElement): void {
  el.removeAttribute("role");
  el.removeAttribute("aria-controls");
  el.removeAttribute("aria-expanded");
  el.removeAttribute("aria-activedescendant");
}
