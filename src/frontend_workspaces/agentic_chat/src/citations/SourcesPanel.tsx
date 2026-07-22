// The frontend app's webpack build compiles this file with the CLASSIC JSX
// runtime (React.createElement), so this import is required at runtime there
// even though agentic_chat's own react-jsx build never references it.
// (Dropping it = 'React is not defined' -> blank page. See KnowledgeConfig.tsx.)
// eslint-disable-next-line @typescript-eslint/no-unused-vars
import React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { pageLabel, scopeLabel, type MessageSource } from './types';
import './SourcesPanel.css';

/** Split snippet into plain/mark segments for the retrieving-query terms.
 * Pure segmentation — never builds HTML strings, so hostile snippets are inert. */
export function highlightSegments(
  snippet: string,
  query?: string,
): Array<{ text: string; mark: boolean }> {
  const terms = (query || '')
    .split(/\s+/)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .filter((t) => t.length >= 3);
  if (!terms.length || !snippet) return [{ text: snippet, mark: false }];
  const re = new RegExp(`(${terms.join('|')})`, 'giu');
  // Split on the capture group: odd-indexed parts are the captured separators
  // (matches). Checking with re.test() would be wrong — the `g` flag makes
  // test() stateful via lastIndex.
  const parts = snippet.split(re);
  return parts.map((part, i) => ({ text: part, mark: i % 2 === 1 && part.length > 0 }));
}

export interface SourcesPanelProps {
  sources: MessageSource[];
  activeN?: number | null;
  onClose: () => void;
  /** Optional doc opener; when provided a "Open document" button renders.
   * Wire to GET /api/knowledge/documents/file via the host app's api module. */
  onOpenDocument?: (source: MessageSource) => Promise<boolean>;
}

export default function SourcesPanel({
  sources,
  activeN,
  onClose,
  onOpenDocument,
}: SourcesPanelProps) {
  const activeRef = useRef<HTMLDivElement | null>(null);
  const [unavailable, setUnavailable] = useState<Record<number, boolean>>({});
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [activeN]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const items = useMemo(() => sources ?? [], [sources]);

  return (
    <aside className="cuga-sources-panel" role="complementary" aria-label="Answer sources">
      <header className="cuga-sources-panel__header">
        <button className="cuga-sources-panel__close" onClick={onClose} aria-label="Close sources">
          ✕
        </button>
        <div className="cuga-sources-panel__title">
          <h3>Sources</h3>
          <span className="cuga-sources-panel__subtitle">
            {items.length} source{items.length === 1 ? '' : 's'} · cited in this answer
          </span>
        </div>
      </header>
      <div className="cuga-sources-panel__list">
        {items.length === 0 && (
          <p className="cuga-sources-panel__empty">
            This answer didn't cite knowledge-base sources.
          </p>
        )}
        {items.map((s) => (
          <div
            // Key by the stable cite_id, not the per-message display number:
            // n repeats across messages (both have n=1), so when the panel stays
            // mounted and the sources prop switches, React would reuse the same
            // key=1 DOM node and carry over stale expanded/ref state.
            key={s.cite_id}
            ref={s.n === activeN ? activeRef : undefined}
            className={`cuga-sources-panel__item${s.n === activeN ? ' is-active' : ''}`}
          >
            <div className="cuga-sources-panel__item-head">
              <span className="cuga-sources-panel__badge">{s.n}</span>
              <span className="cuga-sources-panel__file" dir="auto">
                {s.filename}
              </span>
              <span className="cuga-sources-panel__meta">
                {scopeLabel(s.scope)}
                {pageLabel(s) ? ` · ${pageLabel(s)}` : ''}
              </span>
            </div>
            {s.section_path && <div className="cuga-sources-panel__section">{s.section_path}</div>}
            <blockquote
              className={`cuga-sources-panel__snippet${expanded[s.n] ? ' is-expanded' : ''}`}
              onClick={() => setExpanded((e) => ({ ...e, [s.n]: !e[s.n] }))}
            >
              {highlightSegments(s.snippet, s.query).map((seg, i) =>
                seg.mark ? <mark key={i}>{seg.text}</mark> : <span key={i}>{seg.text}</span>,
              )}
            </blockquote>
            {s.snippet.length > 350 && (
              <button
                className="cuga-sources-panel__expand"
                onClick={() => setExpanded((e) => ({ ...e, [s.n]: !e[s.n] }))}
                aria-expanded={!!expanded[s.n]}
              >
                {expanded[s.n] ? 'Show less ↑' : 'Show more ↓'}
              </button>
            )}
            <div className="cuga-sources-panel__foot">
              {s.query && <span className="cuga-sources-panel__query">Found for: “{s.query}”</span>}
              {typeof s.score === 'number' && (
                <span className="cuga-sources-panel__score" title="retrieval relevance">
                  <i style={{ width: `${Math.round(Math.min(1, Math.max(0, s.score)) * 100)}%` }} />
                </span>
              )}
              {onOpenDocument && (
                <button
                  className="cuga-sources-panel__open"
                  disabled={!!unavailable[s.n]}
                  title={unavailable[s.n] ? 'Document no longer in the knowledge base' : undefined}
                  onClick={async () => {
                    // A rejecting opener (network error) must not become an unhandled
                    // rejection; treat it as "unavailable" like a false return.
                    const ok = await onOpenDocument(s).catch(() => false);
                    if (!ok) setUnavailable((u) => ({ ...u, [s.n]: true }));
                  }}
                >
                  Open document ↗
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}
