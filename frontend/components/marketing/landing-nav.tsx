'use client';

import {
  BarChart3,
  ChevronDown,
  Eye,
  Globe,
  KeyRound,
  Menu,
  Moon,
  Sigma,
  Sun,
  TrendingUp,
  X,
  type LucideIcon,
} from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState, useSyncExternalStore } from 'react';

import { applyTheme, readTheme, subscribeTheme } from '@/lib/theme';
import { cn } from '@/lib/utils';

import { LogoCube } from './logo-cube';

type DropKey = 'product' | 'how';

type NavDropItem =
  { icon: LucideIcon; title: string; desc: string } | { num: string; title: string; desc: string };

type NavDrop = {
  key: DropKey;
  label: string;
  href: string;
  items: readonly NavDropItem[];
};

const NAV_DROPS: readonly NavDrop[] = [
  {
    key: 'product',
    label: 'Product',
    href: '#features',
    items: [
      {
        icon: Globe,
        title: 'Three-engine coverage',
        desc: 'ChatGPT, Gemini, and Claude — one audit.',
      },
      { icon: Sigma, title: 'Deterministic scoring', desc: 'Same data, same score — every time.' },
      { icon: Eye, title: 'Evidence explorer', desc: 'Every metric links to its raw run.' },
      {
        icon: BarChart3,
        title: 'Competitor benchmarking',
        desc: 'Share-of-voice, prompt by prompt.',
      },
      { icon: KeyRound, title: 'BYOK privacy', desc: 'Your keys, Fernet-encrypted at rest.' },
      { icon: TrendingUp, title: 'Repeatable trends', desc: 'Visibility, period over period.' },
    ],
  },
  {
    key: 'how',
    label: 'How it works',
    href: '#how-it-works',
    items: [
      { num: '01', title: 'Define your workspace', desc: 'Brand, competitors, prompts.' },
      { num: '02', title: 'Run the audit', desc: 'Prompt × engine × repetition, on your keys.' },
      { num: '03', title: 'Read the evidence', desc: 'Every score drills to the raw response.' },
    ],
  },
];

/** Icon tile for a dropdown/menu row: lucide glyph or mono step number. */
function DropGlyph({ item }: { item: NavDropItem }) {
  if ('num' in item) {
    return <span className="d-icon mono-num">{item.num}</span>;
  }
  const Icon = item.icon;
  return (
    <span className="d-icon">
      <Icon aria-hidden />
    </span>
  );
}

/**
 * LandingNav — sticky glass nav for the public landing page.
 *
 * Desktop: "Product" / "How it works" open hover-intent dropdown panels (open
 * on hover AND keyboard focus; trigger click only ever opens — never closes a
 * hover-open panel — and item click + Esc close; chevron rotates, an invisible
 * bridge covers the trigger→panel gap so the pointer never loses hover).
 * ≤860px: a hamburger opens a slide-down
 * menu with tap-to-expand accordions (Esc closes it too). The bar's backdrop
 * intensifies once the page scrolls. Open state is driven from React (`.open`)
 * so `aria-expanded` stays truthful; class lists go through `cn()` so prettier
 * can't mangle conditional tokens; all transitions live in marketing.css,
 * gated behind prefers-reduced-motion.
 */
export function LandingNav() {
  const theme = useSyncExternalStore(subscribeTheme, readTheme, () => 'light');
  const [scrolled, setScrolled] = useState(false);
  const [openDrop, setOpenDrop] = useState<DropKey | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [openAcc, setOpenAcc] = useState<DropKey | null>(null);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 10);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  const closeMobile = () => {
    setMobileOpen(false);
    setOpenAcc(null);
  };

  // Esc closes an open mobile menu (desktop dropdowns handle their own Esc
  // inside dropProps, where they can also blur the trigger).
  useEffect(() => {
    if (!mobileOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMobileOpen(false);
        setOpenAcc(null);
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [mobileOpen]);

  const dropProps = (key: DropKey) => ({
    onMouseEnter: () => setOpenDrop(key),
    onMouseLeave: () => setOpenDrop((current) => (current === key ? null : current)),
    onFocusCapture: () => setOpenDrop(key),
    onBlurCapture: (event: React.FocusEvent<HTMLDivElement>) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
        setOpenDrop((current) => (current === key ? null : current));
      }
    },
    onKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key === 'Escape') {
        setOpenDrop(null);
        (document.activeElement as HTMLElement | null)?.blur();
      }
    },
  });

  const toggleAcc = (key: DropKey) => setOpenAcc((current) => (current === key ? null : key));

  return (
    <div className="nav-wrap">
      <nav className={cn('site-nav rim', scrolled && 'scrolled')} aria-label="Main navigation">
        <Link className="wordmark" href="/" aria-label="Searchify home">
          <LogoCube size={28} />
          <span>Searchify</span>
          <span className="by-tag">by CUBE27</span>
        </Link>
        <div className="nav-links">
          {NAV_DROPS.map(({ key, label, href, items }) => (
            <div
              className={cn('nav-item', openDrop === key && 'open')}
              key={key}
              {...dropProps(key)}
            >
              <button
                className="nav-link"
                type="button"
                aria-expanded={openDrop === key}
                aria-haspopup="true"
                aria-controls={`drop-${key}`}
                onClick={() => setOpenDrop(key)}
              >
                {label} <ChevronDown className="chev" aria-hidden />
              </button>
              <div className={`drop drop-${key}`} id={`drop-${key}`} role="menu">
                {items.map((item) => (
                  <a
                    className="drop-item"
                    href={href}
                    role="menuitem"
                    key={item.title}
                    onClick={() => setOpenDrop(null)}
                  >
                    <DropGlyph item={item} />
                    <span className="d-text">
                      <b>{item.title}</b>
                      <small>{item.desc}</small>
                    </span>
                  </a>
                ))}
              </div>
            </div>
          ))}
          <a className="nav-link" href="#evidence">
            Evidence
          </a>
        </div>
        <div className="nav-actions">
          <button
            className="hamburger"
            type="button"
            aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={mobileOpen}
            aria-controls="mobile-menu"
            onClick={() => setMobileOpen((open) => !open)}
          >
            <Menu className="icon-menu" aria-hidden />
            <X className="icon-close" aria-hidden />
          </button>
          <button
            className="theme-toggle"
            type="button"
            aria-label="Toggle color theme"
            aria-pressed={theme === 'dark'}
            title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            onClick={() => applyTheme(theme === 'dark' ? 'light' : 'dark')}
          >
            {theme === 'dark' ? (
              <Sun strokeWidth={1.8} aria-hidden />
            ) : (
              <Moon strokeWidth={1.8} aria-hidden />
            )}
          </button>
          <Link className="signin" href="/login">
            Sign in
          </Link>
          <Link className="btn btn-primary btn-sm" href="/register">
            Get started
          </Link>
        </div>
      </nav>
      <div className={cn('mobile-menu', mobileOpen && 'open')} id="mobile-menu">
        {NAV_DROPS.map(({ key, label, href, items }) => (
          <div className={cn('acc', openAcc === key && 'open')} key={key}>
            <button
              className="acc-head"
              type="button"
              aria-expanded={openAcc === key}
              aria-controls={`acc-${key}`}
              onClick={() => toggleAcc(key)}
            >
              {label} <ChevronDown className="chev" aria-hidden />
            </button>
            <div className="acc-body" id={`acc-${key}`}>
              {items.map((item) => (
                <a className="m-item" href={href} key={item.title} onClick={closeMobile}>
                  <DropGlyph item={item} />
                  {item.title}
                </a>
              ))}
            </div>
          </div>
        ))}
        <a className="m-plain" href="#evidence" onClick={closeMobile}>
          Evidence
        </a>
        <div className="m-sep" />
        <Link className="m-plain" href="/login" onClick={closeMobile}>
          Sign in
        </Link>
      </div>
    </div>
  );
}
