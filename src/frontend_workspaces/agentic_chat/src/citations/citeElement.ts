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
    display: none; position: fixed; transform: translate(-50%, calc(-100% - 8px)); z-index: 9000;
    width: max-content; max-width: 280px; padding: 10px 12px;
    background: var(--cds-layer-01, #ffffff);
    border: 1px solid var(--cds-border-subtle-01, #e0e0e0);
    border-radius: 4px; box-shadow: 0 2px 6px rgba(0,0,0,.2);
    font: 400 12px/1.4 "IBM Plex Sans", system-ui, sans-serif;
    color: var(--cds-text-primary, #161616); text-align: start; line-height: 1.4;
    cursor: pointer;
  }
  .card:hover .hint { text-decoration: underline; }
  :host(:hover) .card, :host(:focus-within) .card { display: block; }
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
      // The card is position:fixed so it escapes any ancestor's overflow clip
      // (e.g. a markdown table's scroll wrapper). Anchor it to the chip on
      // hover/focus; the CSS transform centers it and pops it above.
      const card = root.querySelector('.card') as HTMLElement;
      const place = () => {
        const r = this.getBoundingClientRect();
        card.style.left = `${r.left + r.width / 2}px`;
        card.style.top = `${r.top}px`;
      };
      this.addEventListener('mouseenter', place);
      this.addEventListener('focusin', place);
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
