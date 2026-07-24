import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import type { Project } from '@/lib/api/types';
import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

const WS = '11111111-1111-4111-8111-111111111111';
const PROJECT = '88888888-8888-4888-8888-888888888888';
const GRANT_GOOGLE = '22222222-2222-4222-8222-222222222222';
const CONN_GSC = '33333333-3333-4333-8333-333333333333';
const CONN_GA4 = '44444444-4444-4444-8444-444444444444';
const SYNC_GSC = '77777777-7777-4777-8777-777777777777';
const SYNC_GA4 = '99999999-9999-4999-8999-999999999999';

const activeProject = {
  id: PROJECT,
  workspace_id: WS,
  name: 'Acme Running',
  brand_name: 'Acme',
} as unknown as Project;

vi.mock('@/lib/project/project-context', () => ({
  useProjectContext: () => ({
    projects: [activeProject],
    activeProject,
    activeProjectId: activeProject.id,
    setActiveProjectId: vi.fn(),
    isLoading: false,
  }),
}));

import { TrafficScreen } from './traffic-screen';

const DASHBOARD_URL = `/api/v1/projects/${PROJECT}/traffic`;

// Fixture shapes from the F1/F2 contract suites (lib/api/integrations.test.ts,
// lib/api/traffic.test.ts): the wire CTR is a persisted 0–1 fraction (the UI
// scales it onto 0–100), and null bucket values are chart gaps, never
// invented zeros.
function point(date: string, value: number | null) {
  return { date, value };
}

const dashboardPayload = {
  project_id: PROJECT,
  window_start: '2026-07-21',
  window_end: '2026-07-23',
  granularity: 'day',
  totals: {
    impressions: 1162000,
    clicks: 36400,
    ctr: 0.0317,
    position: 8.4,
    sessions: 41208,
    conversions: 1386,
  },
  series: {
    impressions: [
      point('2026-07-21', 49200),
      point('2026-07-22', null),
      point('2026-07-23', 51800),
    ],
    clicks: [point('2026-07-21', 1320), point('2026-07-22', 1401), point('2026-07-23', 1463)],
    ctr: [point('2026-07-22', 0.0297), point('2026-07-23', 0.0317)],
    position: [point('2026-07-22', 8.9), point('2026-07-23', 8.3)],
    sessions: [
      point('2026-07-21', 18200),
      point('2026-07-22', null),
      point('2026-07-23', 19404),
    ],
    conversions: [point('2026-07-22', 612), point('2026-07-23', 640)],
  },
  formula_version: 'traffic-formula-1',
  normalization_version: 'traffic-normalization-1',
};

// The absent-snapshot shape: every series empty, totals zeroed/null, window
// echoed — the read endpoints never recompute.
const emptyPayload = {
  ...dashboardPayload,
  totals: { impressions: 0, clicks: 0, ctr: null, position: null, sessions: null, conversions: null },
  series: { impressions: [], clicks: [], ctr: [], position: [], sessions: [], conversions: [] },
};

function connection(overrides: Record<string, unknown> = {}) {
  return {
    id: CONN_GSC,
    workspace_id: WS,
    grant_id: GRANT_GOOGLE,
    provider: 'gsc',
    label: 'acme-running GSC',
    account_ref: 'sc-domain:acme-running.example.com',
    grant_status: 'connected',
    granted_scopes: ['https://www.googleapis.com/auth/webmasters.readonly'],
    last_synced_at: '2026-07-23T18:14:00Z',
    created_at: '2026-07-20T00:00:00Z',
    updated_at: '2026-07-23T18:14:00Z',
    ...overrides,
  };
}

function syncRun(overrides: Record<string, unknown> = {}) {
  return {
    id: SYNC_GSC,
    connection_id: CONN_GSC,
    sync_kind: 'on_demand',
    status: 'queued',
    window_start: '2026-07-21',
    window_end: '2026-07-23',
    row_count: 0,
    resync_seq: 1,
    error_code: '',
    error_detail: '',
    created_at: '2026-07-23T18:15:00Z',
    updated_at: '2026-07-23T18:15:00Z',
    completed_at: null,
    ...overrides,
  };
}

/** Dashboard handler echoing the requested granularity; returns the seen URLs. */
function mockDashboard(payload: Record<string, unknown> = dashboardPayload) {
  const seen: URL[] = [];
  mswServer.use(
    http.get(DASHBOARD_URL, ({ request }) => {
      const url = new URL(request.url);
      seen.push(url);
      const granularity = url.searchParams.get('granularity') ?? 'day';
      return HttpResponse.json({ ...payload, granularity });
    }),
  );
  return seen;
}

function mockConnections(items: Record<string, unknown>[]) {
  mswServer.use(http.get('/api/v1/integrations', () => HttpResponse.json(items)));
}

/** The two keyset tables render beneath a populated dashboard — one row each. */
function mockTables() {
  mswServer.use(
    http.get(`${DASHBOARD_URL}/pages`, () =>
      HttpResponse.json({
        items: [
          {
            canonical_url: 'https://acme-running.example.com/blog/best-trail-running-shoes',
            site_url_id: null,
            impressions: 84210,
            clicks: 3204,
            ctr: 0.038,
            position: 4.2,
            sessions: 3102,
            conversions: 88,
          },
        ],
        next_cursor: null,
      }),
    ),
    http.get(`${DASHBOARD_URL}/queries`, () =>
      HttpResponse.json({
        items: [
          {
            normalized_query: 'best trail running shoes',
            impressions: 12204,
            clicks: 986,
            ctr: 0.081,
            position: 2.4,
          },
        ],
        next_cursor: null,
      }),
    ),
  );
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('TrafficScreen — populated dashboard', () => {
  it('renders the toolbar, six stat cards, four trend cards, and both tables', async () => {
    const seen = mockDashboard();
    mockConnections([
      connection(),
      connection({
        id: CONN_GA4,
        provider: 'ga4',
        label: 'acme-running GA4',
        account_ref: 'properties/123456789',
        granted_scopes: ['https://www.googleapis.com/auth/analytics.readonly'],
        last_synced_at: '2026-07-22T10:00:00Z',
      }),
    ]);
    mockTables();
    renderWithProviders(<TrafficScreen />);

    // Toolbar: the latest-window preset + day granularity selected, mono note.
    const toolbar = await screen.findByTestId('traffic-toolbar');
    expect(
      within(toolbar).getByRole('button', { name: 'Select date range' }),
    ).toHaveTextContent('Latest synced window');
    const group = within(toolbar).getByRole('radiogroup', { name: 'Snapshot granularity' });
    expect(within(group).getByRole('radio', { name: 'Day' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(
      within(toolbar).getByText('Last synced Jul 23, 2026 · 18:14 UTC'),
    ).toBeInTheDocument();

    // The default mode sends no window bounds — granularity only.
    await waitFor(() => expect(seen.length).toBeGreaterThan(0));
    expect(seen[0].searchParams.get('granularity')).toBe('day');
    expect(seen[0].searchParams.get('from')).toBeNull();
    expect(seen[0].searchParams.get('to')).toBeNull();

    // Six headline stat cards (mono totals + prior-bucket deltas; the wire CTR
    // fraction renders as a percent, the position delta inverts its tone).
    const stats = await screen.findByTestId('traffic-stats');
    expect(within(stats).getByTestId('stat-impressions')).toHaveTextContent('1,162,000');
    expect(within(stats).getByTestId('stat-impressions')).toHaveTextContent(
      '+5.3% vs. prior day',
    );
    expect(within(stats).getByTestId('stat-clicks')).toHaveTextContent('36,400');
    expect(within(stats).getByTestId('stat-ctr')).toHaveTextContent('3.17%');
    expect(within(stats).getByTestId('stat-ctr')).toHaveTextContent('+0.2 pts vs. prior day');
    expect(within(stats).getByTestId('stat-position')).toHaveTextContent('8.4');
    expect(within(stats).getByTestId('stat-position')).toHaveTextContent('−0.6 vs. prior day');
    expect(within(stats).getByTestId('stat-sessions')).toHaveTextContent('41,208');
    expect(within(stats).getByTestId('stat-conversions')).toHaveTextContent('1,386');

    // Four trend cards: CTR stays on the fixed 0–100% scale; impressions get a
    // truthful count domain (51,800 max → 60K ceiling).
    const ctrCard = await screen.findByTestId('trend-chart-ctr');
    expect(within(ctrCard).getByText('Click-through rate · 0–100% scale')).toBeInTheDocument();
    expect(within(ctrCard).getByText('100%')).toBeInTheDocument();
    const impressionsCard = screen.getByTestId('trend-chart-impressions');
    expect(
      within(impressionsCard).getByText('Google Search Console · daily'),
    ).toBeInTheDocument();
    expect(within(impressionsCard).getByText('60K')).toBeInTheDocument();
    expect(screen.getByTestId('trend-chart-clicks')).toBeInTheDocument();
    expect(screen.getByTestId('trend-chart-average-position')).toBeInTheDocument();

    // Both keyset tables render beneath the charts.
    expect(await screen.findByTestId('pages-table')).toBeInTheDocument();
    expect(await screen.findByTestId('queries-table')).toBeInTheDocument();
    expect(await screen.findByText('best trail running shoes')).toBeInTheDocument();
  });

  it('refetches with the requested granularity when the segmented control changes', async () => {
    const seen = mockDashboard();
    mockConnections([connection()]);
    mockTables();
    const ue = userEvent.setup();
    renderWithProviders(<TrafficScreen />);

    await screen.findByTestId('traffic-stats');
    await waitFor(() =>
      expect(seen.some((url) => url.searchParams.get('granularity') === 'day')).toBe(true),
    );

    await ue.click(screen.getByRole('radio', { name: 'Week' }));
    await waitFor(() =>
      expect(seen.some((url) => url.searchParams.get('granularity') === 'week')).toBe(true),
    );

    await ue.click(screen.getByRole('radio', { name: 'Month' }));
    await waitFor(() =>
      expect(seen.some((url) => url.searchParams.get('granularity') === 'month')).toBe(true),
    );
    // The delta copy follows the selected bucket's noun.
    expect(await screen.findByTestId('stat-impressions')).toHaveTextContent('vs. prior month');
  });
});

describe('TrafficScreen — empty + bounded-miss states', () => {
  it('renders the connect-CTA empty state (no toolbar) when nothing has synced and no connections exist', async () => {
    mockDashboard(emptyPayload);
    mockConnections([]);
    renderWithProviders(<TrafficScreen />);

    expect(await screen.findByText('Connect search data to see traffic')).toBeInTheDocument();
    const cta = screen.getByRole('link', { name: 'Connect an integration' });
    expect(cta).toHaveAttribute('href', '/settings?tab=integrations');
    expect(screen.queryByTestId('traffic-toolbar')).not.toBeInTheDocument();
    expect(screen.queryByTestId('traffic-stats')).not.toBeInTheDocument();
    expect(screen.queryByTestId('pages-table')).not.toBeInTheDocument();
  });

  it('switches the empty-state copy when connections already exist', async () => {
    mockDashboard(emptyPayload);
    mockConnections([connection()]);
    renderWithProviders(<TrafficScreen />);

    expect(await screen.findByText('Your first sync is on its way')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open integrations' })).toHaveAttribute(
      'href',
      '/settings?tab=integrations',
    );
  });

  it('shows the honest no-snapshot note when a bounded range matches no persisted window', async () => {
    const seen: URL[] = [];
    mswServer.use(
      http.get(DASHBOARD_URL, ({ request }) => {
        const url = new URL(request.url);
        seen.push(url);
        const bounded = url.searchParams.has('from') && url.searchParams.has('to');
        return HttpResponse.json({
          ...(bounded ? emptyPayload : dashboardPayload),
          granularity: url.searchParams.get('granularity') ?? 'day',
        });
      }),
    );
    mockConnections([connection()]);
    mockTables();
    const ue = userEvent.setup();
    renderWithProviders(<TrafficScreen />);

    // Populated landing in latest mode, then switch to a bounded preset.
    await screen.findByTestId('traffic-stats');
    await ue.click(screen.getByRole('button', { name: 'Select date range' }));
    await ue.click(await screen.findByRole('menuitem', { name: 'Last 28 days' }));

    // The unmatched window surfaces the honest note (never a recomputation);
    // the toolbar stays so the user can switch back.
    expect(await screen.findByText(/No synced snapshot covers/)).toBeInTheDocument();
    expect(screen.getByText(/Traffic serves persisted sync windows only/)).toBeInTheDocument();
    expect(screen.getByTestId('traffic-toolbar')).toBeInTheDocument();
    expect(screen.queryByTestId('traffic-stats')).not.toBeInTheDocument();
    await waitFor(() =>
      expect(
        seen.some((url) => url.searchParams.has('from') && url.searchParams.has('to')),
      ).toBe(true),
    );
  });
});

describe('TrafficScreen — sync now', () => {
  it('enqueues runs, polls them until terminal, then invalidates the traffic queries', async () => {
    let dashboardCalls = 0;
    let postCalls = 0;
    const pollCalls: Record<string, number> = { [SYNC_GSC]: 0, [SYNC_GA4]: 0 };
    const statuses: Record<string, string> = { [SYNC_GSC]: 'running', [SYNC_GA4]: 'queued' };
    mswServer.use(
      http.get(DASHBOARD_URL, ({ request }) => {
        dashboardCalls += 1;
        const granularity = new URL(request.url).searchParams.get('granularity') ?? 'day';
        return HttpResponse.json({ ...dashboardPayload, granularity });
      }),
      // C3: the 202 carries a bare array of {sync_run_id, connection_id, status}.
      http.post(`${DASHBOARD_URL}/sync`, () => {
        postCalls += 1;
        return HttpResponse.json(
          [
            { sync_run_id: SYNC_GSC, connection_id: CONN_GSC, status: 'queued' },
            { sync_run_id: SYNC_GA4, connection_id: CONN_GA4, status: 'queued' },
          ],
          { status: 202 },
        );
      }),
      http.get(`/api/v1/integrations/${CONN_GSC}/syncs/${SYNC_GSC}`, () => {
        pollCalls[SYNC_GSC] += 1;
        return HttpResponse.json(
          syncRun({
            status: statuses[SYNC_GSC],
            completed_at: statuses[SYNC_GSC] === 'succeeded' ? '2026-07-23T18:17:00Z' : null,
          }),
        );
      }),
      http.get(`/api/v1/integrations/${CONN_GA4}/syncs/${SYNC_GA4}`, () => {
        pollCalls[SYNC_GA4] += 1;
        return HttpResponse.json(
          syncRun({
            id: SYNC_GA4,
            connection_id: CONN_GA4,
            status: statuses[SYNC_GA4],
            completed_at: statuses[SYNC_GA4] === 'succeeded' ? '2026-07-23T18:17:00Z' : null,
          }),
        );
      }),
    );
    mockConnections([
      connection(),
      connection({ id: CONN_GA4, provider: 'ga4', account_ref: 'properties/123456789' }),
    ]);
    mockTables();
    const ue = userEvent.setup();
    const { queryClient } = renderWithProviders(<TrafficScreen />);

    await screen.findByTestId('traffic-stats');
    await ue.click(screen.getByTestId('sync-now-button'));

    // The enqueue POST fired once; the banner shows and Sync now disables.
    await waitFor(() => expect(postCalls).toBe(1));
    expect(await screen.findByTestId('sync-status-banner')).toBeInTheDocument();
    expect(screen.getByTestId('sync-now-button')).toBeDisabled();
    expect(
      within(screen.getByTestId('traffic-toolbar')).getByText(/^Started /),
    ).toBeInTheDocument();

    // Both enqueued runs are polled while non-terminal.
    await waitFor(() => {
      expect(pollCalls[SYNC_GSC]).toBeGreaterThan(0);
      expect(pollCalls[SYNC_GA4]).toBeGreaterThan(0);
    });

    // Both runs finish server-side; the next poll lands the terminal statuses,
    // the banner clears to the success alert, and the dashboard refetches the
    // new projection. (F5 idiom: invalidate to force the poll tick instead of
    // waiting out the 3s interval.)
    const callsAtTerminal = dashboardCalls;
    statuses[SYNC_GSC] = 'succeeded';
    statuses[SYNC_GA4] = 'succeeded';
    await queryClient.invalidateQueries();
    expect(
      await screen.findByText('Sync complete — charts and tables now render the new snapshot.'),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('sync-status-banner')).not.toBeInTheDocument();
    await waitFor(() => expect(dashboardCalls).toBeGreaterThan(callsAtTerminal));
    expect(screen.getByTestId('sync-now-button')).toBeEnabled();
  });

  it('surfaces a notice when the project has no active mapped connection to sync', async () => {
    let postCalls = 0;
    mswServer.use(
      http.get(DASHBOARD_URL, ({ request }) => {
        const granularity = new URL(request.url).searchParams.get('granularity') ?? 'day';
        return HttpResponse.json({ ...dashboardPayload, granularity });
      }),
      http.post(`${DASHBOARD_URL}/sync`, () => {
        postCalls += 1;
        return HttpResponse.json([], { status: 202 });
      }),
    );
    mockConnections([connection()]);
    mockTables();
    const ue = userEvent.setup();
    renderWithProviders(<TrafficScreen />);

    await screen.findByTestId('traffic-stats');
    await ue.click(screen.getByTestId('sync-now-button'));

    expect(
      await screen.findByText(/No active Search Console or GA4 connection is mapped/),
    ).toBeInTheDocument();
    await waitFor(() => expect(postCalls).toBe(1));
    // No runs enqueued → no banner, and the button returns to its idle state.
    expect(screen.queryByTestId('sync-status-banner')).not.toBeInTheDocument();
    expect(screen.getByTestId('sync-now-button')).toBeEnabled();
  });
});
