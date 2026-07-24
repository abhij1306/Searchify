import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';
import { ProjectProvider } from '@/lib/project/project-context';
import { OpportunitiesScreen } from './opportunities-screen';

const WORKSPACE = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const PROJECT = '11111111-1111-4111-8111-111111111111';
const OPP_A = '22222222-2222-4222-8222-222222222222';
const OPP_B = '33333333-3333-4333-8333-333333333333';
const RUN = '44444444-4444-4444-8444-444444444444';

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

function opportunity(overrides: Record<string, unknown> = {}) {
  return {
    id: OPP_A,
    project_id: PROJECT,
    rule_id: 'brand_absent_high_value_prompt',
    opportunity_type: 'visibility',
    severity: 'high',
    priority_score: 120,
    title: 'Brand absent from high-value prompt',
    target_key: `prompt:${OPP_A}`,
    target_prompt_id: OPP_A,
    target_url: null,
    target_theme: 'crm',
    status: 'open',
    created_at: '2026-07-24T00:00:00Z',
    updated_at: '2026-07-24T00:00:00Z',
    ...overrides,
  };
}

const siteRow = opportunity({
  id: OPP_B,
  rule_id: 'thin_content',
  opportunity_type: 'site',
  severity: 'low',
  priority_score: 10,
  title: 'Thin content on an owned page',
  target_key: 'url:https://acme.com/blog',
  target_prompt_id: null,
  target_url: 'https://acme.com/blog',
  target_theme: null,
});

const summary = {
  computed: true,
  run_id: RUN,
  audit_id: RUN,
  site_crawl_id: RUN,
  counts_by_type: { site: 1, topic: 0, traffic: 0, visibility: 1 },
  counts_by_severity: { critical: 0, high: 1, info: 0, low: 1, medium: 0 },
  counts_by_status: { dismissed: 0, in_progress: 0, open: 2, resolved: 0 },
  total_count: 2,
  median_priority: 65,
  analyzer_version: 'opp-analyzer-1',
  rule_version: 'opp-rules-1',
  formula_version: 'opp-formula-1',
  computed_at: '2026-07-24T00:00:00Z',
};

const recomputeResponse = {
  id: RUN,
  run_id: RUN,
  audit_id: RUN,
  site_crawl_id: RUN,
  counts_by_type: summary.counts_by_type,
  counts_by_severity: summary.counts_by_severity,
  counts_by_status: summary.counts_by_status,
  total_count: 2,
  median_priority: 65,
  analyzer_version: 'opp-analyzer-1',
  rule_version: 'opp-rules-1',
  formula_version: 'opp-formula-1',
  created_at: '2026-07-24T00:00:00Z',
};

const detail = {
  ...opportunity(),
  remediation: 'Publish a comparison page.',
  evidence: {
    prompt_text: 'best crm for small teams',
    prompt_theme: 'crm',
    prompt_intent: 'purchase',
    engines: ['gemini'],
    repetitions: 1,
    owned_citation_count: 0,
    competitor_names: ['Globex'],
    audit_id: RUN,
  },
  source_analysis_ids: [RUN],
  source_issue_ids: [],
  source_metric_ids: [RUN],
  source_traffic_ids: [],
  analyzer_version: 'opp-analyzer-1',
  rule_version: 'opp-rules-1',
  formula_version: 'opp-formula-1',
  superseded_by_id: null,
  superseded_at: null,
};

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

function renderScreen() {
  return renderWithProviders(
    <ProjectProvider>
      <OpportunitiesScreen />
    </ProjectProvider>,
  );
}

function mockBase() {
  mswServer.use(http.get('/api/v1/projects', () => HttpResponse.json([project])));
}

describe('OpportunitiesScreen', () => {
  it('shows the never-computed empty state with a Recompute CTA', async () => {
    mockBase();
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/opportunities/summary`, () =>
        HttpResponse.json({
          ...summary,
          computed: false,
          run_id: null,
          audit_id: null,
          site_crawl_id: null,
          counts_by_type: {},
          counts_by_severity: {},
          counts_by_status: {},
          total_count: 0,
          median_priority: null,
          computed_at: null,
        }),
      ),
    );

    renderScreen();

    expect(await screen.findByText('No opportunities computed yet')).toBeInTheDocument();
    expect(screen.getByText(/Run those first, then recompute/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Recompute' })).toBeInTheDocument();
  });

  it('renders the summary strip + priority-sorted catalog when computed', async () => {
    mockBase();
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/opportunities/summary`, () =>
        HttpResponse.json(summary),
      ),
      http.get(`/api/v1/projects/${PROJECT}/opportunities`, () =>
        HttpResponse.json({ items: [opportunity(), siteRow], next_cursor: null }),
      ),
    );

    renderScreen();

    // Summary strip (API-owned counts) + meta.
    expect(await screen.findByText('Opportunity snapshot')).toBeInTheDocument();
    expect(screen.getByText(/analyzer opp-analyzer-1 · formula opp-formula-1/)).toBeInTheDocument();
    expect(screen.getByText('Total')).toBeInTheDocument();
    // "Open" appears as the summary tile AND the per-row status badges.
    expect(screen.getAllByText('Open').length).toBeGreaterThan(0);
    // Export links are same-origin attachments.
    expect(screen.getByRole('link', { name: 'Export CSV' })).toHaveAttribute(
      'href',
      `/api/v1/projects/${PROJECT}/opportunities/export.csv`,
    );

    // Catalog rows: title, target line, badges.
    expect(await screen.findByText('Brand absent from high-value prompt')).toBeInTheDocument();
    expect(screen.getByText('Thin content on an owned page')).toBeInTheDocument();
    expect(screen.getByText('https://acme.com/blog')).toBeInTheDocument();
    // "Visibility" appears as the summary tile, the filter chip, AND the row badge.
    expect(screen.getAllByText('Visibility').length).toBeGreaterThan(0);
    expect(screen.getAllByText('HIGH').length).toBeGreaterThan(0);
    expect(screen.getByText('120.0')).toBeInTheDocument();
  });

  it('sends filter chips as server query params (never a client filter)', async () => {
    mockBase();
    const seen: URLSearchParams[] = [];
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/opportunities/summary`, () =>
        HttpResponse.json(summary),
      ),
      http.get(`/api/v1/projects/${PROJECT}/opportunities`, ({ request }) => {
        seen.push(new URL(request.url).searchParams);
        return HttpResponse.json({ items: [siteRow], next_cursor: null });
      }),
    );

    const user = userEvent.setup();
    renderScreen();
    await screen.findByText('Thin content on an owned page');

    await user.click(screen.getByRole('button', { name: 'Site' }));
    await waitFor(() =>
      expect(seen.some((params) => params.get('type') === 'site')).toBe(true),
    );

    await user.click(screen.getByRole('button', { name: 'Dismissed' }));
    await waitFor(() =>
      expect(seen.some((params) => params.get('status') === 'dismissed')).toBe(true),
    );

    await user.click(screen.getByRole('button', { name: 'Low' }));
    await waitFor(() =>
      expect(seen.some((params) => params.get('severity') === 'low')).toBe(true),
    );
  });

  it('recompute posts and invalidates (summary + list refetch)', async () => {
    mockBase();
    let summaryCalls = 0;
    let recomputeCalls = 0;
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/opportunities/summary`, () => {
        summaryCalls += 1;
        return HttpResponse.json(summary);
      }),
      http.get(`/api/v1/projects/${PROJECT}/opportunities`, () =>
        HttpResponse.json({ items: [opportunity()], next_cursor: null }),
      ),
      http.post(`/api/v1/projects/${PROJECT}/opportunities/recompute`, () => {
        recomputeCalls += 1;
        return HttpResponse.json(recomputeResponse);
      }),
    );

    const user = userEvent.setup();
    renderScreen();
    await screen.findByText('Brand absent from high-value prompt');
    const before = summaryCalls;

    await user.click(screen.getByRole('button', { name: 'Recompute' }));
    await waitFor(() => expect(recomputeCalls).toBe(1));
    await waitFor(() => expect(summaryCalls).toBeGreaterThan(before));
  });

  it('row status dropdown patches the status and refetches the list', async () => {
    mockBase();
    const patches: unknown[] = [];
    let listCalls = 0;
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/opportunities/summary`, () =>
        HttpResponse.json(summary),
      ),
      http.get(`/api/v1/projects/${PROJECT}/opportunities`, () => {
        listCalls += 1;
        return HttpResponse.json({ items: [opportunity()], next_cursor: null });
      }),
      http.patch(`/api/v1/opportunities/${OPP_A}`, async ({ request }) => {
        patches.push(await request.json());
        return HttpResponse.json(opportunity({ status: 'dismissed' }));
      }),
    );

    const user = userEvent.setup();
    renderScreen();
    await screen.findByText('Brand absent from high-value prompt');
    const before = listCalls;

    await user.click(
      screen.getByRole('button', { name: 'Change status for Brand absent from high-value prompt' }),
    );
    await user.click(await screen.findByRole('menuitem', { name: 'Dismissed' }));

    await waitFor(() => expect(patches).toEqual([{ status: 'dismissed' }]));
    await waitFor(() => expect(listCalls).toBeGreaterThan(before));
  });

  it('row click opens the evidence drawer with evidence + provenance + footer actions', async () => {
    mockBase();
    const patches: unknown[] = [];
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/opportunities/summary`, () =>
        HttpResponse.json(summary),
      ),
      http.get(`/api/v1/projects/${PROJECT}/opportunities`, () =>
        HttpResponse.json({ items: [opportunity()], next_cursor: null }),
      ),
      http.get(`/api/v1/opportunities/${OPP_A}`, () => HttpResponse.json(detail)),
      http.patch(`/api/v1/opportunities/${OPP_A}`, async ({ request }) => {
        patches.push(await request.json());
        return HttpResponse.json(opportunity({ status: 'in_progress' }));
      }),
    );

    const user = userEvent.setup();
    renderScreen();
    const rowTitle = await screen.findByText('Brand absent from high-value prompt');
    await user.click(rowTitle);

    // Drawer: prompt quote, kv rows, competitor chip, provenance, remediation.
    expect(await screen.findByText('Opportunity detail')).toBeInTheDocument();
    expect(screen.getByText('“best crm for small teams”')).toBeInTheDocument();
    expect(screen.getByText('Globex')).toBeInTheDocument();
    expect(screen.getByText('brand_absent_high_value_prompt')).toBeInTheDocument();
    expect(screen.getByText('Publish a comparison page.')).toBeInTheDocument();

    // Footer workflow: Mark in progress patches the row.
    await user.click(screen.getByRole('button', { name: 'Mark in progress' }));
    await waitFor(() => expect(patches).toEqual([{ status: 'in_progress' }]));

    // Close returns to the catalog.
    await user.click(screen.getByRole('button', { name: 'Close drawer' }));
    await waitFor(() =>
      expect(screen.queryByText('Opportunity detail')).not.toBeInTheDocument(),
    );
  });
});
