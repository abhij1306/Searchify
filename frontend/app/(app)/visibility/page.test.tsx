import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { setActiveWorkspaceId } from '@/lib/api/client';
import { ProjectProvider } from '@/lib/project/project-context';
import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import VisibilityPage from './page';

const WORKSPACE_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const AUDIT_LATEST = '22222222-2222-4222-8222-222222222222';
const AUDIT_OLDER = '33333333-3333-4333-8333-333333333333';

function makeProject() {
  return {
    id: PROJECT_ID,
    workspace_id: WORKSPACE_ID,
    name: 'Searchify',
    brand_name: 'Acme',
    website_url: 'https://acme.com',
    country_code: 'US',
    language_code: 'en',
    benchmark_mode: 'consumer_like',
    default_repetitions: 3,
    brand: { aliases: [] },
    owned_domains: [],
    unintended_domains: [],
    competitors: [],
    prompt_sets: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

function makeAudit(id: string, completedAt: string) {
  return {
    id,
    workspace_id: WORKSPACE_ID,
    project_id: PROJECT_ID,
    status: 'completed',
    benchmark_mode: 'consumer_like',
    repetitions: 2,
    random_seed: '1',
    requested_count: 6,
    completed_count: 6,
    failed_count: 0,
    error_message: '',
    engine_snapshots: [],
    created_at: completedAt,
    updated_at: completedAt,
    started_at: completedAt,
    completed_at: completedAt,
  };
}

function makeVisibility(auditId: string, score: number) {
  return {
    project_id: PROJECT_ID,
    audit_id: auditId,
    audit_status: 'completed',
    analyzer_version: 'v1',
    scoring_rule_version: 'v1',
    total_completed: 6,
    total_failed: 0,
    visibility_score: score,
    rankings: [
      {
        name: 'Acme',
        is_brand: true,
        mention_rate: score / 100,
        citation_rate: 0.3,
        share_of_voice: 0.6,
        mention_count: 4,
        sentiment: null,
        avg_position: null,
      },
      {
        name: 'Globex',
        is_brand: false,
        mention_rate: 0.3,
        citation_rate: 0.1,
        share_of_voice: 0.4,
        mention_count: 2,
        sentiment: null,
        avg_position: null,
      },
    ],
    per_engine: [
      {
        logical_engine: 'gemini',
        total_completed: 3,
        brand_mention_rate: 0.6,
        owned_citation_rate: 0.3,
        search_use_rate: 0.5,
        visibility_score: 60,
      },
      {
        logical_engine: 'claude',
        total_completed: 3,
        brand_mention_rate: 0.7,
        owned_citation_rate: 0.4,
        search_use_rate: 0.6,
        visibility_score: 70,
      },
    ],
    sentiment: null,
    avg_position: null,
    created_at: '2026-07-15T00:00:00Z',
  };
}

function renderPage() {
  return renderWithProviders(
    <ProjectProvider>
      <VisibilityPage />
    </ProjectProvider>,
  );
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  window.localStorage.clear();
  setActiveWorkspaceId(null);
});
afterEach(() => {
  mswServer.resetHandlers();
  vi.restoreAllMocks();
});
afterAll(() => mswServer.close());

describe('VisibilityPage', () => {
  it('renders the score and per-engine comparison from data', async () => {
    mswServer.use(
      http.get('/api/v1/projects', () => HttpResponse.json([makeProject()])),
      http.get('/api/v1/audits', () => HttpResponse.json([makeAudit(AUDIT_LATEST, '2026-07-15T00:00:00Z')])),
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, () =>
        HttpResponse.json(makeVisibility(AUDIT_LATEST, 67)),
      ),
    );

    renderPage();

    // Score ring announces the value.
    expect(await screen.findByLabelText('Visibility score: 67%')).toBeInTheDocument();
    // Per-engine comparison cards render.
    expect(screen.getByRole('heading', { name: 'Per-engine comparison' })).toBeInTheDocument();
    expect(screen.getByText('Gemini')).toBeInTheDocument();
    expect(screen.getByText('Claude')).toBeInTheDocument();
  });

  it('sorts the rankings table with brand + competitors and renders the placeholders', async () => {
    mswServer.use(
      http.get('/api/v1/projects', () => HttpResponse.json([makeProject()])),
      http.get('/api/v1/audits', () => HttpResponse.json([makeAudit(AUDIT_LATEST, '2026-07-15T00:00:00Z')])),
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, () =>
        HttpResponse.json(makeVisibility(AUDIT_LATEST, 67)),
      ),
    );

    renderPage();

    const rankings = (await screen.findByRole('heading', { name: 'Rankings' })).closest('section')!;
    const rows = within(rankings).getAllByRole('row');
    // Header + brand (higher SOV) first, then competitor.
    const bodyRows = rows.slice(1);
    expect(within(bodyRows[0]).getByText('Acme')).toBeInTheDocument();
    expect(within(bodyRows[0]).getByText('You')).toBeInTheDocument();
    expect(within(bodyRows[1]).getByText('Globex')).toBeInTheDocument();
    // Sentiment + Avg Position show the not-yet-computed placeholder.
    expect(within(bodyRows[0]).getAllByText('—').length).toBeGreaterThanOrEqual(2);
  });

  it('changes the query when a different run is selected', async () => {
    const seen: (string | null)[] = [];
    mswServer.use(
      http.get('/api/v1/projects', () => HttpResponse.json([makeProject()])),
      http.get('/api/v1/audits', () =>
        HttpResponse.json([
          makeAudit(AUDIT_LATEST, '2026-07-15T00:00:00Z'),
          makeAudit(AUDIT_OLDER, '2026-07-10T00:00:00Z'),
        ]),
      ),
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, ({ request }) => {
        const auditId = new URL(request.url).searchParams.get('audit_id');
        seen.push(auditId);
        const score = auditId === AUDIT_OLDER ? 42 : 67;
        return HttpResponse.json(makeVisibility(auditId ?? AUDIT_LATEST, score));
      }),
    );

    const user = userEvent.setup();
    renderPage();

    // Defaults to the latest run.
    expect(await screen.findByLabelText('Visibility score: 67%')).toBeInTheDocument();
    expect(seen[0]).toBe(AUDIT_LATEST);

    await user.click(screen.getByRole('button', { name: 'Select run' }));
    const olderLabel = new Date('2026-07-10T00:00:00Z').toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
    await user.click(await screen.findByRole('menuitem', { name: olderLabel }));

    // The query re-runs for the older audit id.
    await waitFor(() => expect(screen.getByLabelText('Visibility score: 42%')).toBeInTheDocument());
    expect(seen).toContain(AUDIT_OLDER);
  });

  it('narrows the per-engine comparison when an engine filter is applied', async () => {
    mswServer.use(
      http.get('/api/v1/projects', () => HttpResponse.json([makeProject()])),
      http.get('/api/v1/audits', () => HttpResponse.json([makeAudit(AUDIT_LATEST, '2026-07-15T00:00:00Z')])),
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, () =>
        HttpResponse.json(makeVisibility(AUDIT_LATEST, 67)),
      ),
    );

    const user = userEvent.setup();
    renderPage();

    await screen.findByLabelText('Visibility score: 67%');
    const comparisonOf = () =>
      screen.getByRole('heading', { name: 'Per-engine comparison' }).closest('section')!;
    expect(within(comparisonOf()).getByText('Claude')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Filter by engine' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Gemini' }));

    await waitFor(() => expect(within(comparisonOf()).queryByText('Claude')).not.toBeInTheDocument());
    expect(within(comparisonOf()).getByText('Gemini')).toBeInTheDocument();
  });

  it('shows the empty state when the project has no completed runs', async () => {
    mswServer.use(
      http.get('/api/v1/projects', () => HttpResponse.json([makeProject()])),
      http.get('/api/v1/audits', () => HttpResponse.json([])),
    );

    renderPage();

    expect(await screen.findByText('No completed runs yet')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /launch your first audit/i })).toHaveAttribute('href', '/runs');
  });
});
