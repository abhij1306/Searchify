/**
 * Sanitised Markdown renderer for AI-generated content (Content vertical).
 *
 * The model output is UNTRUSTED. Defences, all local to this module:
 *   - raw HTML is never parsed (no `rehype-raw`; react-markdown escapes it),
 *   - URLs pass `safeUrlTransform` — only http/https/mailto survive
 *     (`javascript:`/`data:`/etc. are neutralised to an empty href),
 *   - links open in a new tab with `rel="noopener noreferrer"`,
 *   - a restricted component map (no images, no iframes) with token-only
 *     classes so the output inherits the app theme.
 */
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const SAFE_PROTOCOLS = ['http:', 'https:', 'mailto:'];

/** Allow only http/https/mailto; anything else (javascript:, data:, vbscript:,
 * protocol-relative tricks) collapses to an empty, inert URL. Relative URLs
 * are allowed (they resolve same-origin). */
export function safeUrlTransform(url: string): string {
  const trimmed = url.trim();
  if (trimmed === '') return '';
  try {
    const parsed = new URL(trimmed, 'https://local.invalid');
    if (!SAFE_PROTOCOLS.includes(parsed.protocol)) return '';
  } catch {
    return '';
  }
  return trimmed;
}

/** Render untrusted Markdown safely (GFM tables/lists, no raw HTML). */
export function ContentMarkdown({ markdown }: { markdown: string }) {
  return (
    <div className="space-y-3 text-sm leading-relaxed text-foreground [&_a]:text-accent-text [&_a]:underline [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-secondary [&_code]:rounded [&_code]:bg-background-alt [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-xs [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:text-base [&_h3]:font-semibold [&_li]:ml-4 [&_ol]:list-decimal [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-background-alt [&_pre]:p-3 [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-border [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_ul]:list-disc">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={safeUrlTransform}
        components={{
          // Untrusted output: never render images (remote-fetch beacon risk).
          img: () => null,
          // Forward the remaining DOM props (id, aria-describedby,
          // data-footnote-*) so GFM footnote back-links keep working; `node`
          // is react-markdown's AST handle, not a DOM attribute.
          a: ({ node: _node, children, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
