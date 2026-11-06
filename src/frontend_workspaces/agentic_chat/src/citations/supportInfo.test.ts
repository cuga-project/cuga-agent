import { CheckmarkFilled, HelpFilled, Misuse } from '@carbon/icons-react';
import { describe, expect, it } from 'vitest';
import { supportInfo } from './types';

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
