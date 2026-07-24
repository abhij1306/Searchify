import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const { pathname } = vi.hoisted(() => ({ pathname: { value: '/visibility' } }));

vi.mock('next/navigation', () => ({
  usePathname: () => pathname.value,
}));

import { TooltipProvider } from '@/components/ui/tooltip';

import { TopBar } from './top-bar';

function renderTitle(route: string) {
  pathname.value = route;
  render(
    <TooltipProvider>
      <TopBar />
    </TooltipProvider>,
  );
  return screen.getByRole('heading', { level: 1 }).textContent;
}

describe('TopBar', () => {
  it.each([
    ['/visibility', 'Visibility'],
    ['/analytics', 'LLM Analytics'],
    ['/traffic', 'Traffic'],
    ['/prompts', 'Prompts'],
  ])('resolves %s to the page title %s', (route, title) => {
    expect(renderTitle(route)).toBe(title);
  });

  it.each([
    ['/traffic/anything', 'Traffic'],
    ['/runs/abc', 'Run Detail'],
  ])('resolves deeper route %s by longest-prefix match to %s', (route, title) => {
    expect(renderTitle(route)).toBe(title);
  });

  it('falls back to the product name for unknown routes', () => {
    expect(renderTitle('/nope')).toBe('Searchify');
  });
});
