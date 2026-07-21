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
  it('renders the placeholder entry in the featured slot, filtered from the grid', () => {
    const { container } = render(<BlogPage />);
    const placeholder = POSTS[0];

    // Exactly one h1; no h2–h6 may contain the product name (post titles are
    // styled paragraphs, not headings, so the placeholder title is exempt).
    const h1s = screen.getAllByRole('heading', { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent(/notes on/i);
    for (const heading of screen.getAllByRole('heading')) {
      if (heading === h1s[0]) continue;
      expect(heading).not.toHaveTextContent(/searchify/i);
    }

    // Featured slot: first post as the large card, linked to its slug.
    const featured = container.querySelector('.featured');
    expect(featured).not.toBeNull();
    expect(within(featured as HTMLElement).getByText(placeholder.excerpt)).toBeInTheDocument();
    expect(
      within(featured as HTMLElement).getByRole('link', {
        name: /own how your brand appears in ai answers/i,
      }),
    ).toHaveAttribute('href', '/blog/hello-searchify');

    // The featured post is filtered out of the grid below — with the single
    // placeholder entry, every link to the slug lives inside the featured
    // card and the grid section does not render at all.
    expect(container.querySelector('.post-grid')).toBeNull();
    const slugLinks = container.querySelectorAll('a[href="/blog/hello-searchify"]');
    expect(slugLinks.length).toBeGreaterThan(0);
    for (const link of slugLinks) {
      expect(featured?.contains(link)).toBe(true);
    }
  });

  it('maps posts beyond the featured one to the card grid', () => {
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

describe('BlogPostView (`/blog/[slug]` sync view)', () => {
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
