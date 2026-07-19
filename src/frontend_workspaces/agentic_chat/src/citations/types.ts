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
}

const PAGELESS_EXTENSIONS = ['.txt', '.md', '.log', '.json', '.csv', '.xml'];

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
  if (typeof source.page !== 'number' || source.page < 1) return '';
  return source.filename.toLowerCase().endsWith('.pdf') ? `#page=${source.page}` : '';
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
