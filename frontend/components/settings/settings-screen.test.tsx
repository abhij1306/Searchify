import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { SessionUser } from '@/lib/api/types';

// Stub next/navigation (Link uses it in jsdom).
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/settings',
}));

// Session is mocked per test so the screen renders without a real SessionGuard.
const user: SessionUser = {
  id: '2f9a1c04-77b3-4e19-8a6d-c1e0b4d92f10',
  email: 'abhineet.jain@cube27.com',
  role: 'user',
  is_active: true,
  created_at: '2026-01-03T00:00:00Z',
  updated_at: '2026-07-14T09:22:00Z',
};
vi.mock('@/lib/auth/session-guard', () => ({
  useSessionUser: () => user,
}));

import { SettingsScreen } from './settings-screen';

describe('SettingsScreen', () => {
  it('renders the session email, account role, and initials avatar', () => {
    render(<SettingsScreen />);

    // Email appears in the summary and the detail row.
    expect(screen.getAllByText('abhineet.jain@cube27.com').length).toBeGreaterThanOrEqual(1);
    // Account role label (not "workspace owner") + the free-form role value.
    expect(screen.getByText('Account role')).toBeInTheDocument();
    expect(screen.getAllByText('user').length).toBeGreaterThanOrEqual(1);
    // Initials avatar from the email local part.
    expect(screen.getByText('AB')).toBeInTheDocument();
  });

  it('labels the created timestamp as "Account created", not "Member since"', () => {
    render(<SettingsScreen />);
    expect(screen.getByText('Account created')).toBeInTheDocument();
    expect(screen.queryByText(/member since/i)).not.toBeInTheDocument();
  });

  it('shows the user id and last-updated timestamp when present', () => {
    render(<SettingsScreen />);
    expect(screen.getByText('User ID')).toBeInTheDocument();
    expect(screen.getByText(user.id)).toBeInTheDocument();
    expect(screen.getByText('Last updated')).toBeInTheDocument();
  });

  it('renders a theme toggle', () => {
    render(<SettingsScreen />);
    expect(screen.getByRole('button', { name: /toggle color theme/i })).toBeInTheDocument();
  });

  it('renders a two-column sub-navigation (Account, Appearance, Model providers, Project setup)', () => {
    render(<SettingsScreen />);
    const subnav = screen.getByRole('navigation', { name: /settings/i });
    // In-page anchors for the two account sections.
    expect(within(subnav).getByRole('link', { name: /^account$/i })).toHaveAttribute(
      'href',
      '#account',
    );
    expect(within(subnav).getByRole('link', { name: /^appearance$/i })).toHaveAttribute(
      'href',
      '#appearance',
    );
    // Configuration group links out to the existing screens.
    expect(within(subnav).getByRole('link', { name: /model providers/i })).toHaveAttribute(
      'href',
      '/providers',
    );
    expect(within(subnav).getByRole('link', { name: /project setup/i })).toHaveAttribute(
      'href',
      '/setup',
    );
  });

  it('surfaces configuration as links only (no fabricated provider status)', () => {
    render(<SettingsScreen />);
    // Links-only actions to the real screens.
    expect(screen.getByRole('link', { name: /open provider settings/i })).toHaveAttribute(
      'href',
      '/providers',
    );
    expect(screen.getByRole('link', { name: /open project setup/i })).toHaveAttribute(
      'href',
      '/setup',
    );
    // No fabricated per-provider configured/not-configured status.
    expect(screen.queryByText(/not configured/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/chatgpt|gemini|claude/i)).not.toBeInTheDocument();
  });
});
