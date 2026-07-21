import type { Metadata } from 'next';

import { FaqGroups } from '@/components/marketing/faq';

const DESCRIPTION =
  'The short version of how Searchify works — engines, scoring, keys, site health, ' +
  'billing, and the open-source bits.';

// NOTE: no openGraph.images / metadataBase yet — there is no canonical public
// domain for the app, and OG image URLs must be absolute. Add both once the
// production domain exists.
export const metadata: Metadata = {
  title: 'FAQ — Searchify',
  description: DESCRIPTION,
  openGraph: {
    title: 'FAQ — Searchify',
    description: DESCRIPTION,
    type: 'website',
    siteName: 'Searchify',
  },
  twitter: {
    card: 'summary',
    title: 'FAQ — Searchify',
    description: DESCRIPTION,
  },
};

/**
 * Public marketing FAQ page (`/faq`). Server-rendered so the full page is in
 * the initial HTML (SEO + first paint); the accordion is native
 * <details>/<summary>, so the page renders zero client islands.
 *
 * The shared chrome (aurora/grain backdrop, LandingNav, LandingFooter) lives
 * in the (marketing) route-group layout so every marketing route inherits it.
 *
 * Must stay a SYNC component (no async / headers() / cookies()) so the page
 * test can render it directly under Testing Library.
 */
export default function FaqPage() {
  return (
    <main>
      <header className="page-hero">
        <div className="hero-inner container">
          <span className="eyebrow">FAQ</span>
          <h1>
            Frequently asked <span className="grad-text">questions.</span>
          </h1>
          <p className="hero-sub">
            The short version of how Searchify works — engines, scoring, keys, site health, billing,
            and the open-source bits.
          </p>
        </div>
      </header>
      <FaqGroups />
    </main>
  );
}
