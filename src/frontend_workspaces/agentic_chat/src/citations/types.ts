import { CheckmarkFilled, HelpFilled, Misuse, type CarbonIconType } from '@carbon/icons-react';

export interface MessageSource {
  n: number;
  cite_id: string;
  filename: string;
  page?: number | null;
  section_path?: string;
  scope: string; // "agent" | "session"
  snippet: string;
  score?: number;
  query?: string;
  // Set when knowledge.citation_check is on: whether this source states what it is cited for.
  support?: string; // "supported" | "contradicted" | "not_stated"
  // The passage of this source the verdict rests on, exactly as it appears in the snippet.
  support_quote?: string;
}

const SUPPORT = new Map([
  ['supported', { Icon: CheckmarkFilled, label: "Checked: states what it's cited for" }],
  ['contradicted', { Icon: Misuse, label: "Checked: contradicts what it's cited for" }],
  ['not_stated', { Icon: HelpFilled, label: "Checked: doesn't state what it's cited for" }],
]);

/** Split a snippet around the passage the citation check quoted; null when it has none. */
export function quoteSegments(snippet: string, quote?: string): Array<{ text: string; mark: boolean }> | null {
  const at = quote ? snippet.indexOf(quote) : -1;
  if (!quote || at < 0) return null;
  return [
    { text: snippet.slice(0, at), mark: false },
    { text: quote, mark: true },
    { text: snippet.slice(at + quote.length), mark: false },
  ];
}

/** Whether the quoted passage ends past what a collapsed snippet shows (the panel collapses
 * snippets over 350 characters), so the snippet should open expanded. */
export function quoteBelowFold(source: Pick<MessageSource, 'snippet' | 'support_quote'>): boolean {
  const at = source.support_quote ? source.snippet.indexOf(source.support_quote) : -1;
  return at >= 0 && at + (source.support_quote?.length ?? 0) > 350;
}

/** Icon and label for a source's citation-check verdict; null when it wasn't checked. */
export function supportInfo(support?: string): { Icon: CarbonIconType; label: string } | null {
  return (support && SUPPORT.get(support)) || null;
}

// Formats where "page" is a chunk ordinal, not a real page number. `.docx` is
// here too: Word pagination is a render-time artifact (it reflows with viewport
// and font), so a stored page can't reliably target a location — pageLabel and
// pageFragment must both treat it as pageless or they disagree (NTH-1).
const PAGELESS_EXTENSIONS = ['.txt', '.md', '.log', '.json', '.csv', '.xml', '.docx'];

/** Page label, honest about formats where "page" is really a chunk ordinal. */
export function pageLabel(source: Pick<MessageSource, 'filename' | 'page'>): string {
  if (source.page === null || source.page === undefined) return '';
  const lower = source.filename.toLowerCase();
  if (PAGELESS_EXTENSIONS.some((ext) => lower.endsWith(ext))) return '';
  return `p.${source.page}`;
}

/** URL fragment that opens a document at the cited page. '' = open at the top.
 *
 * PDF only, on purpose. `#page=N` is the fragment every PDF viewer understands,
 * but for the pageless formats above `page` is a chunk ordinal rather than a
 * real page (see pageLabel) — appending it would scroll somewhere arbitrary and
 * look like a bug. Better to open at the top than to land confidently wrong.
 */
export function pageFragment(source: Pick<MessageSource, 'filename' | 'page'>): string {
  const { page } = source;
  // Only a real, positive integer page produces a fragment. typeof + isInteger
  // rejects non-numbers, NaN, ±Infinity, and fractionals, so we never emit
  // "#page=NaN" / "#page=2.5" into a URL that a PDF viewer would choke on.
  if (typeof page !== 'number' || !Number.isInteger(page) || page < 1) return '';
  return source.filename.toLowerCase().endsWith('.pdf') ? `#page=${page}` : '';
}

export function scopeLabel(scope: string): string {
  return scope === 'session' ? 'This conversation' : 'Agent knowledge';
}

export function escapeAttr(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
