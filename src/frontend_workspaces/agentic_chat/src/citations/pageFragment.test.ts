import { describe, expect, it } from 'vitest';
import { pageFragment } from './types';

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

  it('never emits a fragment a viewer would choke on', () => {
    // Guards the concatenation site: anything non-empty must be a clean
    // "#page=<positive int>" so `blobUrl + fragment` is always a valid URL.
    const out = pageFragment({ filename: 'a.pdf', page: 12 });
    expect(out).toMatch(/^#page=[1-9]\d*$/);
  });
});
