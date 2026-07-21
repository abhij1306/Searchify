/**
 * Blog content for /blog and /blog/[slug].
 *
 * `POSTS` holds exactly one ready-to-edit placeholder entry — duplicate it and
 * fill in the '[TODO(user)]' fields to publish a real post. The placeholder
 * slug `hello-searchify` is hardcoded in the e2e suite; keep it (or update
 * e2e/marketing-pages.spec.ts in the same change).
 */

export type BlogBlock =
  { type: 'paragraph' | 'heading'; text: string } | { type: 'list'; items: readonly string[] };

export type BlogPost = {
  slug: string;
  title: string;
  excerpt: string;
  date: string;
  readTime: string;
  author: string;
  tags: readonly string[];
  body: readonly BlogBlock[];
};

export const POSTS: readonly BlogPost[] = [
  {
    slug: 'hello-searchify',
    title: '[Draft placeholder] Introducing Searchify: own how your brand appears in AI answers',
    // Grounded in README.md.
    excerpt:
      'Searchify is an open-source AI visibility and site intelligence platform — ' +
      'measure brand presence across ChatGPT, Gemini, and Claude on your own keys, ' +
      'inspect the evidence behind every result, and improve the pages those answers rely on.',
    date: '[TODO(user)]',
    readTime: '[TODO(user)]',
    author: '[TODO(user)]',
    tags: ['Product'],
    // Placeholder body — exercises every BlogBlock variant the template renders.
    body: [
      { type: 'paragraph', text: '[TODO(user)]' },
      { type: 'heading', text: '[TODO(user)]' },
      { type: 'paragraph', text: '[TODO(user)]' },
      { type: 'list', items: ['[TODO(user)]', '[TODO(user)]', '[TODO(user)]'] },
    ],
  },
];

export type BlogEmptyState = {
  heading: string;
  body: string;
};

/** Shown on /blog when POSTS is empty. */
export const BLOG_EMPTY_STATE: BlogEmptyState = {
  heading: 'First posts are on the way.',
  body:
    'We’re writing about AEO, evidence-first scoring, and building Searchify in the open. ' +
    'Until then, the best way to follow along is to watch the repo on GitHub — or register ' +
    'and try the product yourself.',
};
