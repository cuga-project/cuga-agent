/*
 *  Copyright IBM Corp. 2026
 *
 *  Shared helpers for locating and writing to the Carbon AI Chat composer
 *  input. The composer lives inside a (possibly nested) shadow DOM, and the
 *  underlying element varies across Carbon versions: older builds used a
 *  ``<textarea>``; ``@carbon/ai-chat@1.6.x`` uses a ``contenteditable`` host
 *  with ``role="textbox"``. This module treats both shapes uniformly so
 *  SlashCommandDropdown (autocomplete) has one traversal + value-setting path.
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

/** Alias used by SlashCommandDropdown. */
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

/** Alias used by SlashCommandDropdown. */
export const setComposerTextareaValue = setComposerInputValue;

/**
 * Resolve the caret's DOM position inside a contenteditable composer.
 *
 * Selection APIs and shadow DOM interact poorly across engines, so this
 * walks a fallback chain:
 *  1. ``ShadowRoot.getSelection()`` — non-standard but reliable in Chromium,
 *     which is where the app ships (Electron) and where e2e runs.
 *  2. ``window.getSelection()`` — Firefox reports shadow-tree selections on
 *     the document selection object.
 *  3. ``Selection.getComposedRanges()`` — Safari's standards-track API for
 *     reading selections that cross shadow boundaries.
 * Returns ``null`` when the selection can't be resolved inside ``el`` —
 * callers decide whether to fall back (e.g. assume end-of-text on input
 * events, or do nothing on selectionchange events).
 */
function resolveSelectionIn(
  el: HTMLElement,
): { node: Node; offset: number } | null {
  const root = el.getRootNode();
  const candidates: Array<Selection | null> = [];
  const shadowSel = (root as ShadowRoot & {
    getSelection?: () => Selection | null;
  }).getSelection;
  if (root instanceof ShadowRoot && typeof shadowSel === "function") {
    try {
      candidates.push(shadowSel.call(root));
    } catch {
      /* non-fatal */
    }
  }
  candidates.push(window.getSelection());
  for (const sel of candidates) {
    if (!sel || sel.rangeCount === 0) continue;
    const range = sel.getRangeAt(0);
    if (el.contains(range.endContainer)) {
      return { node: range.endContainer, offset: range.endOffset };
    }
  }
  // Safari: composed ranges pierce the shadow boundary explicitly.
  const docSel = window.getSelection() as
    | (Selection & {
        getComposedRanges?: (opts?: { shadowRoots?: ShadowRoot[] }) => Array<{
          endContainer: Node;
          endOffset: number;
        }>;
      })
    | null;
  if (docSel && typeof docSel.getComposedRanges === "function") {
    try {
      const shadowRoots = root instanceof ShadowRoot ? [root] : [];
      const ranges = docSel.getComposedRanges({ shadowRoots });
      const r = ranges?.[0];
      if (r && el.contains(r.endContainer)) {
        return { node: r.endContainer, offset: r.endOffset };
      }
    } catch {
      /* non-fatal */
    }
  }
  return null;
}

/**
 * Character offset of the caret within the composer's text, or ``null`` when
 * it can't be determined (unfocused composer, selection outside it, engine
 * without a usable shadow-selection API). Form fields expose the offset
 * directly; contenteditable hosts are measured by stretching a Range from
 * the start of the composer to the caret and taking its text length.
 */
export function getComposerCaretOffset(el: ComposerInput | null): number | null {
  if (!el) return null;
  if (isFormField(el)) {
    return el.selectionStart ?? null;
  }
  const point = resolveSelectionIn(el);
  if (!point) return null;
  try {
    const range = document.createRange();
    range.selectNodeContents(el);
    range.setEnd(point.node, point.offset);
    return range.toString().length;
  } catch {
    return null;
  }
}

/**
 * Splice ``insert`` over the ``[start, end)`` character range of the composer
 * value and place the caret right after the inserted text. This is the
 * token-replacement primitive behind mid-text autocomplete acceptance:
 * unlike ``setComposerInputValue`` it preserves the surrounding text and
 * does NOT collapse the caret to the end of the content.
 */
export function replaceComposerRange(
  el: ComposerInput | null,
  start: number,
  end: number,
  insert: string,
): boolean {
  if (!el) return false;
  const value = getComposerInputValue(el);
  const from = Math.max(0, Math.min(start, value.length));
  const to = Math.max(from, Math.min(end, value.length));
  const next = value.slice(0, from) + insert + value.slice(to);
  const caret = from + insert.length;
  if (isFormField(el)) {
    const setter = Object.getOwnPropertyDescriptor(
      el instanceof HTMLTextAreaElement
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype,
      "value",
    )?.set;
    if (setter) setter.call(el, next);
    else el.value = next;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    try {
      el.setSelectionRange(caret, caret);
    } catch {}
    el.focus();
    return true;
  }
  // contenteditable / role=textbox host. Composer content is plain text, so
  // rewriting textContent yields a single text node we can place the caret in.
  el.focus();
  el.textContent = next;
  try {
    const range = document.createRange();
    const textNode = el.firstChild;
    if (textNode && textNode.nodeType === Node.TEXT_NODE) {
      const max = textNode.textContent?.length ?? 0;
      range.setStart(textNode, Math.min(caret, max));
      range.collapse(true);
    } else {
      range.selectNodeContents(el);
      range.collapse(false);
    }
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
  } catch {
    /* selection APIs can be flaky inside shadow DOM; non-fatal */
  }
  el.dispatchEvent(
    new InputEvent("input", { bubbles: true, data: insert, inputType: "insertText" }),
  );
  return true;
}

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
