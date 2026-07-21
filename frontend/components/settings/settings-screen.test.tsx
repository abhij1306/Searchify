import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createAppQueryClient } from '@/lib/api/query-client';
import type { Project, SessionUser } from '@/lib/api/types';

// Stub next/navigation (Link uses it in jsdom). `search` is mutable per test
// so the ?tab= deep link can be exercised.
let search = '';
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/settings',
  useSearchParams: () => new URLSearchParams(search),
}));

// Session is mocked per test so the screen renders without a real SessionGuard.
const user: SessionUser = {
  id: '00000000-0000-4000-8000-000000000001',
  email: 'test.user@example.test',
  role: 'user',
  is_active: true,
  created_at: '2026-01-03T00:00:00Z',
  updated_at: '2026-07-14T09:22:00Z',
};
vi.mock('@/lib/auth/session-guard', () => ({
  useSessionUser: () => user,
}));

// Active project context — the danger zone deletes the active project.
const activeProject = {
  id: '00000000-0000-4000-8000-0000000000p1',
  workspace_id: '00000000-0000-4000-8000-0000000000w1',
  name: 'Acme Storage',
  brand_name: 'Acme',
} as unknown as Project;
const setActiveProjectId = vi.fn();
vi.mock('@/lib/project/project-context', () => ({
  useProjectContext: () => ({
    projects: [activeProject],
    activeProject,
    activeProjectId: activeProject.id,
    setActiveProjectId,
    isLoading: false,
  }),
}));

const deleteProject = vi.fn().mockResolvedValue(undefined);
vi.mock('@/lib/api/projects', () => ({
  projectsApi: { deleteProject: (id: string) => deleteProject(id) },
}));

// The Provider Settings tab fetches the catalog/connections; stub the panel so
// this suite stays focused on the tab shell (the panel has its own suite in
// provider-settings.test.tsx).
vi.mock('@/components/settings/provider-settings', () => ({
  ProviderSettings: () => <div data-testid="provider-settings-panel">provider settings</div>,
}));

import { SettingsScreen } from './settings-screen';

function renderScreen() {
  return render(
    <QueryClientProvider client={createAppQueryClient()}>
      <SettingsScreen />
    </QueryClientProvider>,
  );
}

describe('SettingsScreen', () => {
  beforeEach(() => {
    deleteProject.mockClear();
    setActiveProjectId.mockClear();
    search = '';
  });

  it('renders the three settings tabs with Account selected by default', () => {
    renderScreen();
    const tablist = screen.getByRole('tablist', { name: /settings sections/i });
    const tabs = within(tablist).getAllByRole('tab');
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      'Account',
      'Provider Settings',
      'Danger Zone',
    ]);
    expect(within(tablist).getByRole('tab', { name: 'Account' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('renders the session email, account role, and initials avatar', () => {
    renderScreen();

    // Email appears in the summary and the detail row.
    expect(screen.getAllByText('test.user@example.test').length).toBeGreaterThanOrEqual(1);
    // Account role label (not "workspace owner") + the free-form role value.
    expect(screen.getByText('Account role')).toBeInTheDocument();
    expect(screen.getAllByText('user').length).toBeGreaterThanOrEqual(1);
    // Initials avatar from the email local part.
    expect(screen.getByText('TE')).toBeInTheDocument();
  });

  it('labels the created timestamp as "Account created", not "Member since"', () => {
    renderScreen();
    expect(screen.getByText('Account created')).toBeInTheDocument();
    expect(screen.queryByText(/member since/i)).not.toBeInTheDocument();
  });

  it('shows the user id and last-updated timestamp when present', () => {
    renderScreen();
    expect(screen.getByText('User ID')).toBeInTheDocument();
    expect(screen.getByText(user.id)).toBeInTheDocument();
    expect(screen.getByText('Last updated')).toBeInTheDocument();
  });

  it('renders a theme toggle on the Account tab', () => {
    renderScreen();
    expect(screen.getByRole('button', { name: /toggle color theme/i })).toBeInTheDocument();
  });

  it('shows the provider settings panel on the Provider Settings tab', async () => {
    const ue = userEvent.setup();
    renderScreen();

    // Panels stay mounted for stable aria-controls targets; inactive ones are hidden.
    expect(screen.getByTestId('provider-settings-panel')).not.toBeVisible();
    await ue.click(screen.getByRole('tab', { name: 'Provider Settings' }));
    expect(screen.getByTestId('provider-settings-panel')).toBeVisible();
    // Account content is hidden while another tab is active.
    expect(screen.getByText('Account role')).not.toBeVisible();
  });

  it('opens the Provider Settings tab from a ?tab=providers deep link', () => {
    search = 'tab=providers';
    renderScreen();
    expect(screen.getByRole('tab', { name: 'Provider Settings' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByTestId('provider-settings-panel')).toBeVisible();
  });

  it('falls back to Account on an unknown ?tab value', () => {
    search = 'tab=nonsense';
    renderScreen();
    expect(screen.getByRole('tab', { name: 'Account' })).toHaveAttribute('aria-selected', 'true');
  });

  it('supports arrow-key navigation across tabs', async () => {
    const ue = userEvent.setup();
    renderScreen();

    const account = screen.getByRole('tab', { name: 'Account' });
    account.focus();
    await ue.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Provider Settings' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await ue.keyboard('{End}');
    expect(screen.getByRole('tab', { name: 'Danger Zone' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('shows the danger zone with the active project name on the Danger Zone tab', async () => {
    const ue = userEvent.setup();
    renderScreen();

    await ue.click(screen.getByRole('tab', { name: 'Danger Zone' }));
    expect(screen.getByText('Danger zone')).toBeInTheDocument();
    expect(screen.getByText('Acme Storage')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /delete project/i })).toBeInTheDocument();
  });

  it('deletes the active project after confirming in the dialog', async () => {
    const ue = userEvent.setup();
    renderScreen();

    await ue.click(screen.getByRole('tab', { name: 'Danger Zone' }));
    await ue.click(screen.getByRole('button', { name: /delete project/i }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/cannot be undone/i)).toBeInTheDocument();

    await ue.click(within(dialog).getByRole('button', { name: /delete project/i }));

    expect(deleteProject).toHaveBeenCalledWith(activeProject.id);
  });

  it('does not delete when the dialog is cancelled', async () => {
    const ue = userEvent.setup();
    renderScreen();

    await ue.click(screen.getByRole('tab', { name: 'Danger Zone' }));
    await ue.click(screen.getByRole('button', { name: /delete project/i }));
    const dialog = await screen.findByRole('dialog');
    await ue.click(within(dialog).getByRole('button', { name: /cancel/i }));

    expect(deleteProject).not.toHaveBeenCalled();
  });
});
