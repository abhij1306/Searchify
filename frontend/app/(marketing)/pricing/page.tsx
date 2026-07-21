import { ArrowRight } from 'lucide-react';
import type { Metadata } from 'next';
import Link from 'next/link';

import { ByokTrust } from '@/components/marketing/byok-trust';
import { PricingCta, PricingTable, PricingTiers } from '@/components/marketing/pricing';

const DESCRIPTION =
  'Pricing for Searchify, the open-source AI visibility and site intelligence platform: ' +
  'free sample crawl, Starter monitoring with quota-controlled URLs, Pro for benchmarking ' +
  'teams, Enterprise — every plan runs on your own ChatGPT, Gemini, and Claude keys.';

// NOTE: no openGraph.images / metadataBase yet — there is no canonical public
// domain for the app, and OG image URLs must be absolute. Add both once the
// production domain exists.
export const metadata: Metadata = {
  title: 'Searchify Pricing — BYOK AI visibility audits, site health & AEO monitoring',
  description: DESCRIPTION,
  openGraph: {
    title: 'Searchify Pricing — BYOK AI visibility audits, site health & AEO monitoring',
    description: DESCRIPTION,
    type: 'website',
    siteName: 'Searchify',
  },
  twitter: {
    card: 'summary',
    title: 'Searchify Pricing — BYOK AI visibility audits, site health & AEO monitoring',
    description: DESCRIPTION,
  },
};

/**
 * Public marketing pricing page (`/pricing`). Server-rendered so the full
 * page is in the initial HTML; it renders NO client islands of its own — the
 * shared chrome (aurora/grain backdrop, LandingNav, LandingFooter) lives in
 * the (marketing) route-group layout.
 *
 * Must stay a SYNC component (no async / headers() / cookies()) so the page
 * test can render it directly under Testing Library.
 */
export default function PricingPage() {
  return (
    <main>
      <header className="page-hero">
        <div className="hero-inner container">
          <span className="eyebrow">Pricing</span>
          <h1>
            Pay for the evidence layer.
            <br />
            <span className="grad-text">Not the API markup.</span>
          </h1>
          <p className="hero-sub">
            Every plan runs audits on your own ChatGPT, Gemini, and Claude keys — provider usage
            bills straight to your accounts at provider rates. Searchify charges for the workspace,
            the monitoring, and the evidence behind every score.
          </p>
        </div>
      </header>
      <PricingTiers />
      <PricingTable />
      {/* Trust strip: the plan price never includes model-call markup — audits
          run on the visitor's own provider keys. */}
      <section className="band byok-strip" aria-label="Bring your own keys">
        <div className="container">
          <ByokTrust />
        </div>
      </section>
      <section className="faq-teaser" aria-label="FAQ">
        <div className="container">
          <div className="card faq-card rim">
            <div>
              <h3>Questions about plans?</h3>
              <p>
                How quotas are counted, what BYOK bills to whom, what happens when you hit a limit —
                the FAQ covers the details.
              </p>
            </div>
            <Link className="btn btn-ghost" href="/faq">
              Read the FAQ
              <ArrowRight className="arr" size={15} strokeWidth={2.2} aria-hidden />
            </Link>
          </div>
        </div>
      </section>
      <PricingCta />
    </main>
  );
}
