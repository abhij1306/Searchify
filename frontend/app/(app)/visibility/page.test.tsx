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
const ANALYSIS_A = '44444444-4444-4444-8444-444444444444';
const ANALYSIS_B = '55555555-5555-4555-8555-555555555555';
const ANALYSIS_C = '66666666-6666-4666-8666-666666666666';
const PROMPT_A = '77777777-7777-4777-8777-777777777777';
const SNAP_A = '88888888-8888-4888-8888-888888888888';

// ---------------------------------------------------------------------------
// next/navigation mock — a controllable URL so ?tab= sync + back/forward can be
// asserted. `replace` updates the params; the hooks read the same live object.
// ---------------------------------------------------------------------------
let currentSearch = new URLSearchParams();
const replaceMock = vi.fn((url: string) => {
  const q = url.includes('?') ? url.slice(url.indexOf('?') + 1) : '';
  currentSearch = new URLSearchParams(q);
});
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn(), back: vi.fn(), forward: vi.fn() }),
  usePathname: () => '/visibility',
  useSearchParams: () => currentSearch,
}));

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

function makeTrendPoint(auditId: string, completedAt: string, score: number | null) {
  return {
    audit_id: auditId,
    completed_at: completedAt,
    logical_engine: null,
    visibility_score: score,
    brand_mention_rate: 0.5,
    owned_citation_rate: 0.3,
    sov: { response: 0.4, mention: 0.5 },
    rankings: [
      {
        name: 'Acme',
        is_brand: true,
        mention_rate: 0.5,
        citation_rate: 0.3,
        share_of_voice: 0.6,
        mention_count: 4,
        sentiment: null,
        avg_position: null,
      },
    ],
    sentiment: null,
    avg_position: null,
    source_snapshot_ids: [],
    analyzer_versions: ['v1'],
    scoring_rule_versions: ['v1'],
    spans_version_boundary: false,
  };
}

function makeCitation(ordinal: number) {
  return {
    ordinal,
    url: 'https://acme.com/blog',
    title: 'Acme Blog',
    domain: 'acme.com',
    classification: 'owned',
    is_owned: true,
    is_unintended: false,
    matched_competitor: null,
  };
}

function makeEvidenceItem(overrides: Record<string, unknown> = {}) {
  return {
    audit_id: AUDIT_LATEST,
    task_id: '99999999-9999-4999-8999-999999999999',
    analysis_id: ANALYSIS_A,
    artifact_id: 'abababab-abab-4bab-8bab-abababababab',
    prompt_snapshot_id: SNAP_A,
    prompt_id: PROMPT_A,
    prompt_index: 3,
    prompt_text: 'Best affordable clothing stores in Australia?',
    repetition: 1,
    completed_at: '2026-07-15T14:32:00Z',
    logical_engine: 'chatgpt',
    transport_provider: 'openai',
    transport_model: 'gpt-5.4',
    search_used: true,
    search_query_count: 2,
    query_text_available: true,
    state: 'queries_available',
    search_events: [
      { sequence: 0, query: 'affordable family clothing Australia 2026', call_id: 'c1', call_sequence: 0, query_sequence: 0 },
      { sequence: 1, query: 'best budget clothing shops families', call_id: 'c1', call_sequence: 0, query_sequence: 1 },
    ],
    event_source: 'raw_artifact',
    mentions: [
      { kind: 'brand', name: 'Acme', first_offset: 12, artifact_id: null, analyzer_version: 'v1' },
      { kind: 'competitor', name: 'Globex', first_offset: null, artifact_id: null, analyzer_version: 'v1' },
    ],
    citations: [makeCitation(1)],
    ...overrides,
  };
}

function makeEvidenceResponse(overrides: Record<string, unknown> = {}) {
  return { items: [makeEvidenceItem()], truncated: false, ...overrides };
}

function renderPage() {
  return renderWithProviders(
    <ProjectProvider>
      <VisibilityPage />
    </ProjectProvider>,
  );
}

/** Register the project + audits handlers shared by most tests. */
function useBaseHandlers(extra: Parameters<typeof mswServer.use> = []) {
  mswServer.use(
    http.get('/api/v1/projects', () => HttpResponse.json([makeProject()])),
    http.get('/api/v1/audits', () =>
      HttpResponse.json([makeAudit(AUDIT_LATEST, '2026-07-15T00:00:00Z')]),
    ),
    ...extra,
  );
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  window.localStorage.clear();
  setActiveWorkspaceId(null);
  currentSearch = new URLSearchParams();
  replaceMock.mockClear();
});
afterEach(() => {
  mswServer.resetHandlers();
  vi.restoreAllMocks();
});
afterAll(() => mswServer.close());

describe('VisibilityPage — tablist', () => {
  it('renders exactly the four tabs in order and no Sources/Topics/Sentiment tab', async () => {
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, () =>
        HttpResponse.json(makeVisibility(AUDIT_LATEST, 67)),
      ),
    ]);
    renderPage();

    const tablist = await screen.findByRole('tablist', { name: 'Visibility views' });
    const tabs = within(tablist).getAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual([
      'Overview',
      'Trends',
      'Mentions & Citations',
      'Query Fanout',
    ]);
    // The forbidden tab labels are absent.
    expect(within(tablist).queryByRole('tab', { name: 'Sources' })).toBeNull();
    expect(within(tablist).queryByRole('tab', { name: 'Topics' })).toBeNull();
    expect(within(tablist).queryByRole('tab', { name: 'Sentiment' })).toBeNull();
  });

  it('opens on Overview by default and renders exactly one active panel', async () => {
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, () =>
        HttpResponse.json(makeVisibility(AUDIT_LATEST, 67)),
      ),
    ]);
    renderPage();

    const overviewTab = await screen.findByRole('tab', { name: 'Overview' });
    expect(overviewTab).toHaveAttribute('aria-selected', 'true');
    // Exactly one panel is rendered.
    expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
    expect(await screen.findByLabelText('Visibility score: 67%')).toBeInTheDocument();
  });

  it('falls back to Overview for an invalid ?tab= value', async () => {
    currentSearch = new URLSearchParams('tab=sources');
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, () =>
        HttpResponse.json(makeVisibility(AUDIT_LATEST, 67)),
      ),
    ]);
    renderPage();

    expect(await screen.findByRole('tab', { name: 'Overview' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('reads the active tab from ?tab= on load (refresh/deeplink)', async () => {
    currentSearch = new URLSearchParams('tab=trends');
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/trends`, () =>
        HttpResponse.json([
          makeTrendPoint(AUDIT_LATEST, '2026-07-15T00:00:00Z', 67),
          makeTrendPoint(AUDIT_OLDER, '2026-07-10T00:00:00Z', 55),
        ]),
      ),
    ]);
    renderPage();

    expect(await screen.findByRole('tab', { name: 'Trends' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('syncs ?tab= via router.replace when a tab is clicked', async () => {
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, () =>
        HttpResponse.json(makeVisibility(AUDIT_LATEST, 67)),
      ),
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/trends`, () => HttpResponse.json([])),
    ]);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole('tab', { name: 'Overview' });
    await user.click(screen.getByRole('tab', { name: 'Trends' }));

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith(expect.stringContaining('tab=trends')),
    );
  });

  it('supports keyboard Arrow/Home/End navigation with focus transfer', async () => {
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, () =>
        HttpResponse.json(makeVisibility(AUDIT_LATEST, 67)),
      ),
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/trends`, () => HttpResponse.json([])),
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/evidence`, () =>
        HttpResponse.json(makeEvidenceResponse()),
      ),
    ]);
    const user = userEvent.setup();
    renderPage();

    const overviewTab = await screen.findByRole('tab', { name: 'Overview' });
    overviewTab.focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Trends' })).toHaveAttribute('aria-selected', 'true');

    await user.keyboard('{End}');
    expect(screen.getByRole('tab', { name: 'Query Fanout' })).toHaveAttribute(
      'aria-selected',
      'true',
    );

    // Wraps forward from the last tab back to the first.
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');

    await user.keyboard('{Home}');
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
  });

  it('exposes a horizontally scrollable tablist for narrow viewports', async () => {
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, () =>
        HttpResponse.json(makeVisibility(AUDIT_LATEST, 67)),
      ),
    ]);
    renderPage();

    const tablist = await screen.findByRole('tablist', { name: 'Visibility views' });
    expect(tablist.className).toContain('overflow-x-auto');
    expect(tablist.className).toContain('flex-nowrap');
  });
});

describe('VisibilityPage — Overview (unchanged behavior)', () => {
  it('renders the score and per-engine comparison from data', async () => {
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, () =>
        HttpResponse.json(makeVisibility(AUDIT_LATEST, 67)),
      ),
    ]);
    renderPage();

    expect(await screen.findByLabelText('Visibility score: 67%')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Per-engine comparison' })).toBeInTheDocument();
    expect(screen.getByText('Gemini')).toBeInTheDocument();
    expect(screen.getByText('Claude')).toBeInTheDocument();
  });

  it('sorts the rankings table with brand + competitors and renders the placeholders', async () => {
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, () =>
        HttpResponse.json(makeVisibility(AUDIT_LATEST, 67)),
      ),
    ]);
    renderPage();

    const rankings = (await screen.findByRole('heading', { name: 'Rankings' })).closest('section')!;
    const bodyRows = within(rankings).getAllByRole('row').slice(1);
    expect(within(bodyRows[0]).getByText('Acme')).toBeInTheDocument();
    expect(within(bodyRows[0]).getByText('You')).toBeInTheDocument();
    expect(within(bodyRows[1]).getByText('Globex')).toBeInTheDocument();
    expect(within(bodyRows[0]).getAllByText('—').length).toBeGreaterThanOrEqual(2);
  });

  it('changes the query when a different run is selected', async () => {
    const seen: (string | null)[] = [];
    useBaseHandlers([
      http.get('/api/v1/audits', () =>
        HttpResponse.json([
          makeAudit(AUDIT_LATEST, '2026-07-15T00:00:00Z'),
          makeAudit(AUDIT_OLDER, '2026-07-10T00:00:00Z'),
        ]),
      ),
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, ({ request }) => {
        const auditId = new URL(request.url).searchParams.get('audit_id');
        seen.push(auditId);
        return HttpResponse.json(makeVisibility(auditId ?? AUDIT_LATEST, auditId === AUDIT_OLDER ? 42 : 67));
      }),
    ]);
    // makeAudit sets status completed; override the base audits handler above.
    mswServer.use(
      http.get('/api/v1/audits', () =>
        HttpResponse.json([
          makeAudit(AUDIT_LATEST, '2026-07-15T00:00:00Z'),
          makeAudit(AUDIT_OLDER, '2026-07-10T00:00:00Z'),
        ]),
      ),
    );
    const user = userEvent.setup();
    renderPage();

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

    await waitFor(() => expect(screen.getByLabelText('Visibility score: 42%')).toBeInTheDocument());
    expect(seen).toContain(AUDIT_OLDER);
  });

  it('narrows the per-engine comparison when an engine filter is applied', async () => {
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, () =>
        HttpResponse.json(makeVisibility(AUDIT_LATEST, 67)),
      ),
    ]);
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
    // No tablist is rendered in the empty state.
    expect(screen.queryByRole('tablist')).toBeNull();
  });
});

describe('VisibilityPage — per-tab query enablement + cache reuse', () => {
  it('only fetches the selected-run projection on Overview (not trends/evidence)', async () => {
    let visibilityCalls = 0;
    let trendCalls = 0;
    let evidenceCalls = 0;
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, () => {
        visibilityCalls += 1;
        return HttpResponse.json(makeVisibility(AUDIT_LATEST, 67));
      }),
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/trends`, () => {
        trendCalls += 1;
        return HttpResponse.json([]);
      }),
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/evidence`, () => {
        evidenceCalls += 1;
        return HttpResponse.json(makeEvidenceResponse());
      }),
    ]);
    renderPage();

    await screen.findByLabelText('Visibility score: 67%');
    expect(visibilityCalls).toBe(1);
    expect(trendCalls).toBe(0);
    expect(evidenceCalls).toBe(0);
  });

  it('reuses one evidence request across the two evidence tabs', async () => {
    let evidenceCalls = 0;
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, () =>
        HttpResponse.json(makeVisibility(AUDIT_LATEST, 67)),
      ),
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/evidence`, () => {
        evidenceCalls += 1;
        return HttpResponse.json(makeEvidenceResponse());
      }),
    ]);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole('tab', { name: 'Overview' });
    await user.click(screen.getByRole('tab', { name: 'Mentions & Citations' }));
    expect(await screen.findByText('Best affordable clothing stores in Australia?')).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: 'Query Fanout' }));
    // The Query Fanout panel renders from the same cached response.
    expect(await screen.findByText('affordable family clothing Australia 2026')).toBeInTheDocument();

    // One shared evidence request only — no duplicate fetch on tab switch.
    await waitFor(() => expect(evidenceCalls).toBe(1));
  });
});

describe('VisibilityPage — Trends tab', () => {
  it('renders the trend charts and sends granularity + date bounds', async () => {
    currentSearch = new URLSearchParams('tab=trends');
    const params: URL[] = [];
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/trends`, ({ request }) => {
        params.push(new URL(request.url));
        return HttpResponse.json([
          makeTrendPoint(AUDIT_OLDER, '2026-07-10T00:00:00Z', 55),
          makeTrendPoint(AUDIT_LATEST, '2026-07-15T00:00:00Z', 67),
        ]);
      }),
    ]);
    renderPage();

    expect(await screen.findByTestId('trend-chart-visibility_score')).toBeInTheDocument();
    expect(screen.getByTestId('trend-chart-sov')).toBeInTheDocument();
    // Default granularity=run and a bounded 90d `from` are sent.
    expect(params[0].searchParams.get('granularity')).toBe('run');
    expect(params[0].searchParams.get('from')).toBeTruthy();
  });

  it('renders the single-point info state', async () => {
    currentSearch = new URLSearchParams('tab=trends');
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/trends`, () =>
        HttpResponse.json([makeTrendPoint(AUDIT_LATEST, '2026-07-15T00:00:00Z', 67)]),
      ),
    ]);
    renderPage();

    expect(await screen.findByText(/only one completed run is in range/i)).toBeInTheDocument();
  });

  it('renders a null trend metric as a chart gap, never a zero', async () => {
    currentSearch = new URLSearchParams('tab=trends');
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/trends`, () =>
        HttpResponse.json([
          makeTrendPoint(AUDIT_OLDER, '2026-07-10T00:00:00Z', 55),
          makeTrendPoint('11111111-1111-4111-8111-1111111111ab', '2026-07-12T00:00:00Z', null),
          makeTrendPoint(AUDIT_LATEST, '2026-07-15T00:00:00Z', 67),
        ]),
      ),
    ]);
    renderPage();

    const scoreChart = await screen.findByTestId('trend-chart-visibility_score');
    const svg = within(scoreChart).getByRole('img');
    expect(svg.getAttribute('aria-label')).toContain('unavailable and shown as gaps');
    // The null point draws no dot: only the two available points do.
    expect(scoreChart.querySelectorAll('circle.fill-accent')).toHaveLength(2);
  });

  it('renders the retryable error state', async () => {
    currentSearch = new URLSearchParams('tab=trends');
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/trends`, () =>
        HttpResponse.json({ detail: 'boom' }, { status: 400 }),
      ),
    ]);
    renderPage();

    expect(
      await screen.findByText(/could not load the visibility trend/i),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});

describe('VisibilityPage — Mentions & Citations tab', () => {
  it('renders persisted mentions, classified citations, and provenance', async () => {
    currentSearch = new URLSearchParams('tab=mentions-citations');
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/evidence`, () =>
        HttpResponse.json(makeEvidenceResponse()),
      ),
    ]);
    renderPage();

    expect(await screen.findByText('Best affordable clothing stores in Australia?')).toBeInTheDocument();
    // Mentions render as classification badges.
    expect(screen.getByText('Acme')).toBeInTheDocument();
    expect(screen.getByText('Globex')).toBeInTheDocument();
    // Classified citation is shown.
    expect(screen.getByText('Acme Blog')).toBeInTheDocument();
    // Provenance line includes task/analysis.
    expect(screen.getByText(/Provenance: task/)).toBeInTheDocument();
    // No generated-query list on this tab.
    expect(screen.queryByText('affordable family clothing Australia 2026')).toBeNull();
  });

  it('sends the audit/prompt/engine params and shows the truncation notice', async () => {
    currentSearch = new URLSearchParams('tab=mentions-citations');
    let captured: URL | null = null;
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/evidence`, ({ request }) => {
        captured = new URL(request.url);
        return HttpResponse.json(makeEvidenceResponse({ truncated: true }));
      }),
    ]);
    renderPage();

    await screen.findByText('Best affordable clothing stores in Australia?');
    // audit_id defaults to the latest run; engine defaults to all (omitted).
    expect(captured!.searchParams.get('audit_id')).toBe(AUDIT_LATEST);
    expect(captured!.searchParams.get('limit')).toBe('100');
    expect(screen.getByText(/Showing newest 100 executions/)).toBeInTheDocument();
  });

  it('renders the empty state when there is no persisted evidence and no narrowing filter', async () => {
    currentSearch = new URLSearchParams('tab=mentions-citations');
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/evidence`, () =>
        HttpResponse.json({ items: [], truncated: false }),
      ),
    ]);
    const user = userEvent.setup();
    renderPage();

    // The default range preset (90d) counts as a narrowing filter; widen it so
    // the genuinely-empty (not filtered-empty) state is exercised.
    await user.click(await screen.findByRole('button', { name: 'Select date range' }));
    await user.click(await screen.findByRole('menuitem', { name: 'All time' }));

    expect(await screen.findByText('No mentions or citations yet')).toBeInTheDocument();
  });

  it('renders the filtered-empty state with a clear-filters action', async () => {
    currentSearch = new URLSearchParams('tab=mentions-citations');
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/evidence`, () =>
        HttpResponse.json({ items: [], truncated: false }),
      ),
    ]);
    renderPage();

    // Default range preset (90d) is a narrowing filter, so this is filtered-empty.
    expect(await screen.findByText('No results match these filters')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Clear filters' })).toBeInTheDocument();
  });

  it('renders the retryable error state', async () => {
    currentSearch = new URLSearchParams('tab=mentions-citations');
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/evidence`, () =>
        HttpResponse.json({ detail: 'boom' }, { status: 400 }),
      ),
    ]);
    renderPage();

    expect(await screen.findByText(/Couldn't load this evidence/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});

describe('VisibilityPage — Query Fanout tab', () => {
  it('renders actual query text, count-only, and no-search states distinctly', async () => {
    currentSearch = new URLSearchParams('tab=query-fanout');
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/evidence`, () =>
        HttpResponse.json({
          items: [
            makeEvidenceItem(),
            makeEvidenceItem({
              analysis_id: ANALYSIS_B,
              logical_engine: 'claude',
              transport_model: 'claude-sonnet-4-6',
              state: 'count_only',
              query_text_available: false,
              search_query_count: 1,
              search_events: [],
              event_source: 'audit_task',
              artifact_id: null,
              mentions: [],
              citations: [],
            }),
            makeEvidenceItem({
              analysis_id: ANALYSIS_C,
              prompt_index: 2,
              logical_engine: 'gemini',
              transport_model: 'gemini-flash-latest',
              state: 'no_search',
              search_used: false,
              search_query_count: 0,
              query_text_available: false,
              search_events: [],
              event_source: 'none',
              mentions: [],
              citations: [],
            }),
          ],
          truncated: false,
        }),
      ),
    ]);
    renderPage();

    // Actual query text.
    expect(await screen.findByText('affordable family clothing Australia 2026')).toBeInTheDocument();
    // Count-only legacy explanation.
    expect(screen.getByText('Query text unavailable; provider reported 1 search')).toBeInTheDocument();
    // No-search state.
    expect(screen.getByText('No web searches performed for this execution')).toBeInTheDocument();
    // No duplicated citation browser here.
    expect(screen.queryByText('Acme Blog')).toBeNull();
  });

  it('groups executions by frozen prompt without claiming a global total', async () => {
    currentSearch = new URLSearchParams('tab=query-fanout');
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/evidence`, () =>
        HttpResponse.json(makeEvidenceResponse()),
      ),
    ]);
    renderPage();

    // The prompt heading appears once as the group header.
    expect(await screen.findByRole('heading', { name: 'Best affordable clothing stores in Australia?' })).toBeInTheDocument();
    expect(screen.getByText('1 prompt')).toBeInTheDocument();
  });
});

describe('VisibilityPage — shared filter persistence', () => {
  it('keeps the selected engine when switching tabs', async () => {
    const evidenceEngines: (string | null)[] = [];
    useBaseHandlers([
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility`, () =>
        HttpResponse.json(makeVisibility(AUDIT_LATEST, 67)),
      ),
      http.get(`/api/v1/projects/${PROJECT_ID}/visibility/evidence`, ({ request }) => {
        evidenceEngines.push(new URL(request.url).searchParams.get('engine'));
        return HttpResponse.json(makeEvidenceResponse());
      }),
    ]);
    const user = userEvent.setup();
    renderPage();

    await screen.findByLabelText('Visibility score: 67%');
    // Pick an engine on Overview.
    await user.click(screen.getByRole('button', { name: 'Filter by engine' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Gemini' }));

    // Switch to an evidence tab; the engine filter carries over into the query.
    await user.click(screen.getByRole('tab', { name: 'Mentions & Citations' }));
    await screen.findByText('Best affordable clothing stores in Australia?');
    await waitFor(() => expect(evidenceEngines).toContain('gemini'));
  });
});
