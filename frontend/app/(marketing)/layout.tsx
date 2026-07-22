import { Bricolage_Grotesque, IBM_Plex_Mono, Public_Sans } from 'next/font/google';
import type { ReactNode } from 'react';

import { LandingFooter } from '@/components/marketing/landing-footer';
import { LandingNav } from '@/components/marketing/landing-nav';
import { MarketingThemeReset } from '@/components/marketing/marketing-theme-reset';

import './marketing.css';

const display = Bricolage_Grotesque({
  subsets: ['latin'],
  weight: ['600', '700'],
  variable: '--font-bricolage',
  display: 'swap',
});
const sans = Public_Sans({ subsets: ['latin'], variable: '--font-public-sans', display: 'swap' });
const mono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--font-plex',
  display: 'swap',
});

/**
 * Marketing route-group layout — public surface, deliberately NOT wrapped in
 * SessionGuard (the landing page must be reachable and server-rendered for
 * anonymous visitors). Loads the marketing brand fonts and scopes all
 * marketing styles under the `.mkt` wrapper (see marketing.css).
 */
export default function MarketingLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <div className={`${display.variable} ${sans.variable} ${mono.variable} mkt`}>
      {/* Handles theme defaulting on mount and restoration on exit (see MarketingThemeReset). */}
      <MarketingThemeReset />
      {/* Shared chrome — every route in the (marketing) group inherits the
          aurora/grain backdrop, the nav, and the footer from this layout. */}
      <div className="aurora" aria-hidden="true">
        <i className="a1" />
        <i className="a2" />
      </div>
      <div className="grain" aria-hidden="true" />
      <LandingNav />
      {children}
      <LandingFooter />
    </div>
  );
}
