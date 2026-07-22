import type { Metadata } from 'next';

import {
  EnterpriseContactCta,
  EnterpriseHero,
  EnterpriseLimits,
  EnterpriseOps,
  EnterpriseSelfHost,
} from '@/components/marketing/enterprise';

const DESCRIPTION =
  'Enterprise Searchify: deterministic, auditable AI-visibility scoring over immutable, ' +
  'provenance-carrying evidence. BYOK with Fernet-encrypted write-only keys, UUID workspace ' +
  'isolation, PostgreSQL-durable queues — cloud or self-hosted inside your network.';

// NOTE: no openGraph.images / metadataBase yet — there is no canonical public
// domain for the app, and OG image URLs must be absolute. Add both once the
// production domain exists.
export const metadata: Metadata = {
  title: 'Searchify Enterprise — AI visibility with enterprise-grade evidence',
  description: DESCRIPTION,
  openGraph: {
    title: 'Searchify Enterprise — AI visibility with enterprise-grade evidence',
    description: DESCRIPTION,
    type: 'website',
    siteName: 'Searchify',
  },
  twitter: {
    card: 'summary',
    title: 'Searchify Enterprise — AI visibility with enterprise-grade evidence',
    description: DESCRIPTION,
  },
};

/**
 * Public marketing `/enterprise` page. Server-rendered so the full page is in
 * the initial HTML (SEO + first paint); renders no client islands of its own.
 * The shared chrome (aurora/grain backdrop, LandingNav, LandingFooter) lives
 * in the (marketing) route-group layout.
 *
 * Must stay a SYNC component (no async / headers() / cookies()) so the page
 * test can render it directly under Testing Library.
 */
export default function EnterprisePage() {
  return (
    <main>
      <EnterpriseHero />
      <EnterpriseOps />
      <EnterpriseSelfHost />
      <EnterpriseLimits />
      <EnterpriseContactCta />
    </main>
  );
}
