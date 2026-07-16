import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { TooltipProvider } from '@/components/ui/tooltip';

vi.mock('next/navigation', () => ({
  usePathname: () => '/visibility',
}));

import { SidebarNav } from './sidebar-nav';
import { NAV_GROUPS } from './nav-items';

function renderNav() {
  return render(
    <TooltipProvider>
      <SidebarNav />
    </TooltipProvider>,
  );
}

describe('SidebarNav', () => {
  it('renders all four groups', () => {
    renderNav();
    for (const group of ['Analytics', 'Prompts', 'Actions', 'On Page']) {
      expect(screen.getByText(group)).toBeInTheDocument();
    }
  });

  it('renders live items as navigable links', () => {
    renderNav();
    // Visibility is live → a link pointing at /visibility.
    const visibility = screen.getByRole('link', { name: /visibility/i });
    expect(visibility).toHaveAttribute('href', '/visibility');
  });

  it('highlights the active route', () => {
    renderNav();
    const visibility = screen.getByRole('link', { name: /visibility/i });
    expect(visibility).toHaveAttribute('aria-current', 'page');

    // A different live route is not marked active.
    const prompts = screen.getByRole('link', { name: /your prompts/i });
    expect(prompts).not.toHaveAttribute('aria-current');
  });

  it('renders disabled items as non-navigable with a "soon" affordance', () => {
    renderNav();
    // LLM Analytics is a roadmap item — no link, and shows "soon".
    expect(screen.queryByRole('link', { name: /llm analytics/i })).not.toBeInTheDocument();

    const disabled = screen.getByText('LLM Analytics').closest('[aria-disabled="true"]');
    expect(disabled).not.toBeNull();
    expect(disabled).toHaveTextContent(/soon/i);
  });

  it('renders exactly the five MVP-live items as links', () => {
    renderNav();
    const liveLabels = NAV_GROUPS.flatMap((group) =>
      group.items.filter((item) => item.live).map((item) => item.label),
    );
    expect(liveLabels).toEqual([
      'Visibility',
      'Your Prompts',
      'Runs',
      'Providers',
      'Setup',
    ]);
    for (const label of liveLabels) {
      expect(screen.getByRole('link', { name: new RegExp(label, 'i') })).toBeInTheDocument();
    }
  });
});
