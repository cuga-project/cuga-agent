// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { injectCitations } from './injectCitations';
import type { MessageSource } from './types';

const SRC = (n: number): MessageSource => ({
  n,
  cite_id: `s${n}`,
  filename: `f${n}.pdf`,
  page: n,
  scope: 'agent',
  snippet: 'text',
});

describe('injectCitations', () => {
  it('replaces known [n] with cuga-cite elements', () => {
    const out = injectCitations('fact [1].', [SRC(1)]);
    expect(out).toContain('<cuga-cite n="1"');
    expect(out).toContain('filename="f1.pdf"');
    expect(out).not.toContain('[1]');
  });

  it('leaves unknown bracket numbers alone', () => {
    expect(injectCitations('array [3] index', [SRC(1)])).toBe('array [3] index');
  });

  it('never rewrites inside code fences or inline code', () => {
    const text = 'cite [1]\n```\nx[1]\n```\nand `y[1]` done [1]';
    const out = injectCitations(text, [SRC(1)]);
    expect(out).toContain('x[1]');
    expect(out).toContain('`y[1]`');
    expect(out.match(/<cuga-cite/g)?.length).toBe(2);
  });

  it('never rewrites inside double-backtick inline code', () => {
    // ``arr[1]`` is a double-backtick span; it must stay byte-identical (it was
    // mangled + falsely cited before CODE_SPLIT mirrored the backend _CODE_RE).
    const out = injectCitations('see ``arr[1]`` and cite [1]', [SRC(1)]);
    expect(out).toContain('``arr[1]``');
    expect(out.match(/<cuga-cite/g)?.length).toBe(1);
  });

  it('escapes hostile filenames', () => {
    const evil: MessageSource = { ...SRC(1), filename: '"><img src=x onerror=1>' };
    const out = injectCitations('x [1]', [evil]);
    expect(out).not.toContain('<img');
    expect(out).toContain('&quot;&gt;&lt;img');
  });

  it('returns text unchanged when no sources', () => {
    expect(injectCitations('plain [1]', [])).toBe('plain [1]');
  });

  it('stamps an escaped msg attribute when messageKey is given', () => {
    const out = injectCitations('fact [1]', [SRC(1)], 'msg-"7"');
    expect(out).toContain('msg="msg-&quot;7&quot;"');
    expect(injectCitations('fact [1]', [SRC(1)])).not.toContain('msg=');
  });
});
