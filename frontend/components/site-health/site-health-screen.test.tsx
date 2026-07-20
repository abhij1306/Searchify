import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';
import { ProjectProvider } from '@/lib/project/project-context';
import { SiteHealthScreen } from './site-health-screen';

const WORKSPACE = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const PROJECT = '11111111-1111-4111-8111-111111111111';
const CRAWL = '22222222-2222-4222-8222-222222222222';

const project = {
  id: PROJECT,
  workspace_id: WORKSPACE,
  name: 'Acme',
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

const entitlement = {
  workspace_id: WORKSPACE,
  plan_key: 'starter',
  access_mode: 'selection',
  sample_url_limit: 10,
  monitored_url_limit: 50,
  can_view_discovered_total: true,
  capability_revision: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

function crawl(overrides: Record<string, unknown> = {}) {
  return {
    id: CRAWL,
    workspace_id: WORKSPACE,
    project_id: PROJECT,
    profile_id: '55555555-5555-4555-8555-555555555555',
    status: 'failed',
    discovery_status: 'completed',
    analysis_status: 'failed',
    root_url: 'https://acme.com/',
    sample_mode: false,
    seed: '1',
    inventory_complete: true,
    visible_url_count: 3,
    analyzed_count: 0,
    failed_count: 0,
    discovered_count: 3,
    total_url_count: 3,
    has_more_site_urls: false,
    score_summary: null,
    extractor_version: 'e1',
    analyzer_version: 'a1',
    rule_version: 'r1',
    scoring_version: 's1',
    error_message: '',
    created_at: '2026-07-16T00:00:00Z',
    updated_at: '2026-07-16T00:00:00Z',
    started_at: '2026-07-16T00:00:00Z',
    completed_at: '2026-07-16T00:05:00Z',
    ...overrides,
  };
}

function mockRoutes(crawlOverrides: Record<string, unknown> = {}) {
  mswServer.use(
    http.get('/api/v1/projects', () => HttpResponse.json([project])),
    http.get('/api/v1/entitlements', () => HttpResponse.json(entitlement)),
    http.get(`/api/v1/projects/${PROJECT}/site-health`, () =>
      HttpResponse.json({
        project_id: PROJECT,
        crawl: crawl(crawlOverrides),
        score_summary: null,
        quota: { used: 3, limit: 50 },
      }),
    ),
    http.get(`/api/v1/projects/${PROJECT}/monitored-urls`, () =>
      HttpResponse.json({
        project_id: PROJECT,
        selection_version: 1,
        monitored_urls: [],
        quota: { used: 0, limit: 50 },
      }),
    ),
    http.get(`/api/v1/site-crawls/${CRAWL}/pages`, () =>
      HttpResponse.json({ items: [], next_cursor: null }),
    ),
    http.get(`/api/v1/site-crawls/${CRAWL}/inventory`, () =>
      HttpResponse.json({ items: [], next_cursor: null }),
    ),
    http.get(`/api/v1/site-crawls/${CRAWL}/events`, () => HttpResponse.text('', { status: 200 })),
  );
}

function renderScreen() {
  return renderWithProviders(
    <ProjectProvider>
      <SiteHealthScreen />
    </ProjectProvider>,
  );
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('SiteHealthScreen — terminal phase', () => {
  it('renders an explicit terminal state (not the active-progress UI) for a failed crawl', async () => {
    mockRoutes({ status: 'failed', error_message: 'Robots.txt denied crawling.' });

    renderScreen();

    expect(await screen.findByText('Robots.txt denied crawling.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start a new crawl' })).toBeInTheDocument();
    // No redundant Cancel control for an already-stopped crawl.
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
  });

  it('shows generic terminal copy for a cancelled crawl with NOTHING discovered', async () => {
    mockRoutes({ status: 'cancelled', error_message: '', visible_url_count: 0 });

    renderScreen();

    expect(
      await screen.findByText('This crawl was cancelled before it produced results.'),
    ).toBeInTheDocument();
  });

  it('keeps the discovered inventory (selection phase) for a cancelled Starter crawl', async () => {
    // Cancelling discovery must NOT dead-end the discovered URLs: the
    // inventory persists server-side, so the selection screen renders with a
    // cancellation notice instead of the terminal card.
    mockRoutes({ status: 'cancelled', error_message: '', visible_url_count: 3 });
    mswServer.use(
      http.get(`/api/v1/site-crawls/${CRAWL}/inventory`, () =>
        HttpResponse.json({
          items: [
            {
              site_url_id: '66666666-6666-4666-8666-666666666666',
              normalized_url: 'https://acme.com/pricing',
              display_url: 'https://acme.com/pricing',
              title: null,
              content_type: 'text/html',
              source: 'link',
              depth: 1,
              monitored: false,
              first_seen_at: null,
              last_seen_at: null,
              issue_count: null,
              technical_score: null,
              aeo_score: null,
              overall_score: null,
              last_audited: null,
            },
          ],
          next_cursor: null,
        }),
      ),
    );

    renderScreen();

    expect(
      await screen.findByText(/Discovery was cancelled — the pages found so far are kept/),
    ).toBeInTheDocument();
    // The persisted inventory row itself is visible and selectable.
    expect(
      await screen.findByLabelText('Monitor https://acme.com/pricing'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start analysis' })).toBeInTheDocument();
    expect(
      screen.queryByText('This crawl was cancelled before it produced results.'),
    ).not.toBeInTheDocument();
  });

  it('renders no Re-crawl button in the header while a crawl is still active (discovering)', async () => {
    mockRoutes({
      status: 'running',
      discovery_status: 'running',
      analysis_status: 'pending',
      score_summary: null,
    });

    renderScreen();

    // Wait for the screen to settle past the initial loading skeleton.
    await waitFor(() => expect(screen.queryByText(/Discovering pages/)).toBeInTheDocument());
    // The header only renders "Re-crawl now" for the dashboard/terminal
    // phases; an active (discovering) crawl must not expose it, since the
    // dedicated in-flow Cancel button is the only control while active.
    expect(screen.queryByRole('button', { name: /Re-crawl now/ })).not.toBeInTheDocument();
  });

  it('keeps the dashboard + partial scores and labels the run Cancelled (with Re-crawl)', async () => {
    // Cancellation with partial data must keep the latest dashboard, partial
    // scores, and inventory visible, explicitly labelled Cancelled, and offer
    // Re-crawl — never blank the results.
    const summary = {
      overall_score: 71,
      technical_score: 80,
      aeo_score: 62,
      selected_count: 10,
      analyzed_count: 4,
      issue_count: 3,
      scoring_version: 's1',
    };
    mockRoutes({
      status: 'cancelled',
      discovery_status: 'cancelled',
      analysis_status: 'cancelled',
      score_summary: summary,
    });
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/site-health`, () =>
        HttpResponse.json({
          project_id: PROJECT,
          crawl: crawl({
            status: 'cancelled',
            discovery_status: 'cancelled',
            analysis_status: 'cancelled',
            score_summary: summary,
          }),
          score_summary: summary,
          quota: { used: 4, limit: 50 },
        }),
      ),
    );

    renderScreen();

    // Explicit, text-labelled Cancelled notice (not color-only) with Re-crawl copy.
    expect(
      await screen.findByText(/This run was cancelled — showing the pages analyzed so far/),
    ).toBeInTheDocument();
    // The dashboard score value stays visible (partial results kept).
    expect(await screen.findByText('71 / 100')).toBeInTheDocument();
    // The header offers Re-crawl for a terminal-with-data dashboard.
    expect(screen.getByRole('button', { name: /Re-crawl now/ })).toBeInTheDocument();
    // Not the bare terminal card.
    expect(
      screen.queryByText('This crawl was cancelled before it produced results.'),
    ).not.toBeInTheDocument();
  });
});
