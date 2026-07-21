// citeElement.ts
// Self-contained citation chip. Registered on the GLOBAL custom-element
// registry, so it upgrades both in light DOM (CardManager marked HTML) and
// inside @carbon/ai-chat's nested shadow roots (verified: ai-chat 1.6.0 dist/es
// uses the global registry and its markdown renderer allows custom elements).
// All styling lives in the component's own shadow root — Carbon *tokens*
// inherit as CSS custom properties with hardcoded fallbacks for the extension
// surface, which loads no Carbon CSS in the message area.

export const CITE_CLICK_EVENT = 'cuga-cite-click';

const TEMPLATE = `
<style>
  :host { display: inline-block; vertical-align: super; line-height: 0; position: relative; }
  button {
    all: unset; cursor: pointer; box-sizing: border-box;
    min-width: 16px; height: 16px; padding: 0 5px; border-radius: 8px;
    font: 600 11px/16px "IBM Plex Sans", system-ui, sans-serif; text-align: center;
    color: var(--cds-link-primary, #0f62fe);
    background: var(--cds-layer-02, #f4f4f4);
    border: 1px solid var(--cds-border-subtle-01, #e0e0e0);
    transition: background 70ms ease, color 70ms ease;
  }
  button:hover { background: var(--cds-layer-hover-02, #e8e8e8); }
  button:focus-visible { outline: 2px solid var(--cds-focus, #0f62fe); outline-offset: 1px; }
  :host([active]) button { background: var(--cds-link-primary, #0f62fe); color: #ffffff; }
  .card {
    display: none; position: fixed; inset: auto; margin: 0;
    transform: translate(-50%, calc(-100% - 8px)); z-index: 9000;
    width: max-content; max-width: 280px; padding: 10px 12px;
    background: var(--cds-layer-01, #ffffff);
    border: 1px solid var(--cds-border-subtle-01, #e0e0e0);
    border-radius: 4px; box-shadow: 0 2px 6px rgba(0,0,0,.2);
    font: 400 12px/1.4 "IBM Plex Sans", system-ui, sans-serif;
    color: var(--cds-text-primary, #161616); text-align: start; line-height: 1.4;
    cursor: pointer; overflow: visible;
  }
  .card:hover .hint { text-decoration: underline; }
  /* Modern browsers: the card is shown as a popover in the browser TOP LAYER
     (driven by JS), which is positioned relative to the viewport and clipped by
     nothing — the real fix for tooltips cut off inside a markdown table's
     transformed/overflow scroll wrapper. inset:auto/margin:0 above cancel the
     UA popover centering so our left/top + transform still place it. */
  .card:popover-open { display: block; }
  /* Transparent bridge spanning the 8px gap between the card and the chip, so
     the pointer never crosses dead space traveling to click the card. */
  .card::after { content: ""; position: absolute; left: 0; right: 0; bottom: -8px; height: 8px; }
  /* Fallback (no Popover API): plain position:fixed shown on hover. Escapes
     overflow clipping but NOT a transformed ancestor. The :not([popover]) guard
     keeps this from fighting the popover path (which sets the attribute). */
  :host(:hover) .card:not([popover]), :host(:focus-within) .card:not([popover]) { display: block; }
  .head { display: flex; gap: 8px; justify-content: space-between; align-items: baseline; }
  .file { font-weight: 600; max-width: 180px; overflow: hidden; text-overflow: ellipsis;
          white-space: nowrap; unicode-bidi: plaintext; }
  .meta, .hint { color: var(--cds-text-secondary, #525252); font-size: 11px; }
  .section { color: var(--cds-text-secondary, #525252); font-size: 11px; margin-top: 2px; }
  .preview { margin-top: 6px; color: var(--cds-text-secondary, #525252); font-style: italic; }
  .hint { margin-top: 6px; color: var(--cds-link-primary, #0f62fe); }
</style>
<button type="button" aria-haspopup="dialog"></button>
<span class="card" role="tooltip">
  <span class="head"><span class="file"></span><span class="meta"></span></span>
  <span class="section"></span>
  <span class="preview"></span>
  <span class="hint">Click to view source →</span>
</span>`;

export class CugaCiteElement extends HTMLElement {
  static get observedAttributes() {
    return ['n', 'filename', 'page', 'scope', 'section', 'preview'];
  }

  connectedCallback() {
    if (!this.shadowRoot) {
      const root = this.attachShadow({ mode: 'open' });
      root.innerHTML = TEMPLATE;
      const open = (e: Event) => {
        e.stopPropagation();
        this.dispatchEvent(
          new CustomEvent(CITE_CLICK_EVENT, {
            bubbles: true,
            composed: true, // crosses ai-chat's shadow boundaries
            detail: { n: Number(this.getAttribute('n') || 0) },
          }),
        );
      };
      root.querySelector('button')!.addEventListener('click', open);
      // The hover card advertises "Click to view source" — honor clicks on
      // the card itself, not just the chip underneath it.
      root.querySelector('.card')!.addEventListener('click', open);
      // The hover card must escape ancestor clipping. position:fixed alone
      // escapes overflow, but NOT a transformed / contain'd ancestor — and a
      // markdown-table scroll wrapper is exactly that, re-establishing a
      // containing block that clips the card. The robust fix: promote the card
      // to the browser TOP LAYER via the Popover API, which is positioned
      // relative to the viewport and clipped by nothing. Anchor it to the chip
      // on hover/focus; the CSS transform centers it and pops it above.
      const card = root.querySelector('.card') as HTMLElement;
      const usePopover = typeof (card as HTMLElement & { showPopover?: () => void }).showPopover === 'function';
      if (usePopover) card.setAttribute('popover', 'manual');
      const place = () => {
        const r = this.getBoundingClientRect();
        card.style.left = `${r.left + r.width / 2}px`;
        card.style.top = `${r.top}px`;
      };
      let hideTimer: number | undefined;
      const cancelHide = () => {
        if (hideTimer !== undefined) {
          clearTimeout(hideTimer);
          hideTimer = undefined;
        }
      };
      const show = () => {
        cancelHide();
        place();
        // Fallback path (no popover attribute) is shown purely by the CSS
        // :host(:hover) rule — nothing to do here.
        if (!usePopover) return;
        try {
          if (!card.matches(':popover-open')) (card as HTMLElement & { showPopover: () => void }).showPopover();
        } catch {
          /* showPopover throws if already open / not connected — safe to ignore */
        }
      };
      const hideNow = () => {
        cancelHide();
        if (!usePopover) return;
        try {
          if (card.matches(':popover-open')) (card as HTMLElement & { hidePopover: () => void }).hidePopover();
        } catch {
          /* hidePopover throws if not currently open — safe to ignore */
        }
      };
      // Defer the hide so the pointer can travel from the chip up onto the card
      // — which is promoted to the top layer and sits OUTSIDE the host's box, so
      // the host's mouseleave fires as the pointer crosses the gap. Entering the
      // card (or its ::after bridge) cancels the pending hide, keeping the
      // advertised "Click to view source" affordance reachable.
      const scheduleHide = () => {
        cancelHide();
        hideTimer = window.setTimeout(hideNow, 140);
      };
      this.addEventListener('mouseenter', show);
      this.addEventListener('focusin', show);
      this.addEventListener('mouseleave', scheduleHide);
      this.addEventListener('focusout', scheduleHide);
      card.addEventListener('mouseenter', cancelHide);
      card.addEventListener('mouseleave', scheduleHide);
    }
    this.render();
  }

  attributeChangedCallback() {
    if (this.shadowRoot) this.render();
  }

  private render() {
    const root = this.shadowRoot!;
    const n = this.getAttribute('n') || '';
    const button = root.querySelector('button')!;
    button.textContent = n;
    button.setAttribute('aria-label', `Source ${n}: ${this.getAttribute('filename') || ''}`);
    root.querySelector('.file')!.textContent = this.getAttribute('filename') || '';
    const scope = this.getAttribute('scope') === 'session' ? 'session' : 'agent';
    const page = this.getAttribute('page') || '';
    root.querySelector('.meta')!.textContent = [scope, page].filter(Boolean).join(' · ');
    root.querySelector('.section')!.textContent = this.getAttribute('section') || '';
    const preview = this.getAttribute('preview') || '';
    root.querySelector('.preview')!.textContent = preview ? `“${preview}…”` : '';
  }
}

export function registerCiteElement(): void {
  if (typeof window !== 'undefined' && !customElements.get('cuga-cite')) {
    customElements.define('cuga-cite', CugaCiteElement);
  }
}
