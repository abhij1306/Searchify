import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({
  usePathname: () => '/visibility',
}));

import { SidebarNav } from './sidebar-nav';
import { NAV_GROUPS } from './nav-items';

function renderNav() {
  return render(<SidebarNav />);
}

describe('SidebarNav', () => {
  it('renders both groups', () => {
    renderNav();
    for (const group of ['Analyze', 'Optimize']) {
      expect(screen.getByText(group)).toBeInTheDocument();
    }
  });

  it('renders items as navigable links', () => {
    renderNav();
    const visibility = screen.getByRole('link', { name: /visibility/i });
    expect(visibility).toHaveAttribute('href', '/visibility');
    // Prompts is the single prompts surface (read view + in-page manage mode).
    const prompts = screen.getByRole('link', { name: /prompts/i });
    expect(prompts).toHaveAttribute('href', '/prompts');
  });

  it('highlights the active route', () => {
    renderNav();
    const visibility = screen.getByRole('link', { name: /visibility/i });
    expect(visibility).toHaveAttribute('aria-current', 'page');

    // A different route is not marked active.
    const runs = screen.getByRole('link', { name: /runs/i });
    expect(runs).not.toHaveAttribute('aria-current');
  });

  it('renders every item as a link — no disabled state or "soon" badge', () => {
    renderNav();
    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(8);
    for (const link of links) {
      expect(link).not.toHaveAttribute('aria-disabled');
    }
    expect(screen.queryByText(/soon/i)).not.toBeInTheDocument();
  });

  it('renders exactly the nav model as links', () => {
    renderNav();
    expect(NAV_GROUPS).toHaveLength(2);
    const labels = NAV_GROUPS.flatMap((group) => group.items.map((item) => item.label));
    expect(labels).toEqual([
      'Visibility',
      'Prompts',
      'Runs',
      'Content',
      'Site Health',
      'Issues',
      'Knowledge Base',
      'Setup',
    ]);
    for (const label of labels) {
      expect(screen.getByRole('link', { name: new RegExp(label, 'i') })).toBeInTheDocument();
    }
  });
});
