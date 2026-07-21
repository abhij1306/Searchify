import type { Metadata } from 'next';

import { EvidenceBand, FinalCta } from '@/components/marketing/cta-band';
import { EngineStrip } from '@/components/marketing/engine-strip';
import { FeaturesGrid } from '@/components/marketing/features-grid';
import { HowItWorks } from '@/components/marketing/how-it-works';
import { LandingFooter } from '@/components/marketing/landing-footer';
import { LandingHero } from '@/components/marketing/landing-hero';
import { LandingNav } from '@/components/marketing/landing-nav';
import { LandingSessionRedirect } from '@/components/marketing/landing-session-redirect';
import { ProductVisual } from '@/components/marketing/product-visual';

const DESCRIPTION =
  'Searchify audits ChatGPT, Gemini, and Claude with the prompts your buyers actually ask. ' +
  'Every response is scored deterministically — mentions, citations, share of voice — with ' +
  'the raw evidence behind every number. Bring your own API keys; encrypted at rest.';

// NOTE: no openGraph.images / metadataBase yet — there is no canonical public
// domain for the app, and OG image URLs must be absolute. Add both once the
// production domain exists.
export const metadata: Metadata = {
  title: 'Searchify — See how AI answers talk about your brand',
  description: DESCRIPTION,
  openGraph: {
    title: 'Searchify — See how AI answers talk about your brand',
    description: DESCRIPTION,
    type: 'website',
    siteName: 'Searchify',
  },
  twitter: {
    card: 'summary',
    title: 'Searchify — See how AI answers talk about your brand',
    description: DESCRIPTION,
  },
};

/**
 * Public marketing landing page (`/`). Server-rendered so the full page is in
 * the initial HTML (SEO + first paint); the only client islands are the nav
 * (dropdowns/mobile menu/theme toggle) and the invisible LandingSessionRedirect,
 * which forwards signed-in visitors on to their dashboard (`/visibility`) or
 * first-run `/setup` — the contract `/` had before this page existed.
 *
 * Must stay a SYNC component (no async / headers() / cookies()) so the page
 * test can render it directly under Testing Library.
 */
export default function LandingPage() {
  return (
    <>
      <LandingSessionRedirect />
      <div className="aurora" aria-hidden="true">
        <i className="a1" />
        <i className="a2" />
      </div>
      <div className="grain" aria-hidden="true" />
      <LandingNav />
      <main>
        <LandingHero />
        <ProductVisual />
        <EngineStrip />
        <FeaturesGrid />
        <HowItWorks />
        <EvidenceBand />
        <FinalCta />
      </main>
      <LandingFooter />
    </>
  );
}
