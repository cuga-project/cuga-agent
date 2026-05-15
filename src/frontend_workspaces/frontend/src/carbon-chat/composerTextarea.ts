/*
 *  Copyright IBM Corp. 2026
 *
 *  Shared helpers for locating and writing to the Carbon AI Chat composer
 *  input. The composer lives inside a (possibly nested) shadow DOM, and the
 *  underlying element varies across Carbon versions: older builds used a
 *  ``<textarea>``; ``@carbon/ai-chat@1.6.x`` uses a ``contenteditable`` host
 *  with ``role="textbox"``. This module treats both shapes uniformly.
 *
 *  Originally written for the slash-command autocomplete dropdown (slice #18)
 *  and factored out here so the unknown-command suggestion chips (slice #23)
 *  reuse the exact same traversal + value-setting mechanism rather than
 *  duplicating it.
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
 * Walk every reachable shadow root looking for the composer input. Carbon's
 * exact element has evolved across versions (textarea -> contenteditable
 * div); we cast a wide net and return the first interactive candidate found.
 */
export function findComposerInput(anchor: HTMLElement | null): ComposerInput | null {
  const visited = new Set<ShadowRoot>();
  const queue: ShadowRoot[] = findShadowRoots(anchor);
  while (queue.length) {
    const root = queue.shift()!;
    if (visited.has(root)) continue;
    visited.add(root);

    const direct = root.querySelector(COMPOSER_SELECTOR) as ComposerInput | null;
    if (direct) return direct;

    root.querySelectorAll("*").forEach((el) => {
      const sr = (el as HTMLElement & { shadowRoot?: ShadowRoot | null }).shadowRoot;
      if (sr && !visited.has(sr)) queue.push(sr);
    });
  }
  return null;
}

/** Back-compat alias retained for slice #18's callsite. */
export const findComposerTextarea = findComposerInput;

function isFormField(el: ComposerInput): el is HTMLTextAreaElement | HTMLInputElement {
  return el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement;
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
    } catch {
      /* some inputs disallow setSelectionRange */
    }
    el.focus();
    return true;
  }
  // contenteditable / role=textbox host
  el.focus();
  el.textContent = value;
  // Place caret at the end.
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

/** Back-compat alias retained for slice #18's callsite. */
export const setComposerTextareaValue = setComposerInputValue;
