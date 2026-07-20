import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createAppQueryClient } from '@/lib/api/query-client';
import type { Project, SessionUser } from '@/lib/api/types';

// Stub next/navigation (Link uses it in jsdom).
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/settings',
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

  it('renders a theme toggle', () => {
    renderScreen();
    expect(screen.getByRole('button', { name: /toggle color theme/i })).toBeInTheDocument();
  });

  it('renders a two-column sub-navigation (Account, Appearance, Model providers, Project setup)', () => {
    renderScreen();
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
    // Danger-zone anchor.
    expect(within(subnav).getByRole('link', { name: /danger zone/i })).toHaveAttribute(
      'href',
      '#danger',
    );
  });

  it('surfaces configuration as links only (no fabricated provider status)', () => {
    renderScreen();
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

  it('shows the danger zone with the active project name', () => {
    renderScreen();
    // Appears in both the subnav anchor and the card title.
    expect(screen.getAllByText('Danger zone').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Acme Storage')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /delete project/i })).toBeInTheDocument();
  });

  it('deletes the active project after confirming in the dialog', async () => {
    const ue = userEvent.setup();
    renderScreen();

    await ue.click(screen.getByRole('button', { name: /delete project/i }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/cannot be undone/i)).toBeInTheDocument();

    await ue.click(within(dialog).getByRole('button', { name: /delete project/i }));

    expect(deleteProject).toHaveBeenCalledWith(activeProject.id);
  });

  it('does not delete when the dialog is cancelled', async () => {
    const ue = userEvent.setup();
    renderScreen();

    await ue.click(screen.getByRole('button', { name: /delete project/i }));
    const dialog = await screen.findByRole('dialog');
    await ue.click(within(dialog).getByRole('button', { name: /cancel/i }));

    expect(deleteProject).not.toHaveBeenCalled();
  });
});
