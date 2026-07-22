/**
 * Blog content for /blog and /blog/[slug].
 *
 * `POSTS` is intentionally empty until editorial content is approved. The
 * index renders its designed empty state, and all `/blog/[slug]` paths return
 * 404 until a reviewed entry is added here.
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

/** Posts are published only after editorial review; the blog renders its designed empty state meanwhile. */
export const POSTS: readonly BlogPost[] = [];

export type BlogEmptyState = {
  heading: string;
  body: string;
};

/** Shown on /blog when POSTS is empty. */
export const BLOG_EMPTY_STATE: BlogEmptyState = {
  heading: 'First posts are on the way.',
  body:
    'We’re writing about AEO, evidence-first scoring, and how teams measure AI visibility. ' +
    'Until then, the best way to follow along is to register and try the product yourself.',
};
