import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { BlogPostView } from '@/components/marketing/blog-post';
import { POSTS } from '@/lib/marketing-content/blog';

/**
 * Public marketing blog post template (`/blog/[slug]`). Statically generated
 * from the typed posts array in lib/marketing-content/blog (single import
 * site); unknown slugs 404.
 *
 * This route is the ONE allowed exception to the marketing sync-RSC rule:
 * Next 16 hands `params` to the page as a Promise, so the default export is a
 * thin async wrapper that awaits it and delegates all rendering to the sync
 * BlogPostView (which the page test renders directly under Testing Library).
 */

export function generateStaticParams() {
  return POSTS.map((post) => ({ slug: post.slug }));
}

export async function generateMetadata({
  params,
}: Readonly<{
  params: Promise<{ slug: string }>;
}>): Promise<Metadata> {
  const { slug } = await params;
  const post = POSTS.find((candidate) => candidate.slug === slug);
  if (!post) return {};
  const title = `${post.title} — Searchify Blog`;
  // NOTE: no openGraph.images / metadataBase yet — there is no canonical
  // public domain for the app, and OG image URLs must be absolute. Add both
  // once the production domain exists.
  return {
    title,
    description: post.excerpt,
    openGraph: {
      title,
      description: post.excerpt,
      type: 'article',
      siteName: 'Searchify',
    },
    twitter: {
      card: 'summary',
      title,
      description: post.excerpt,
    },
  };
}

export default async function BlogPostPage({
  params,
}: Readonly<{
  params: Promise<{ slug: string }>;
}>) {
  const { slug } = await params;
  const post = POSTS.find((candidate) => candidate.slug === slug);
  if (!post) notFound();
  return (
    <main>
      <BlogPostView post={post} />
    </main>
  );
}
