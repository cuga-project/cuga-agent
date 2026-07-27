import { describe, expect, it } from 'vitest';
import { pageFragment, pageLabel } from './types';

describe('pageFragment', () => {
  it('opens a PDF at the cited page', () => {
    expect(pageFragment({ filename: 'sovereign_core_overview.pdf', page: 7 })).toBe('#page=7');
  });

  it('is case-insensitive about the extension', () => {
    expect(pageFragment({ filename: 'REPORT.PDF', page: 2 })).toBe('#page=2');
  });

  // For these, `page` is a chunk ordinal rather than a real page (see
  // pageLabel), so a fragment would scroll somewhere arbitrary — landing
  // confidently wrong is worse than opening at the top.
  it.each(['notes.md', 'log.txt', 'data.csv', 'doc.docx', 'page.html'])(
    'returns no fragment for %s',
    (filename) => {
      expect(pageFragment({ filename, page: 3 })).toBe('');
    },
  );

  it('returns no fragment when the page is missing', () => {
    expect(pageFragment({ filename: 'a.pdf', page: null })).toBe('');
    expect(pageFragment({ filename: 'a.pdf', page: undefined })).toBe('');
  });

  it('rejects non-positive pages rather than emitting #page=0', () => {
    expect(pageFragment({ filename: 'a.pdf', page: 0 })).toBe('');
    expect(pageFragment({ filename: 'a.pdf', page: -1 })).toBe('');
  });

  it.each([NaN, Infinity, -Infinity, 2.5, 0.9])(
    'rejects the malformed page value %p (no #page=NaN / #page=2.5 in a URL)',
    (page) => {
      expect(pageFragment({ filename: 'a.pdf', page })).toBe('');
    },
  );

  it('never emits a fragment a viewer would choke on', () => {
    // Guards the concatenation site: anything non-empty must be a clean
    // "#page=<positive int>" so `blobUrl + fragment` is always a valid URL.
    const out = pageFragment({ filename: 'a.pdf', page: 12 });
    expect(out).toMatch(/^#page=[1-9]\d*$/);
  });

  // NTH-1: pageLabel and pageFragment must agree about which formats have real
  // pages. .docx paginates at render time, so both must treat it as pageless —
  // otherwise the panel shows "p.5" but the document opens at the top.
  it.each(['doc.docx', 'notes.md', 'log.txt', 'data.csv'])(
    'pageLabel and pageFragment agree (both empty) for %s',
    (filename) => {
      expect(pageLabel({ filename, page: 5 })).toBe('');
      expect(pageFragment({ filename, page: 5 })).toBe('');
    },
  );

  it('still labels and links real PDF pages', () => {
    expect(pageLabel({ filename: 'r.pdf', page: 5 })).toBe('p.5');
    expect(pageFragment({ filename: 'r.pdf', page: 5 })).toBe('#page=5');
  });
});
