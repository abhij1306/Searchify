import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';
import { ProjectProvider } from '@/lib/project/project-context';
import { SiteHealthScreen } from './site-health-screen';

// The analyzing/scored inventory modes render PagesTable, which calls
// useRouter for clickable rows; stub next/navigation (unavailable in jsdom).
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

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

function inventoryRow(id: string, url: string) {
  return {
    site_url_id: id,
    normalized_url: url,
    display_url: url,
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

describe('SiteHealthScreen — terminal states on the canonical screen', () => {
  it('renders an explicit terminal notice (not the active-progress UI) for a failed crawl', async () => {
    mockRoutes({ status: 'failed', error_message: 'Robots.txt denied crawling.' });

    renderScreen();

    expect(await screen.findByText('Robots.txt denied crawling.')).toBeInTheDocument();
    // The header offers the restart — the screen itself stays the dashboard.
    expect(screen.getByRole('button', { name: 'Start a new crawl' })).toBeInTheDocument();
    // No redundant Cancel control for an already-stopped crawl.
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
    // The score section stays mounted with placeholders (no screen swap).
    expect(screen.getByTestId('score-section')).toBeInTheDocument();
  });

  it('shows generic terminal copy for a cancelled crawl with NOTHING discovered', async () => {
    mockRoutes({ status: 'cancelled', error_message: '', visible_url_count: 0 });

    renderScreen();

    expect(
      await screen.findByText('This crawl was cancelled before it produced results.'),
    ).toBeInTheDocument();
  });

  it('keeps the discovered inventory (selection mode) for a cancelled Starter crawl', async () => {
    // Cancelling discovery must NOT dead-end the discovered URLs: the
    // inventory persists server-side, so the inventory section switches to
    // selection mode with a cancellation notice instead of a terminal card.
    mockRoutes({ status: 'cancelled', error_message: '', visible_url_count: 3 });
    mswServer.use(
      http.get(`/api/v1/site-crawls/${CRAWL}/inventory`, () =>
        HttpResponse.json({
          items: [inventoryRow('66666666-6666-4666-8666-666666666666', 'https://acme.com/pricing')],
          next_cursor: null,
        }),
      ),
    );

    renderScreen();

    expect(
      await screen.findByText(/Discovery was cancelled — the pages found so far are kept/),
    ).toBeInTheDocument();
    // The persisted inventory row itself is visible and selectable.
    expect(await screen.findByLabelText('Monitor https://acme.com/pricing')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start analysis' })).toBeInTheDocument();
    expect(
      screen.queryByText('This crawl was cancelled before it produced results.'),
    ).not.toBeInTheDocument();
  });

  it('offers Cancel (not Re-crawl) in the header while a crawl is discovering', async () => {
    mockRoutes({
      status: 'running',
      discovery_status: 'running',
      analysis_status: 'pending',
      score_summary: null,
    });

    renderScreen();

    // Wait for the screen to settle past the initial loading skeleton.
    await waitFor(() => expect(screen.queryByText(/Discovering pages/)).toBeInTheDocument());
    // The single header control is Cancel while active; Re-crawl only appears
    // for a settled dashboard/terminal state.
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
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
    mockRoutes();
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
    // Not the bare terminal notice.
    expect(
      screen.queryByText('This crawl was cancelled before it produced results.'),
    ).not.toBeInTheDocument();
  });
});

describe('SiteHealthScreen — canonical single-screen flow (regression)', () => {
  it('walks discover → cancel → select → start analysis → finish without ever swapping the screen', async () => {
    // The reported bug: each lifecycle step replaced the whole panel (cancel
    // showed a URL-list screen, starting analysis bounced back to that list,
    // finishing jumped to a separate dashboard). This walks the exact
    // sequence against ONE mutable server state and asserts the canonical
    // layout container is the SAME DOM node at every step — data updates in
    // place, the screen never changes.
    const user = userEvent.setup();
    const NEW_CRAWL = '99999999-9999-4999-8999-999999999999';
    const URL_ID = '66666666-6666-4666-8666-666666666666';
    const summary = {
      overall_score: 71,
      technical_score: 80,
      aeo_score: 62,
      selected_count: 1,
      analyzed_count: 1,
      issue_count: 3,
      scoring_version: 's1',
    };

    // Mutable server state the handlers read on every request.
    let serverCrawl = crawl({
      status: 'running',
      discovery_status: 'running',
      analysis_status: 'pending',
      inventory_complete: false,
      score_summary: null,
      completed_at: null,
    });
    const monitored: Array<Record<string, unknown>> = [];

    mswServer.use(
      http.get('/api/v1/projects', () => HttpResponse.json([project])),
      http.get('/api/v1/entitlements', () => HttpResponse.json(entitlement)),
      http.get(`/api/v1/projects/${PROJECT}/site-health`, () =>
        HttpResponse.json({
          project_id: PROJECT,
          crawl: serverCrawl,
          score_summary: serverCrawl.score_summary,
          quota: { used: monitored.length, limit: 50 },
        }),
      ),
      http.get(`/api/v1/projects/${PROJECT}/monitored-urls`, () =>
        HttpResponse.json({
          project_id: PROJECT,
          selection_version: 1,
          monitored_urls: monitored,
          quota: { used: monitored.length, limit: 50 },
        }),
      ),
      http.put(`/api/v1/projects/${PROJECT}/monitored-urls`, async ({ request }) => {
        const body = (await request.json()) as { site_url_ids: string[] };
        monitored.length = 0;
        for (const id of body.site_url_ids) {
          monitored.push({
            site_url_id: id,
            normalized_url: 'https://acme.com/pricing',
            display_url: 'https://acme.com/pricing',
            title: null,
            active: true,
            selection_source: 'user',
            selected_at: '2026-07-16T00:00:00Z',
            deselected_at: null,
          });
        }
        return HttpResponse.json({
          project_id: PROJECT,
          selection_version: 2,
          monitored_urls: monitored,
          quota: { used: monitored.length, limit: 50 },
        });
      }),
      http.post(`/api/v1/site-crawls/${serverCrawl.id}/cancel`, () => {
        serverCrawl = crawl({
          status: 'cancelled',
          discovery_status: 'cancelled',
          analysis_status: 'cancelled',
          score_summary: null,
          completed_at: null,
        });
        return HttpResponse.json(serverCrawl);
      }),
      http.post('/api/v1/site-crawls', () => {
        serverCrawl = crawl({
          id: NEW_CRAWL,
          status: 'running',
          discovery_status: 'completed',
          analysis_status: 'running',
          score_summary: null,
          completed_at: null,
        });
        return HttpResponse.json(serverCrawl);
      }),
      http.get('/api/v1/site-crawls/:id/pages', () =>
        HttpResponse.json({ items: [], next_cursor: null }),
      ),
      http.get('/api/v1/site-crawls/:id/inventory', () =>
        HttpResponse.json({
          items: [inventoryRow(URL_ID, 'https://acme.com/pricing')],
          next_cursor: null,
        }),
      ),
      http.get('/api/v1/site-crawls/:id/events', () => HttpResponse.text('', { status: 200 })),
    );

    const { queryClient } = renderScreen();

    // Step 1 — discovering: canonical layout with live discovery narration.
    await waitFor(() => expect(screen.queryByTestId('site-health-canonical')).toBeInTheDocument());
    const canonical = screen.getByTestId('site-health-canonical');
    expect(screen.getByText(/pages discovered so far/)).toBeInTheDocument();

    // Step 2 — cancel from the header. The SAME screen shifts to selection
    // mode (inventory persists), no navigation, no terminal dead-end.
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(
      await screen.findByText(/Discovery was cancelled — the pages found so far are kept/),
    ).toBeInTheDocument();
    expect(screen.getByTestId('site-health-canonical')).toBe(canonical);

    // Step 3 — stage + save a monitored page, then start the analysis.
    await user.click(await screen.findByLabelText('Monitor https://acme.com/pricing'));
    await user.click(screen.getByRole('button', { name: /Save selection/ }));
    const startAnalysis = screen.getByRole('button', { name: 'Start analysis' });
    await waitFor(() => expect(startAnalysis).toBeEnabled());
    await user.click(startAnalysis);

    // The screen moves FORWARD to the analysis view in place — it must never
    // bounce back to the selection list (the reported regression).
    expect(
      await screen.findByText('Auditing selected pages for technical and AEO health issues'),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText('Monitor https://acme.com/pricing')).not.toBeInTheDocument();
    expect(screen.getByTestId('site-health-canonical')).toBe(canonical);

    // Step 4 — the run finishes server-side; the next poll/SSE invalidation
    // lands the scores IN PLACE on the same screen (no dashboard jump).
    serverCrawl = crawl({
      id: NEW_CRAWL,
      status: 'completed',
      discovery_status: 'completed',
      analysis_status: 'completed',
      score_summary: summary,
    });
    await queryClient.invalidateQueries();

    expect(await screen.findByText('71 / 100')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Re-crawl now/ })).toBeInTheDocument();
    expect(screen.getByTestId('site-health-canonical')).toBe(canonical);
    // The score section that showed placeholders during analysis is the same
    // mounted section now showing real data.
    expect(screen.getByTestId('score-section')).toBeInTheDocument();
  });
});
