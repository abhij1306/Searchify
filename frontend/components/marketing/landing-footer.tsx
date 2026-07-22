import { ArrowUpRight } from 'lucide-react';
import Link from 'next/link';

import { COMPETITORS } from '@/lib/marketing-content/compare';
import { CONTACT_EMAIL, SOCIAL_LINKS, type SocialLink } from '@/lib/marketing-content/social';

import { LogoCube } from '@/components/ui/logo-cube';

type FooterLink = {
  label: string;
  href: string;
  /** External links open in a new tab and carry the .f-ext arrow glyph. */
  external?: boolean;
};

type FooterColumn = {
  key: string;
  label: string;
  links: readonly FooterLink[];
};

/**
 * The five footer columns. The Compare column maps COMPETITORS from the
 * content module so it stays in sync with /compare; everything else is a
 * static route. '#' hrefs are user-fillable placeholders (see social.ts).
 */
const FOOTER_COLUMNS: readonly FooterColumn[] = [
  {
    key: 'product',
    label: 'Product',
    links: [
      { label: 'Features', href: '/#features' },
      { label: 'How it works', href: '/#how-it-works' },
      { label: 'Evidence', href: '/#evidence' },
      { label: 'Pricing', href: '/pricing' },
      { label: 'Enterprise', href: '/enterprise' },
    ],
  },
  {
    key: 'resources',
    label: 'Resources',
    links: [
      { label: 'Blog', href: '/blog' },
      { label: 'FAQ', href: '/faq' },
      { label: 'Compare', href: '/compare' },
    ],
  },
  {
    key: 'solutions',
    label: 'Solutions',
    links: [
      { label: 'Agencies', href: '/solutions#agencies' },
      { label: 'In-house teams', href: '/solutions#in-house' },
      { label: 'Founders', href: '/solutions#founders' },
      { label: 'PR & comms', href: '/solutions#pr' },
    ],
  },
  {
    key: 'compare',
    label: 'Compare',
    links: [
      { label: 'All comparisons', href: '/compare' },
      ...COMPETITORS.map((competitor) => ({
        label: `vs ${competitor.name}`,
        href: `/compare/${competitor.slug}`,
      })),
    ],
  },
  {
    key: 'company',
    label: 'Company',
    links: [
      ...(CONTACT_EMAIL ? [{ label: 'Contact', href: `mailto:${CONTACT_EMAIL}` }] : []),
      { label: 'Sign in', href: '/login' },
      { label: 'Get started', href: '/register' },
    ],
  },
];

/** One footer-column link: internal routes via next/link, everything else <a>. */
function FooterColumnLink({ link }: { link: FooterLink }) {
  if (link.external) {
    return (
      <a href={link.href} target="_blank" rel="noreferrer">
        {link.label}
        <ArrowUpRight className="f-ext" aria-hidden />
      </a>
    );
  }
  if (link.href.startsWith('/')) {
    return <Link href={link.href}>{link.label}</Link>;
  }
  return <a href={link.href}>{link.label}</a>;
}

/** Social chip: '#' placeholders stay plain anchors; real profiles open externally. */
function SocialButton({ social }: { social: SocialLink }) {
  const Icon = social.icon;
  const external = social.href !== '#';
  return (
    <a
      className="social-btn"
      href={social.href}
      target={external ? '_blank' : undefined}
      rel={external ? 'noreferrer' : undefined}
      aria-label={social.label}
    >
      <Icon aria-hidden />
    </a>
  );
}

/**
 * LandingFooter — multi-column footer: brand block (wordmark, one-line product
 * statement, social chips), five link columns inside the Footer nav landmark,
 * and the legal row. Styling lives in the FOOTER section of marketing.css; the
 * root keeps the `footer` class so the shared transition list keeps applying.
 */
export function LandingFooter() {
  return (
    <footer className="footer">
      <div className="container">
        <nav className="footer-grid" aria-label="Footer">
          <div className="footer-brand">
            <Link className="wordmark" href="/" aria-label="Searchify home">
              <LogoCube size={24} />
              <span>Searchify</span>
            </Link>
            <p className="footer-desc">AI visibility and site intelligence platform.</p>
            {SOCIAL_LINKS.length > 0 ? (
              <div className="social-row">
                {SOCIAL_LINKS.map((social) => (
                  <SocialButton key={social.key} social={social} />
                ))}
              </div>
            ) : null}
          </div>
          {FOOTER_COLUMNS.map((column) => (
            <div className="footer-col" key={column.key}>
              <div className="f-col-label">{column.label}</div>
              <nav className="f-col-links" aria-label={column.label}>
                {column.links.map((link) => (
                  <FooterColumnLink key={link.label} link={link} />
                ))}
              </nav>
            </div>
          ))}
        </nav>
        <div className="footer-bottom">
          <span className="footer-copy">© 2026 Searchify · A CUBE27 product</span>
        </div>
      </div>
    </footer>
  );
}
