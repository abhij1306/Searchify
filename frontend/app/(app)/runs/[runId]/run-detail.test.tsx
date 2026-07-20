import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import RunDetailPage from './page';

const AUDIT_ID = '44444444-4444-4444-8444-444444444444';
const WORKSPACE_ID = '22222222-2222-4222-8222-222222222222';
const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const EXEC_ID = '77777777-7777-4777-8777-777777777777';

vi.mock('next/navigation', () => ({
  useParams: () => ({ runId: AUDIT_ID }),
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
    completed_count: 4,
    failed_count: 1,
    error_message: '',
    engine_snapshots: [],
    created_at: '2026-07-15T00:00:00Z',
    updated_at: '2026-07-15T00:00:00Z',
    started_at: '2026-07-15T00:00:05Z',
    completed_at: null,
    ...overrides,
  };
}

function execution(overrides: Record<string, unknown> = {}) {
  return {
    id: EXEC_ID,
    audit_id: AUDIT_ID,
    prompt_index: 0,
    repetition: 1,
    randomized_position: 0,
    logical_engine: 'gemini',
    transport_provider: 'google',
    transport_model: 'gemini-flash-latest',
    status: 'succeeded',
    attempt_count: 1,
    max_attempts: 5,
    answer_text: 'An answer',
    search_used: true,
    error_code: '',
    error_detail: '',
    latency_ms: 1200,
    created_at: '2026-07-15T00:00:00Z',
    completed_at: '2026-07-15T00:00:03Z',
    ...overrides,
  };
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('RunDetailPage', () => {
  it('renders the progress panel counts + status and the executions table', async () => {
    mswServer.use(
      http.get(`/api/v1/audits/${AUDIT_ID}`, () => HttpResponse.json(audit())),
      http.get(`/api/v1/audits/${AUDIT_ID}/executions`, () => HttpResponse.json([execution()])),
    );

    renderWithProviders(<RunDetailPage />);

    expect(await screen.findByText('Running')).toBeInTheDocument();
    // Counts.
    expect(screen.getByText('6')).toBeInTheDocument();
    expect(screen.getByText('4')).toBeInTheDocument();
    // Executions table row with the engine + an evidence link.
    const row = (await screen.findByText('Gemini')).closest('tr')!;
    expect(within(row).getByText('Succeeded')).toBeInTheDocument();
    expect(within(row).getByRole('link', { name: 'Evidence' })).toHaveAttribute(
      'href',
      `/runs/${AUDIT_ID}/executions/${EXEC_ID}`,
    );
  });

  it('cancels an active run via POST /audits/{id}/cancel', async () => {
    const user = userEvent.setup();
    let cancelled = false;
    mswServer.use(
      http.get(`/api/v1/audits/${AUDIT_ID}`, () =>
        HttpResponse.json(audit(cancelled ? { status: 'cancelled' } : {})),
      ),
      http.get(`/api/v1/audits/${AUDIT_ID}/executions`, () => HttpResponse.json([execution()])),
      http.post(`/api/v1/audits/${AUDIT_ID}/cancel`, () => {
        cancelled = true;
        return HttpResponse.json(
          audit({ status: 'cancelled', completed_at: '2026-07-15T00:05:00Z' }),
        );
      }),
    );

    renderWithProviders(<RunDetailPage />);

    const cancelButton = await screen.findByRole('button', { name: /cancel run/i });
    await user.click(cancelButton);

    // The run flips to cancelled and the cancel button becomes disabled (terminal).
    expect(await screen.findByText('Cancelled')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: /cancel run/i })).toBeDisabled());
  });

  it('exposes CSV/MD export links', async () => {
    mswServer.use(
      http.get(`/api/v1/audits/${AUDIT_ID}`, () =>
        HttpResponse.json(audit({ status: 'completed' })),
      ),
      http.get(`/api/v1/audits/${AUDIT_ID}/executions`, () => HttpResponse.json([execution()])),
    );

    renderWithProviders(<RunDetailPage />);

    const csv = await screen.findByRole('link', { name: /export csv/i });
    expect(csv).toHaveAttribute('href', `/api/v1/audits/${AUDIT_ID}/export.csv`);
    expect(screen.getByRole('link', { name: /export md/i })).toHaveAttribute(
      'href',
      `/api/v1/audits/${AUDIT_ID}/export.md`,
    );
  });
});
