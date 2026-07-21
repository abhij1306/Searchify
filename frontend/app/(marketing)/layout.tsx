import { Bricolage_Grotesque, IBM_Plex_Mono, Public_Sans } from 'next/font/google';
import type { ReactNode } from 'react';

import { THEME_STORAGE_KEY } from '@/lib/theme';
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
 * Dark-first default for the marketing surface: when the visitor has made NO
 * explicit theme choice, paint the landing in the approved midnight theme.
 * An explicit stored choice (from any ThemeToggle, anywhere in the product)
 * always wins; nothing is persisted here, so the rest of the app keeps its
 * own default behavior until the visitor actually toggles.
 */
const MARKETING_THEME_DEFAULT_SCRIPT = `(() =\u003e {
  try {
    if (!window.localStorage.getItem('${THEME_STORAGE_KEY}')) {
      document.documentElement.dataset.theme = 'dark';
    }
  } catch (e) {
    /* storage unavailable — leave the shared bootstrap's choice alone */
  }
})();`;

/**
 * Marketing route-group layout — public surface, deliberately NOT wrapped in
 * SessionGuard (the landing page must be reachable and server-rendered for
 * anonymous visitors). Loads the marketing brand fonts and scopes all
 * marketing styles under the `.mkt` wrapper (see marketing.css).
 */
export default function MarketingLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <div className={`${display.variable} ${sans.variable} ${mono.variable} mkt`}>
      <script dangerouslySetInnerHTML={{ __html: MARKETING_THEME_DEFAULT_SCRIPT }} />
      {/* Restores the bootstrap theme on client-side exit so the dark-first
          default above stays marketing-only (see MarketingThemeReset). */}
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
