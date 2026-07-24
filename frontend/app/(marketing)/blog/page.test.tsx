import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { BlogPostView } from '@/components/marketing/blog-post';
import type { BlogPost } from '@/lib/marketing-content/blog';
import { BLOG_EMPTY_STATE, POSTS } from '@/lib/marketing-content/blog';

import BlogPage from './page';

// The typed content module is mocked with a lazy POSTS getter so individual
// tests can swap the posts array (empty state, multi-post grid) while the
// default render keeps the module's real placeholder entry. BLOG_EMPTY_STATE
// and the types pass through from the actual module.
const blogState = vi.hoisted(() => ({
  posts: undefined as readonly BlogPost[] | undefined,
}));
vi.mock('@/lib/marketing-content/blog', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/marketing-content/blog')>();
  return {
    ...actual,
    get POSTS() {
      return blogState.posts ?? actual.POSTS;
    },
  };
});

beforeEach(() => {
  blogState.posts = undefined;
});

describe('Blog index (public marketing `/blog`)', () => {
  it('renders the intentional empty state until an editorial post is approved', () => {
    const { container } = render(<BlogPage />);

    // Exactly one h1; no h2–h6 may contain the product name. Post titles are
    // styled paragraphs carrying role="heading" for assistive tech, so this
    // asserts against literal h2–h6 tags rather than the heading role — the
    // placeholder title is a heading to screen readers but not an h2–h6.
    const h1s = screen.getAllByRole('heading', { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent(/notes on/i);
    for (const heading of container.querySelectorAll('h2, h3, h4, h5, h6')) {
      expect(heading).not.toHaveTextContent(/searchify/i);
    }

    expect(
      screen.getByRole('heading', { level: 2, name: BLOG_EMPTY_STATE.heading }),
    ).toBeInTheDocument();
    expect(screen.getByText(BLOG_EMPTY_STATE.body)).toBeInTheDocument();
    expect(container.querySelector('.featured')).toBeNull();
    expect(container.querySelector('.post-grid')).toBeNull();
  });

  // Deferred until finalized editorial fixtures provide a featured post plus grid entries.
  it.skip('maps posts beyond the featured one to the card grid', () => {
    const second: BlogPost = {
      slug: 'second-note',
      title: 'A second note on evidence.',
      excerpt: 'Second excerpt.',
      date: 'Jul 20, 2026',
      readTime: '3 min read',
      author: 'The team',
      tags: ['Field report'],
      body: [],
    };
    blogState.posts = [...POSTS, second];
    const { container } = render(<BlogPage />);

    const grid = container.querySelector('.post-grid');
    expect(grid).not.toBeNull();
    // Grid carries the second post only — the featured placeholder is not duplicated.
    expect(within(grid as HTMLElement).getByRole('link', { name: second.title })).toHaveAttribute(
      'href',
      '/blog/second-note',
    );
    expect(
      within(grid as HTMLElement).queryByText(/own how your brand appears in ai answers/i),
    ).toBeNull();
    expect(screen.getByText('2 posts')).toBeInTheDocument();
  });

  it('renders the empty state when the posts array is empty', () => {
    blogState.posts = [];
    const { container } = render(<BlogPage />);

    expect(
      screen.getByRole('heading', { level: 2, name: BLOG_EMPTY_STATE.heading }),
    ).toBeInTheDocument();
    expect(screen.getByText(BLOG_EMPTY_STATE.body)).toBeInTheDocument();
    expect(container.querySelector('.featured')).toBeNull();
    expect(container.querySelector('.post-grid')).toBeNull();

    // The page hero (and its single h1) still renders above the empty state.
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  });
});

// Deferred until a finalized post supplies real byline and body-block content.
describe.skip('BlogPostView (`/blog/[slug]` sync view)', () => {
  it('renders the title, byline, and every BlogBlock variant', () => {
    const post = POSTS[0];
    const { container } = render(<BlogPostView post={post} />);

    // Single h1 = the post title; back-link returns to the index.
    const h1s = screen.getAllByRole('heading', { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent(post.title);
    expect(screen.getByRole('link', { name: /all notes/i })).toHaveAttribute('href', '/blog');

    // Byline: author, date, and read time render verbatim from the module
    // ('[TODO(user)]' placeholders until the user fills them in).
    expect(container.querySelector('.byline-name')).toHaveTextContent(post.author);
    const meta = container.querySelector('.byline-meta');
    expect(meta).toHaveTextContent(post.date);
    expect(meta).toHaveTextContent(post.readTime);

    // Body blocks: the placeholder exercises all three BlogBlock variants.
    // Lede (excerpt) + two paragraph blocks:
    expect(container.querySelector('.prose .lede')).toHaveTextContent(post.excerpt);
    expect(container.querySelectorAll('.prose > p')).toHaveLength(3);
    // One heading block:
    expect(screen.getByRole('heading', { level: 2, name: '[TODO(user)]' })).toBeInTheDocument();
    // One list block with three items:
    expect(within(screen.getByRole('list')).getAllByRole('listitem')).toHaveLength(3);

    // Closing CTA band links to /register.
    const ctaBand = screen.getByRole('region', { name: 'Get started' });
    expect(within(ctaBand).getByRole('link', { name: /get started/i })).toHaveAttribute(
      'href',
      '/register',
    );
  });
});
