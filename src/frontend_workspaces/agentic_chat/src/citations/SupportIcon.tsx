// The frontend app's webpack build compiles this file with the CLASSIC JSX
// runtime (React.createElement), so this import is required at runtime there
// even though agentic_chat's own react-jsx build never references it.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
import React from 'react';
import { CheckmarkFilled, HelpFilled, Misuse, type CarbonIconType } from '@carbon/icons-react';
import type { CitationSupport } from './types';

const SUPPORT: Record<CitationSupport, { Icon: CarbonIconType; label: string }> = {
  supported: { Icon: CheckmarkFilled, label: "Checked: states what it's cited for" },
  contradicted: { Icon: Misuse, label: "Checked: contradicts what it's cited for" },
  not_stated: { Icon: HelpFilled, label: "Checked: doesn't state what it's cited for" },
};

/** Icon and label for a source's citation-check verdict; null when it wasn't checked. */
export function supportInfo(support?: string): { Icon: CarbonIconType; label: string } | null {
  return support && Object.prototype.hasOwnProperty.call(SUPPORT, support)
    ? SUPPORT[support as CitationSupport]
    : null;
}

/** The verdict a reader most needs to see among some sources: contradicted, then not stated. */
export function worstSupport(sources: { support?: string }[]): CitationSupport | undefined {
  const found = new Set(sources.map((s) => s.support));
  return (['contradicted', 'not_stated'] as const).find((v) => found.has(v));
}

/** The verdict icon. Decorative: its label is in the surrounding text or the tooltip. */
export function SupportIcon({ support }: { support?: string }) {
  const info = supportInfo(support);
  if (!info) return null;
  return <info.Icon size={16} className={`cuga-support cuga-support--${support}`} aria-hidden="true" />;
}
