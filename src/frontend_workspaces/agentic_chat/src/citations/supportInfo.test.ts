import { describe, expect, it } from 'vitest';
import { supportInfo } from './types';

describe('supportInfo', () => {
  it.each([
    ['supported', '✓'],
    ['contradicted', '✗'],
    ['not_stated', '?'],
  ])('marks a %s source with %s', (support, mark) => {
    expect(supportInfo(support)?.mark).toBe(mark);
  });

  it('shows nothing for a source that was not checked', () => {
    expect(supportInfo(undefined)).toBeNull();
    expect(supportInfo('maybe')).toBeNull();
    expect(supportInfo('constructor')).toBeNull();
  });
});
