import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import RunsPage from './page';

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const WORKSPACE_ID = '22222222-2222-4222-8222-222222222222';
const SET_ID = '33333333-3333-4333-8333-333333333333';
const AUDIT_ID = '44444444-4444-4444-8444-444444444444';

const pushMock = vi.fn();

// Route the F5 project context to a fixed active project, and stub the router.
vi.mock('@/lib/project/project-context', () => ({
  useActiveProject: () => ({ id: PROJECT_ID, workspace_id: WORKSPACE_ID }),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock }),
}));

function audit(overrides: Record<string, unknown> = {}) {
  return {
    id: AUDIT_ID,
    workspace_id: WORKSPACE_ID,
    project_id: PROJECT_ID,
    status: 'running',
    benchmark_mode: 'consumer_like',
    repetitions: 3,
    random_seed: '7',
    requested_count: 6,
    completed_count: 2,
    failed_count: 0,
    error_message: '',
    engine_snapshots: [],
    created_at: '2026-07-15T00:00:00Z',
    updated_at: '2026-07-15T00:00:00Z',
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

const promptSet = {
  id: SET_ID,
  project_id: PROJECT_ID,
  name: 'Default prompt set',
  description: '',
  prompt_count: 4,
  prompts: [],
  created_at: '2026-07-15T00:00:00Z',
  updated_at: '2026-07-15T00:00:00Z',
};

function connection(routes: string[]) {
  return {
    id: '55555555-5555-4555-8555-555555555555',
    workspace_id: WORKSPACE_ID,
    label: 'byok',
    transport_provider: 'google',
    base_url: null,
    active: true,
    api_key_set: true,
    last_tested_at: null,
    last_test_status: '',
    routes: routes.map((engine, i) => ({
      id: `66666666-6666-4666-8666-66666666666${i}`,
      logical_engine: engine,
      transport_provider: 'google',
      transport_model: 'gemini-flash-latest',
      is_default: false,
    })),
    created_at: '2026-07-15T00:00:00Z',
    updated_at: '2026-07-15T00:00:00Z',
  };
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => pushMock.mockReset());
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('RunsPage', () => {
  it('renders the runs table with counts and status', async () => {
    mswServer.use(http.get('/api/v1/audits', () => HttpResponse.json([audit()])));

    renderWithProviders(<RunsPage />);

    // Scope to the table — the "Running" status filter chip carries the same
    // label as the run-status badge.
    const table = await screen.findByRole('table');
    expect(within(table).getByText('Running')).toBeInTheDocument();
    const row = within(table).getByText('Running').closest('tr')!;
    expect(within(row).getByText('6')).toBeInTheDocument();
    expect(within(row).getByRole('link', { name: 'View' })).toHaveAttribute(
      'href',
      `/runs/${AUDIT_ID}`,
    );
    // Mono page indicator on the pagination footer.
    expect(screen.getByText('1–1 of 1 runs')).toBeInTheDocument();
  });

  it('filters the runs list by status chip', async () => {
    const user = userEvent.setup();
    mswServer.use(
      http.get('/api/v1/audits', () =>
        HttpResponse.json([
          audit({ status: 'completed' }),
          audit({
            id: 'abababab-abab-4bab-8bab-abababababab',
            status: 'failed',
            failed_count: 6,
          }),
        ]),
      ),
    );

    renderWithProviders(<RunsPage />);

    const table = await screen.findByRole('table');
    // Both rows listed ("Completed"/"Failed" also name header cells + chips,
    // so assert on the row links).
    expect(within(table).getAllByRole('link', { name: 'View' })).toHaveLength(2);

    // The chips carry mono counts and toggle the visible rows.
    const chips = screen.getByRole('group', { name: 'Filter by status' });
    expect(within(chips).getByRole('button', { name: /all/i })).toHaveTextContent('2');
    await user.click(within(chips).getByRole('button', { name: /failed/i }));

    const links = within(table).getAllByRole('link', { name: 'View' });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute('href', '/runs/abababab-abab-4bab-8bab-abababababab');
    expect(screen.getByText('1–1 of 1 runs')).toBeInTheDocument();
  });

  it('shows the empty state when there are no runs', async () => {
    mswServer.use(http.get('/api/v1/audits', () => HttpResponse.json([])));

    renderWithProviders(<RunsPage />);

    expect(await screen.findByText('No runs yet')).toBeInTheDocument();
  });

  it('launches an audit from the dialog and routes to the new run', async () => {
    const user = userEvent.setup();
    let posted: Record<string, unknown> | null = null;
    mswServer.use(
      http.get('/api/v1/audits', () => HttpResponse.json([])),
      http.get('/api/v1/prompt-sets', () => HttpResponse.json([promptSet])),
      http.get('/api/v1/provider-connections', () =>
        HttpResponse.json([connection(['gemini', 'claude'])]),
      ),
      http.post('/api/v1/audits', async ({ request }) => {
        posted = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(audit({ status: 'queued' }), { status: 201 });
      }),
    );

    renderWithProviders(<RunsPage />);

    await user.click((await screen.findAllByRole('button', { name: /launch/i }))[0]);

    // The dialog loads the prompt set + configured engines.
    const geminiToggle = await screen.findByRole('checkbox', { name: 'Gemini' });
    await user.click(geminiToggle);
    expect(geminiToggle).toHaveAttribute('aria-checked', 'true');

    await user.click(screen.getByRole('button', { name: 'Launch audit' }));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toEqual({
      project_id: PROJECT_ID,
      prompt_set_id: SET_ID,
      engines: ['gemini'],
      repetitions: 3,
    });
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith(`/runs/${AUDIT_ID}`));
  });
});
