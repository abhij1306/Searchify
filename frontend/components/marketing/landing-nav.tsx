'use client';

import {
  ArrowUpRight,
  BarChart3,
  Building2,
  ChevronDown,
  CircleHelp,
  Eye,
  Globe,
  KeyRound,
  Megaphone,
  Menu,
  Moon,
  Newspaper,
  Rocket,
  Scale,
  Sigma,
  Sun,
  TrendingUp,
  Users,
  X,
  type LucideIcon,
} from 'lucide-react';
import Link from 'next/link';
import { Fragment, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import { applyTheme, readTheme, subscribeTheme } from '@/lib/theme';
import { cn } from '@/lib/utils';

import { LogoCube } from './logo-cube';

type DropKey = 'product' | 'resources' | 'solutions';

type NavDropItem =
  | { icon: LucideIcon; title: string; desc: string; href: string; external?: boolean }
  | { num: string; title: string; desc: string; href: string };

type NavDropGroup = {
  label?: string;
  items: readonly NavDropItem[];
};

type NavDrop = {
  key: DropKey;
  label: string;
  groups: readonly NavDropGroup[];
};

const NAV_DROPS: readonly NavDrop[] = [
  {
    key: 'product',
    label: 'Product',
    groups: [
      {
        items: [
          {
            icon: Globe,
            title: 'Three-engine coverage',
            desc: 'ChatGPT, Gemini, and Claude — one audit.',
            href: '/#features',
          },
          {
            icon: Sigma,
            title: 'Deterministic scoring',
            desc: 'Same data, same score — every time.',
            href: '/#features',
          },
          {
            icon: Eye,
            title: 'Evidence explorer',
            desc: 'Every metric links to its raw run.',
            href: '/#features',
          },
          {
            icon: BarChart3,
            title: 'Competitor benchmarking',
            desc: 'Share-of-voice, prompt by prompt.',
            href: '/#features',
          },
          {
            icon: KeyRound,
            title: 'BYOK privacy',
            desc: 'Your keys, Fernet-encrypted at rest.',
            href: '/#features',
          },
          {
            icon: TrendingUp,
            title: 'Repeatable trends',
            desc: 'Visibility, period over period.',
            href: '/#features',
          },
        ],
      },
      {
        label: 'How it works',
        items: [
          {
            num: '01',
            title: 'Define your workspace',
            desc: 'Brand, competitors, prompts.',
            href: '/#how-it-works',
          },
          {
            num: '02',
            title: 'Run the audit',
            desc: 'Prompt × engine × repetition, on your keys.',
            href: '/#how-it-works',
          },
          {
            num: '03',
            title: 'Read the evidence',
            desc: 'Every score drills to the raw response.',
            href: '/#how-it-works',
          },
        ],
      },
    ],
  },
  {
    key: 'resources',
    label: 'Resources',
    groups: [
      {
        items: [
          {
            icon: Newspaper,
            title: 'Blog',
            desc: 'AEO guides, engine notes, and audit teardowns.',
            href: '/blog',
          },
          {
            icon: CircleHelp,
            title: 'FAQ',
            desc: 'Straight answers on scoring, keys, and data.',
            href: '/faq',
          },
          {
            icon: Scale,
            title: 'Compare',
            desc: 'Searchify vs Profound, Otterly, Scrunch, Peec.',
            href: '/compare',
          },
        ],
      },
    ],
  },
  {
    key: 'solutions',
    label: 'Solutions',
    groups: [
      {
        items: [
          {
            icon: Users,
            title: 'Agencies',
            desc: 'Audits across every client workspace.',
            href: '/solutions#agencies',
          },
          {
            icon: Building2,
            title: 'In-house teams',
            desc: 'AI answers beside your classic rankings.',
            href: '/solutions#in-house',
          },
          {
            icon: Rocket,
            title: 'Founders',
            desc: 'See whether AI engines recommend you.',
            href: '/solutions#founders',
          },
          {
            icon: Megaphone,
            title: 'PR & comms',
            desc: 'Check what engines say after a campaign.',
            href: '/solutions#pr',
          },
        ],
      },
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

/** True for items that leave the site (plain <a target="_blank">). */
function isExternal(item: NavDropItem): boolean {
  return 'external' in item && item.external === true;
}

/** Desktop dropdown panel row — internal rows use Link, external a plain <a>. */
function DropItemLink({ item, onSelect }: { item: NavDropItem; onSelect: () => void }) {
  const body = (
    <>
      <DropGlyph item={item} />
      <span className="d-text">
        <b>{item.title}</b>
        <small>{item.desc}</small>
      </span>
    </>
  );
  if (isExternal(item)) {
    return (
      <a
        className="drop-item"
        href={item.href}
        role="menuitem"
        target="_blank"
        rel="noreferrer"
        onClick={onSelect}
      >
        {body}
        <ArrowUpRight className="d-ext" aria-hidden />
      </a>
    );
  }
  return (
    <Link className="drop-item" href={item.href} role="menuitem" onClick={onSelect}>
      {body}
    </Link>
  );
}

/** Mobile accordion row — same internal/external split as the desktop rows. */
function MobileItemLink({ item, onSelect }: { item: NavDropItem; onSelect: () => void }) {
  const body = (
    <>
      <DropGlyph item={item} />
      {item.title}
    </>
  );
  if (isExternal(item)) {
    return (
      <a className="m-item" href={item.href} target="_blank" rel="noreferrer" onClick={onSelect}>
        {body}
        <ArrowUpRight className="d-ext" aria-hidden />
      </a>
    );
  }
  return (
    <Link className="m-item" href={item.href} onClick={onSelect}>
      {body}
    </Link>
  );
}

/**
 * LandingNav — sticky glass nav for the public marketing site.
 *
 * Desktop: "Product" / "Resources" / "Solutions" open hover-intent dropdown
 * panels (open on hover AND keyboard focus; trigger click only ever opens —
 * never closes a hover-open panel — and item click + Esc close; chevron
 * rotates, an invisible bridge covers the trigger→panel gap so the pointer
 * never loses hover). "Enterprise" / "Pricing" are plain links with no
 * dropdown chrome. ≤860px: a hamburger opens a slide-down menu with
 * tap-to-expand accordions (Esc closes it too). The bar's backdrop
 * intensifies once the page scrolls. Open state is driven from React
 * (`.open`) so `aria-expanded` stays truthful; class lists go through `cn()`
 * so prettier can't mangle conditional tokens; all transitions live in
 * marketing.css, gated behind prefers-reduced-motion. Anchors are absolute
 * (`/#features`, `/#how-it-works`) so they resolve from any subpage.
 */
export function LandingNav() {
  const theme = useSyncExternalStore(subscribeTheme, readTheme, () => 'light');
  const [scrolled, setScrolled] = useState(false);
  const [openDrop, setOpenDrop] = useState<DropKey | null>(null);
  const [slideDirection, setSlideDirection] = useState<'left' | 'right'>('right');
  const [dropLeft, setDropLeft] = useState(0);
  const navLinksRef = useRef<HTMLDivElement | null>(null);
  const triggerRefs = useRef<Partial<Record<DropKey, HTMLButtonElement>>>({});
  const closeTimer = useRef<number | null>(null);
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

  const clearDropClose = () => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
    closeTimer.current = null;
  };

  const openDesktopDrop = (key: DropKey) => {
    clearDropClose();
    const order: DropKey[] = ['product', 'resources', 'solutions'];
    if (openDrop && openDrop !== key) {
      setSlideDirection(order.indexOf(key) > order.indexOf(openDrop) ? 'right' : 'left');
    }
    const trigger = triggerRefs.current[key];
    const navLinks = navLinksRef.current;
    if (trigger && navLinks) {
      const triggerRect = trigger.getBoundingClientRect();
      const navRect = navLinks.getBoundingClientRect();
      setDropLeft(triggerRect.left - navRect.left + triggerRect.width / 2);
    }
    setOpenDrop(key);
  };

  const scheduleDropClose = () => {
    clearDropClose();
    closeTimer.current = window.setTimeout(() => setOpenDrop(null), 140);
  };

  useEffect(() => () => clearDropClose(), []);

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
    onMouseEnter: () => openDesktopDrop(key),
    onFocusCapture: () => openDesktopDrop(key),
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
  const activeDrop = NAV_DROPS.find((drop) => drop.key === openDrop);

  return (
    <div className="nav-wrap">
      <nav className={cn('site-nav rim', scrolled && 'scrolled')} aria-label="Main navigation">
        <Link className="wordmark" href="/" aria-label="Searchify home">
          <LogoCube size={28} />
          <span>Searchify</span>
          <span className="by-tag">by CUBE27</span>
        </Link>
        <div
          className="nav-links"
          ref={navLinksRef}
          onMouseEnter={clearDropClose}
          onMouseLeave={scheduleDropClose}
        >
          {NAV_DROPS.map(({ key, label }) => (
            <div
              className={cn('nav-item', openDrop === key && 'open')}
              key={key}
              {...dropProps(key)}
            >
              <button
                ref={(node) => {
                  if (node) triggerRefs.current[key] = node;
                }}
                className="nav-link"
                type="button"
                aria-expanded={openDrop === key}
                aria-haspopup="true"
                aria-controls="desktop-nav-panel"
                onClick={() => openDesktopDrop(key)}
              >
                {label} <ChevronDown className="chev" aria-hidden />
              </button>
            </div>
          ))}
          <Link className="nav-link" href="/enterprise">
            Enterprise
          </Link>
          <Link className="nav-link" href="/pricing">
            Pricing
          </Link>
          <div
            className={cn(
              'drop shared-drop',
              openDrop && 'open',
              openDrop && `drop-${openDrop}`,
              `slide-${slideDirection}`,
            )}
            id="desktop-nav-panel"
            role="menu"
            aria-hidden={!openDrop}
            style={{ left: dropLeft }}
            onMouseEnter={clearDropClose}
          >
            {activeDrop ? (
              <div className="drop-content" key={activeDrop.key}>
                {activeDrop.groups.map((group) =>
                  group.label ? (
                    <div className="d-group" key={group.label}>
                      <span className="d-group-label">{group.label}</span>
                      <div className="d-steps">
                        {group.items.map((item) => (
                          <DropItemLink
                            key={item.title}
                            item={item}
                            onSelect={() => setOpenDrop(null)}
                          />
                        ))}
                      </div>
                    </div>
                  ) : (
                    group.items.map((item) => (
                      <DropItemLink
                        key={item.title}
                        item={item}
                        onSelect={() => setOpenDrop(null)}
                      />
                    ))
                  ),
                )}
              </div>
            ) : null}
          </div>
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
        {NAV_DROPS.map(({ key, label, groups }) => (
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
              {groups.map((group) => (
                <Fragment key={group.label ?? 'items'}>
                  {group.label ? <div className="m-label">{group.label}</div> : null}
                  {group.items.map((item) => (
                    <MobileItemLink key={item.title} item={item} onSelect={closeMobile} />
                  ))}
                </Fragment>
              ))}
            </div>
          </div>
        ))}
        <Link className="m-plain" href="/enterprise" onClick={closeMobile}>
          Enterprise
        </Link>
        <Link className="m-plain" href="/pricing" onClick={closeMobile}>
          Pricing
        </Link>
        <div className="m-sep" />
        <Link className="m-plain" href="/login" onClick={closeMobile}>
          Sign in
        </Link>
      </div>
    </div>
  );
}
