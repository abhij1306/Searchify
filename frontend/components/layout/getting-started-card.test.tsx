import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Project } from '@/lib/api/types';
import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

// The card only reads the active project from context; queries go through MSW.
let activeProject: Project | null = null;
vi.mock('@/lib/project/project-context', () => ({
  useProjectContext: () => ({
    projects: activeProject ? [activeProject] : [],
    activeProject,
    activeProjectId: activeProject?.id ?? null,
    setActiveProjectId: vi.fn(),
    isLoading: false,
  }),
}));

import { GettingStartedCard } from './getting-started-card';

const project = {
  id: '11111111-1111-4111-8111-111111111111',
  workspace_id: '22222222-2222-4222-8222-222222222222',
  name: 'Searchify',
  brand_name: 'Searchify',
  prompt_sets: [
    {
      id: '33333333-3333-4333-8333-333333333333',
      name: 'Default',
      prompts: [{ id: '44444444-4444-4444-8444-444444444444', text: 'best seo tool' }],
    },
  ],
} as unknown as Project;

const connection = {
  id: '55555555-5555-4555-8555-555555555555',
  workspace_id: project.workspace_id,
  transport_provider: 'anthropic',
  base_url: null,
  active: true,
  api_key_set: true,
  created_at: '2026-07-18T08:00:00Z',
  updated_at: '2026-07-18T08:00:00Z',
};

// Served to the connect dialog that step 3 opens (v2 direct routes).
const catalog = {
  transports: ['openai', 'anthropic', 'google'],
  engines: [
    {
      logical_engine: 'chatgpt',
      routes: [{ transport_provider: 'openai', default_model: 'gpt-5.4' }],
    },
    {
      logical_engine: 'gemini',
      routes: [{ transport_provider: 'google', default_model: 'gemini-flash-latest' }],
    },
    {
      logical_engine: 'claude',
      routes: [{ transport_provider: 'anthropic', default_model: 'claude-sonnet-4-6' }],
    },
  ],
};

const audit = {
  id: '66666666-6666-4666-8666-666666666666',
  workspace_id: project.workspace_id,
  project_id: project.id,
  status: 'completed',
  benchmark_mode: 'natural',
  repetitions: 1,
  random_seed: '42',
  requested_count: 1,
  completed_count: 1,
  failed_count: 0,
  error_message: '',
  engine_snapshots: [],
  created_at: '2026-07-19T10:00:00Z',
  updated_at: '2026-07-19T10:05:00Z',
  started_at: '2026-07-19T10:00:10Z',
  completed_at: '2026-07-19T10:05:00Z',
};

/** Register both card queries; override per test with `mswServer.use`. */
function handlers({ connections = [] as unknown[], audits = [] as unknown[] } = {}) {
  mswServer.use(
    http.get('/api/v1/provider-connections', () => HttpResponse.json(connections)),
    http.get('/api/v1/audits', () => HttpResponse.json(audits)),
  );
}

async function findProgress(label: RegExp) {
  return waitFor(() => expect(screen.getByRole('progressbar')).toHaveAccessibleName(label));
}

describe('GettingStartedCard', () => {
  beforeEach(() => {
    activeProject = null;
  });

  it('shows 0 of 5 with no project and never fetches', () => {
    // No handlers registered: an unexpected fetch would fail the suite
    // (onUnhandledRequest: 'error').
    renderWithProviders(<GettingStartedCard />);
    expect(screen.getByRole('progressbar')).toHaveAccessibleName('0 of 5 steps complete');
    const next = screen.getByRole('link', { name: /next: set up your project/i });
    expect(next).toHaveAttribute('href', '/setup');
  });

  it('links the prompts step to the prompts page when no prompts exist', async () => {
    activeProject = { ...project, prompt_sets: [] } as unknown as Project;
    handlers({ connections: [connection] });

    renderWithProviders(<GettingStartedCard />);

    await findProgress(/1 of 5 steps complete/);
    const next = screen.getByRole('link', { name: /next: add prompts/i });
    expect(next).toHaveAttribute('href', '/prompts');
  });

  it('counts a configured provider connection as step 3 done', async () => {
    activeProject = project;
    handlers({ connections: [connection] });

    renderWithProviders(<GettingStartedCard />);

    // project setup + prompts + provider = 3; next step is the first run.
    await findProgress(/3 of 5 steps complete/);
    const next = screen.getByRole('link', { name: /next: launch your first run/i });
    expect(next).toHaveAttribute('href', '/runs');
  });

  it('ignores inactive or keyless connections and opens the connect dialog from step 3', async () => {
    const user = userEvent.setup();
    activeProject = project;
    handlers({
      connections: [
        { ...connection, active: false },
        { ...connection, id: '55555555-5555-4555-8555-555555555556', api_key_set: false },
      ],
    });
    mswServer.use(http.get('/api/v1/provider-catalog', () => HttpResponse.json(catalog)));

    renderWithProviders(<GettingStartedCard />);

    await findProgress(/2 of 5 steps complete/);
    // The step is a button that opens the guided connect dialog inline (Task
    // 3.2) instead of linking away to Settings. Full 3-engine management
    // stays reachable as a secondary affordance (asserted before opening —
    // the modal dialog aria-hides the card behind it).
    expect(screen.getByRole('link', { name: /manage all providers in settings/i })).toHaveAttribute(
      'href',
      '/settings?tab=providers',
    );

    const next = screen.getByRole('button', { name: /next: connect a provider/i });
    await user.click(next);
    expect(await screen.findByRole('dialog', { name: 'Connect a provider' })).toBeInTheDocument();
  });

  it('marks all five steps done once a run has completed', async () => {
    activeProject = project;
    handlers({ connections: [connection], audits: [audit] });

    renderWithProviders(<GettingStartedCard />);

    await findProgress(/5 of 5 steps complete/);
    expect(screen.getByText(/all set — you're ready to run/i)).toBeInTheDocument();
  });

  it('counts a launched-but-unfinished run as step 4 only', async () => {
    activeProject = project;
    handlers({
      connections: [connection],
      audits: [{ ...audit, status: 'running', completed_count: 0, completed_at: null }],
    });

    renderWithProviders(<GettingStartedCard />);

    await findProgress(/4 of 5 steps complete/);
    expect(screen.getByRole('link', { name: /next: review visibility/i })).toHaveAttribute(
      'href',
      '/visibility',
    );
  });
});
