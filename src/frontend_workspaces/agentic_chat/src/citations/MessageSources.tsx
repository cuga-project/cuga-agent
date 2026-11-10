// The frontend app's webpack build compiles this file with the CLASSIC JSX
// runtime (React.createElement), so this import is required at runtime there
// even though agentic_chat's own react-jsx build never references it.
// (Dropping it = 'React is not defined' -> blank page. See KnowledgeConfig.tsx.)
// eslint-disable-next-line @typescript-eslint/no-unused-vars
import React from 'react';
import { SupportIcon, supportInfo, worstSupport } from './SupportIcon';
import { pageLabel, scopeLabel, type MessageSource } from './types';

const MAX_VISIBLE = 8;

export function MessageSources({
  sources,
  onOpen,
}: {
  sources: MessageSource[];
  onOpen: (n: number) => void;
}) {
  if (!sources?.length) return null;
  const visible = sources.slice(0, MAX_VISIBLE);
  const hidden = sources.length - visible.length;
  // A contradiction among the hidden sources must not disappear behind '+N more'.
  const hiddenWorst = worstSupport(sources.slice(MAX_VISIBLE));
  return (
    <div className="cuga-msg-sources" role="list" aria-label="Answer sources">
      <span className="cuga-msg-sources__label">Sources</span>
      {visible.map((s) => {
        const support = supportInfo(s.support);
        return (
          <button
            key={s.n}
            role="listitem"
            className="cuga-msg-sources__card"
            onClick={() => onOpen(s.n)}
            title={`${s.filename} — ${scopeLabel(s.scope)}${support ? ` — ${support.label}` : ''}`}
          >
            <span className="cuga-msg-sources__badge">{s.n}</span>
            <SupportIcon support={s.support} />
            <span className="cuga-msg-sources__file" dir="auto">
              {s.filename}
            </span>
            {pageLabel(s) && <span className="cuga-msg-sources__page">{pageLabel(s)}</span>}
            <span className={`cuga-msg-sources__dot cuga-msg-sources__dot--${s.scope}`} />
          </button>
        );
      })}
      {hidden > 0 && (
        <button
          className="cuga-msg-sources__card cuga-msg-sources__more"
          onClick={() => onOpen(visible.length + 1)}
          title={hiddenWorst ? `Includes a source that ${supportInfo(hiddenWorst)?.label.replace('Checked: ', '')}` : undefined}
        >
          <SupportIcon support={hiddenWorst} />+{hidden} more
        </button>
      )}
    </div>
  );
}
