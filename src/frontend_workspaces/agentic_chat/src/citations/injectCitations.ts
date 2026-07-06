import { escapeAttr, pageLabel, type MessageSource } from './types';

const CODE_SPLIT = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g;
// (?![:(]) keeps markdown reference links ("[1]: url", "[1](url)") intact when
// a link's numeric text collides with a source number.
const MARKER = /\[(\d{1,3})\](?![:(])/g;

/**
 * Replace resolved display markers [n] in answer markdown with <cuga-cite>
 * custom elements. Only numbers present in `sources` are replaced; code
 * fences and inline code are untouched. Output is markdown-with-inline-HTML,
 * safe for both `marked` (CardManager) and @carbon/ai-chat's markdown-it
 * (html:true, custom elements whitelisted).
 *
 * `messageKey` (optional) is stamped as a `msg` attribute on every chip so a
 * document-level click listener can resolve WHICH message's source set the
 * chip belongs to (chips only carry `n`, which repeats across messages).
 */
export function injectCitations(
  text: string,
  sources: MessageSource[],
  messageKey?: string,
): string {
  if (!text || !sources?.length) return text;
  const byN = new Map(sources.map((s) => [s.n, s]));
  const parts = text.split(CODE_SPLIT);
  return parts
    .map((part, i) => {
      if (i % 2 === 1) return part; // code segment
      return part.replace(MARKER, (whole, num) => {
        const source = byN.get(Number(num));
        if (!source) return whole;
        const page = pageLabel(source);
        return (
          `<cuga-cite n="${source.n}"` +
          (messageKey ? ` msg="${escapeAttr(messageKey)}"` : '') +
          ` filename="${escapeAttr(source.filename)}"` +
          (page ? ` page="${escapeAttr(page)}"` : '') +
          ` scope="${escapeAttr(source.scope)}"` +
          (source.section_path ? ` section="${escapeAttr(source.section_path)}"` : '') +
          // Newlines inside an attribute split the surrounding markdown
          // paragraph — normalize whitespace BEFORE slicing (safe order:
          // escaping after slicing can never truncate an entity).
          ` preview="${escapeAttr(
            (source.snippet || '')
              .replace(/\s+/g, ' ')
              .slice(0, 140),
          )}"` +
          `></cuga-cite>`
        );
      });
    })
    .join('');
}
