import type { Metadata } from 'next';

import { BlogIndex } from '@/components/marketing/blog';

const DESCRIPTION =
  'Essays, release notes, and field reports on answer-engine optimization — ' +
  'evidence-first, open source, and straight from the team building Searchify.';

// NOTE: no openGraph.images / metadataBase yet — there is no canonical public
// domain for the app, and OG image URLs must be absolute. Add both once the
// production domain exists.
export const metadata: Metadata = {
  title: 'Searchify Blog — Notes on AI visibility',
  description: DESCRIPTION,
  openGraph: {
    title: 'Searchify Blog — Notes on AI visibility',
    description: DESCRIPTION,
    type: 'website',
    siteName: 'Searchify',
  },
  twitter: {
    card: 'summary',
    title: 'Searchify Blog — Notes on AI visibility',
    description: DESCRIPTION,
  },
};

/**
 * Public marketing blog index (`/blog`). Server-rendered so the full page is
 * in the initial HTML (SEO + first paint); the shared chrome (aurora/grain
 * backdrop, LandingNav, LandingFooter) comes from the (marketing) route-group
 * layout. Content renders from lib/marketing-content/blog (single import
 * site): featured-post slot for the first post, a card grid for the rest, or
 * the empty state when the posts array is empty.
 *
 * Must stay a SYNC component (no async / headers() / cookies()) so the page
 * test can render it directly under Testing Library.
 */
export default function BlogPage() {
  return (
    <main>
      <BlogIndex />
    </main>
  );
}
