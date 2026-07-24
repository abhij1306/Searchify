import type { ReactNode } from 'react';

import { FAQ_GROUPS, type FaqGroup } from '@/lib/marketing-content/faq';

/**
 * FAQ body for `/faq` — the sticky group rail plus the four question groups
 * from the content module (visual spec: /code/.plans/designs/page-faq.html).
 *
 * The accordion is native <details>/<summary> on purpose: it substitutes the
 * mockup's button+JS accordion (styled identically — open state hangs off
 * `details[open]` in marketing.css) so the page ships zero client JS and
 * stays a sync RSC.
 */

// Rail/section anchor per group heading — the approved mockup's ids. The
// slug fallback keeps rail hrefs and section ids consistent if a heading is
// edited later. The `faq-` prefix keeps every id clear of the no-hex guard.
const GROUP_ANCHORS: Record<string, string> = {
  Product: 'faq-product',
  'Privacy & keys': 'faq-privacy',
  'Site health': 'faq-site-health',
  'Account & billing': 'faq-billing',
};

function groupAnchor(group: FaqGroup): string {
  const fallback = group.heading
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return GROUP_ANCHORS[group.heading] ?? `faq-${fallback}`;
}

// Answers are plain strings from the content module. Two inline transforms
// keep them faithful to the mockup: bare '[TODO(user)]' placeholders render
// as the dashed todo-tag pill, and bare URLs render as real links.
const INLINE_TOKEN_RE = /\[TODO\(user\)\]|https?:\/\/\S+/g;
// Sentence punctuation straight after a URL belongs to the prose, not the href.
const TRAILING_PUNCT_RE = /[.,;:!?)]+$/;

function AnswerText({ text }: Readonly<{ text: string }>) {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (const match of text.matchAll(INLINE_TOKEN_RE)) {
    const token = match[0];
    const start = match.index;
    if (start > cursor) nodes.push(text.slice(cursor, start));
    if (token.startsWith('http')) {
      const trailing = token.match(TRAILING_PUNCT_RE)?.[0] ?? '';
      const href = trailing ? token.slice(0, -trailing.length) : token;
      nodes.push(
        <a key={key} href={href} target="_blank" rel="noreferrer">
          {href}
        </a>,
      );
      key += 1;
      if (trailing) nodes.push(trailing);
    } else {
      nodes.push(
        <span key={key} className="todo-tag">
          {token}
        </span>,
      );
      key += 1;
    }
    cursor = start + token.length;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

/** FaqGroups — the "On this page" rail + the four <details> accordion groups. */
export function FaqGroups() {
  return (
    <div className="faq-layout container">
      <nav className="faq-toc" aria-label="FAQ groups">
        <span className="panel-label">On this page</span>
        {FAQ_GROUPS.map((group) => (
          <a key={group.heading} href={`#${groupAnchor(group)}`}>
            {group.heading} <span className="n">{group.items.length}</span>
          </a>
        ))}
      </nav>

      <div className="faq-groups">
        {FAQ_GROUPS.map((group) => (
          <section
            key={group.heading}
            className="faq-group"
            id={groupAnchor(group)}
            aria-label={group.heading}
          >
            <div className="faq-group-head">
              <h2>{group.heading}</h2>
              <span className="n">{group.items.length} answers</span>
            </div>
            {group.items.map((item) => (
              <details key={item.q} className="faq-item">
                <summary className="faq-q">
                  {item.q}
                  <span className="faq-ico" aria-hidden="true">
                    <svg
                      viewBox="0 0 12 12"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={1.6}
                      strokeLinecap="round"
                    >
                      <path d="M1 6h10" />
                      <path className="ico-v" d="M6 1v10" />
                    </svg>
                  </span>
                </summary>
                <div className="faq-a">
                  <div className="faq-a-inner">
                    <p>
                      <AnswerText text={item.a} />
                    </p>
                  </div>
                </div>
              </details>
            ))}
          </section>
        ))}
      </div>
    </div>
  );
}
