import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { TooltipProvider } from '@/components/ui/tooltip';

// Stub next/navigation (Link uses it in jsdom).
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/visibility',
}));

const clearSession = vi.fn();
vi.mock('@/lib/auth/session-guard', () => ({
  useSession: () => ({
    user: {
      id: '00000000-0000-4000-8000-000000000001',
      email: 'test.user@example.test',
      role: 'user',
      is_active: true,
      created_at: '2026-01-03T00:00:00Z',
      updated_at: '2026-07-14T09:22:00Z',
    },
    clearSession,
  }),
}));

vi.mock('@/lib/api/auth', () => ({
  authApi: { logout: vi.fn().mockResolvedValue(undefined) },
}));

import { QueryClientProvider } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';

import { createAppQueryClient } from '@/lib/api/query-client';

import { UserMenu } from './user-menu';

function renderMenu() {
  return render(
    <QueryClientProvider client={createAppQueryClient()}>
      <TooltipProvider>
        <UserMenu />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe('UserMenu', () => {
  it('shows a Settings item directly above Sign out, linking to /settings', async () => {
    const user = userEvent.setup();
    renderMenu();

    await user.click(screen.getByRole('button', { name: /test\.user@example\.test/i }));

    const menu = await screen.findByRole('menu');
    const items = within(menu).getAllByRole('menuitem');
    const labels = items.map((item) => item.textContent ?? '');

    const settingsIndex = labels.findIndex((label) => /settings/i.test(label));
    const signOutIndex = labels.findIndex((label) => /sign out/i.test(label));

    expect(settingsIndex).toBeGreaterThanOrEqual(0);
    expect(signOutIndex).toBe(settingsIndex + 1);

    // asChild renders the menuitem as the Link anchor itself.
    expect(items[settingsIndex]).toHaveAttribute('href', '/settings');
  });
});
