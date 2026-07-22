import type { LucideIcon } from 'lucide-react';

import { ICONS } from '@/lib/icons';

/**
 * Sidebar navigation model (F5, simplified): two groups — Analyze / Optimize —
 * with eight live items, all navigable. Icons come from the canonical map
 * (`@/lib/icons`) so nav glyphs stay consistent with the rest of the app.
 * This is data-only so the sidebar component stays presentational and the nav
 * is unit-testable.
 */
export type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
};

export type NavGroup = {
  title: string;
  items: NavItem[];
};

export const NAV_GROUPS: NavGroup[] = [
  {
    title: 'Analyze',
    items: [
      { label: 'Visibility', href: '/visibility', icon: ICONS.visibility },
      // Single prompts surface: read view by default, manage mode in-page.
      { label: 'Prompts', href: '/prompts', icon: ICONS.prompts },
      { label: 'Runs', href: '/runs', icon: ICONS.runs },
    ],
  },
  {
    title: 'Optimize',
    items: [
      { label: 'Content', href: '/content', icon: ICONS.content },
      { label: 'Site Health', href: '/site-health', icon: ICONS.siteHealth },
      { label: 'Issues', href: '/issues', icon: ICONS.issues },
      { label: 'Knowledge Base', href: '/knowledge-base', icon: ICONS.knowledgeBase },
      { label: 'Setup', href: '/setup', icon: ICONS.setup },
    ],
  },
];
