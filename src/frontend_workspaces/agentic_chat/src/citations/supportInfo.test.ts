import { CheckmarkFilled, HelpFilled, Misuse } from '@carbon/icons-react';
import { describe, expect, it } from 'vitest';
import { quoteBelowFold, quoteSegments, supportInfo } from './types';

describe('supportInfo', () => {
  it.each([
    ['supported', CheckmarkFilled],
    ['contradicted', Misuse],
    ['not_stated', HelpFilled],
  ])('marks a %s source with its icon', (support, Icon) => {
    expect(supportInfo(support)?.Icon).toBe(Icon);
  });

  it('shows nothing for a source that was not checked', () => {
    expect(supportInfo(undefined)).toBeNull();
    expect(supportInfo('maybe')).toBeNull();
    expect(supportInfo('constructor')).toBeNull();
  });
});

describe('quoteSegments', () => {
  it('marks the quoted passage inside the snippet', () => {
    expect(quoteSegments('Invoice INV-9 was issued to Contoso. Paid.', 'issued to Contoso')).toEqual([
      { text: 'Invoice INV-9 was ', mark: false },
      { text: 'issued to Contoso', mark: true },
      { text: '. Paid.', mark: false },
    ]);
  });

  it('returns null without a quote or when the quote is not in the snippet', () => {
    expect(quoteSegments('Backups run daily.', undefined)).toBeNull();
    expect(quoteSegments('Backups run daily.', 'weekly')).toBeNull();
  });
});

describe('quoteBelowFold', () => {
  const filler = 'x'.repeat(400);
  it('opens the snippet when the quote ends past the collapsed part', () => {
    expect(quoteBelowFold({ snippet: `${filler} the cap is 220 dollars.`, support_quote: 'the cap is 220 dollars.' })).toBe(true);
  });
  it('keeps it collapsed when the quote is visible, missing or absent', () => {
    expect(quoteBelowFold({ snippet: `the cap is 220 dollars. ${filler}`, support_quote: 'the cap is 220 dollars.' })).toBe(false);
    expect(quoteBelowFold({ snippet: filler, support_quote: 'not there' })).toBe(false);
    expect(quoteBelowFold({ snippet: filler })).toBe(false);
  });
});
