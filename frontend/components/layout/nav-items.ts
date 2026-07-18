import {
  BarChart3,
  FileText,
  Gauge,
  Layers,
  Lightbulb,
  ListChecks,
  type LucideIcon,
  MessageSquareText,
  PlugZap,
  Route,
  Settings,
  ShieldCheck,
  Sparkles,
  TrendingUp,
} from 'lucide-react';

/**
 * Sidebar navigation model (F5). Grouped Analytics / Prompts / Actions / On Page
 * (docs/design.md §9.2). Only MVP-live items are navigable; everything else is
 * rendered but disabled with a "soon" affordance. This is data-only so the
 * sidebar component stays presentational and the nav is unit-testable.
 */
export type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
  /** Live at MVP → navigable; otherwise rendered disabled with a "soon" badge. */
  live: boolean;
};

export type NavGroup = {
  title: string;
  items: NavItem[];
};

export const NAV_GROUPS: NavGroup[] = [
  {
    title: 'Analytics',
    items: [
      { label: 'Visibility', href: '/visibility', icon: Gauge, live: true },
      { label: 'LLM Analytics', href: '/analytics', icon: BarChart3, live: false },
      { label: 'Traffic', href: '/traffic', icon: TrendingUp, live: false },
    ],
  },
  {
    title: 'Prompts',
    items: [
      { label: 'Your Prompts', href: '/prompts', icon: MessageSquareText, live: true },
      { label: 'Prompt Research', href: '/topics', icon: Sparkles, live: false },
    ],
  },
  {
    title: 'Actions',
    items: [
      { label: 'Runs', href: '/runs', icon: ListChecks, live: true },
      { label: 'Providers', href: '/providers', icon: PlugZap, live: true },
      { label: 'Content', href: '/content', icon: FileText, live: false },
      { label: 'Opportunities', href: '/opportunities', icon: Lightbulb, live: false },
    ],
  },
  {
    title: 'On Page',
    items: [
      { label: 'Setup', href: '/setup', icon: Settings, live: true },
      { label: 'Site Health', href: '/site-health', icon: ShieldCheck, live: true },
      { label: 'Issues', href: '/issues', icon: Route, live: true },
      { label: 'Knowledge Base', href: '/writing', icon: Layers, live: false },
    ],
  },
];
