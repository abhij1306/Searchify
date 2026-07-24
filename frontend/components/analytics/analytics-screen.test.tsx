import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import type { Project } from '@/lib/api/types';
import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

const WS = '11111111-1111-4111-8111-111111111111';
const PROJECT = '88888888-8888-4888-8888-888888888888';

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

import { AnalyticsScreen } from './analytics-screen';

// Fixture shapes from the F3 contract suite (lib/api/analytics.test.ts) and
// the A9 backend DTOs: share/rate values are persisted 0–1 fractions the UI
// scales onto the 0–100 display scale; null bucket values are chart gaps.
const headline = {
  project_id: PROJECT,
  window_start: '2026-07-06',
  window_end: '2026-07-20',
  granularity: 'week' as const,
  referral_volume: [
    { date: '2026-07-06', value: 98 },
    { date: '2026-07-13', value: 141 },
    { date: '2026-07-20', value: 247 },
  ],
  referral_share: [
    { date: '2026-07-06', value: 0.026 },
    { date: '2026-07-13', value: 0.031 },
    { date: '2026-07-20', value: 0.046 },
  ],
  sources: [
    { ai_source: 'chatgpt' as const, sessions: 62, share: 0.62 },
    { ai_source: 'perplexity' as const, sessions: 38, share: 0.38 },
  ],
  engine_visibility: [
    {
      logical_engine: 'claude',
      series: [
        { date: '2026-07-13', value: 40 },
        { date: '2026-07-20', value: 54 },
      ],
    },
    {
      logical_engine: 'chatgpt',
      series: [
        { date: '2026-07-13', value: 71 },
        { date: '2026-07-20', value: 78 },
      ],
    },
    {
      logical_engine: 'gemini',
      series: [
        { date: '2026-07-13', value: null },
        { date: '2026-07-20', value: 66 },
      ],
    },
  ],
  correlation: { state: 'ok' as const, coefficient: 0.68, sample_size: 12 },
  analyzer_version: 'b6-analysis-1',
  formula_version: 'analytics-formula-1',
};

const themeRow = {
  theme: 'Trail running',
  intent: 'discovery' as const,
  total_completed: 8,
  brand_mention_rate: 0.46,
  visibility_score: 78,
  share_of_voice: 0.35,
};

const referralRow = {
  id: '22222222-2222-4222-8222-222222222222',
  occurred_at: '2026-07-23T20:41:00Z',
  landing_url: 'https://acme-running.example.com/blog/best-trail-running-shoes',
  referrer_host: 'chatgpt.com',
  is_ai_referral: true,
  ai_source: 'chatgpt' as const,
  logical_engine: 'chatgpt',
  confidence: 'exact' as const,
  match_signal: 'referrer' as const,
};

const ANALYTICS_URL = `/api/v1/projects/${PROJECT}/llm-analytics`;

/** Register the three read endpoints; returns the seen headline URLs. */
function mockEndpoints(headlinePayload: Record<string, unknown> = headline) {
  const seen: string[] = [];
  mswServer.use(
    http.get(ANALYTICS_URL, ({ request }) => {
      seen.push(request.url);
      // Echo the requested granularity so bucket-count badges track the control.
      const granularity = new URL(request.url).searchParams.get('granularity') ?? 'week';
      return HttpResponse.json({ ...headlinePayload, granularity });
    }),
    http.get(`${ANALYTICS_URL}/themes`, () => HttpResponse.json([themeRow])),
    http.get(`${ANALYTICS_URL}/referrals`, () =>
      HttpResponse.json({ items: [referralRow], next_cursor: null }),
    ),
  );
  return seen;
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('AnalyticsScreen — populated dashboard', () => {
  it('renders the toolbar, trend cards, donut, correlation, engines, themes, and referrals', async () => {
    mockEndpoints();
    renderWithProviders(<AnalyticsScreen />);

    // Toolbar (default latest-snapshot range + week granularity selected).
    const toolbar = await screen.findByTestId('analytics-toolbar');
    expect(within(toolbar).getByRole('button', { name: 'Select date range' })).toHaveTextContent(
      'Latest synced window',
    );
    const group = within(toolbar).getByRole('radiogroup', { name: 'Granularity' });
    expect(within(group).getByRole('radio', { name: 'Week' })).toHaveAttribute(
      'aria-checked',
      'true',
    );

    // Trend cards: volume carries the bucket-count badge; share is fixed 0–100%.
    expect(await screen.findByText('AI-referral volume')).toBeInTheDocument();
    expect(screen.getByText('3 weeks')).toBeInTheDocument();
    expect(screen.getByText('Referral share')).toBeInTheDocument();

    // Per-source donut: sessions-descending legend + grouped center total.
    expect(screen.getByText('AI referrals by source')).toBeInTheDocument();
    expect(screen.getByText('62%')).toBeInTheDocument();
    expect(screen.getByText('38%')).toBeInTheDocument();
    expect(screen.getByText('100')).toBeInTheDocument();

    // Correlation (ok): coefficient + sample size, descriptive framing.
    expect(screen.getByText('r = 0.68')).toBeInTheDocument();
    expect(screen.getByText('n = 12 weekly buckets')).toBeInTheDocument();
    expect(screen.getByText(/Descriptive — not a forecast/)).toBeInTheDocument();

    // Cross-engine tiles in canonical order with latest scores.
    const engineCard = screen.getByText('Cross-engine visibility').closest('section');
    expect(engineCard).not.toBeNull();
    const tiles = within(engineCard as HTMLElement).getAllByText(/^(ChatGPT|Gemini|Claude)$/);
    expect(tiles.map((node) => node.textContent)).toEqual(['ChatGPT', 'Gemini', 'Claude']);
    expect(within(engineCard as HTMLElement).getByText('78')).toBeInTheDocument();
    expect(within(engineCard as HTMLElement).getByText('66')).toBeInTheDocument();
    expect(within(engineCard as HTMLElement).getByText('54')).toBeInTheDocument();

    // Theme rollup row (0–1 rates rendered as percents).
    expect(await screen.findByText('Trail running')).toBeInTheDocument();
    expect(screen.getByText('discovery')).toBeInTheDocument();
    expect(screen.getByText('46%')).toBeInTheDocument();
    expect(screen.getByText('35%')).toBeInTheDocument();

    // Referrals drill-down row.
    expect(await screen.findByText('chatgpt.com')).toBeInTheDocument();
    expect(screen.getByText('acme-running.example.com')).toBeInTheDocument();
    expect(screen.getByText('/blog/best-trail-running-shoes')).toBeInTheDocument();
  });

  it('re-derives the dashboard when granularity or range changes', async () => {
    const seen = mockEndpoints();
    const user = userEvent.setup();
    renderWithProviders(<AnalyticsScreen />);

    await screen.findByText('AI-referral volume');
    expect(seen).toHaveLength(1);
    expect(new URL(seen[0]).searchParams.get('granularity')).toBe('week');

    await user.click(screen.getByRole('radio', { name: 'Day' }));
    await waitFor(() =>
      expect(seen.some((url) => new URL(url).searchParams.get('granularity') === 'day')).toBe(true),
    );
    expect(await screen.findByText('3 days')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Select date range' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Last 30 days' }));
    const params = (url: string) => new URL(url).searchParams;
    const fromValues = () => seen.map((url) => params(url).get('from'));
    // Bounded presets send the both-or-neither `from`+`to` UTC-date window
    // the analytics API binds — never a from-only ISO datetime.
    await waitFor(() => expect(fromValues().some((value) => value !== null)).toBe(true));
    for (const url of seen.filter((candidate) => params(candidate).get('from') !== null)) {
      expect(params(url).get('from')).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(params(url).get('to')).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    }

    await user.click(screen.getByRole('button', { name: 'Select date range' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Last 90 days' }));
    await waitFor(() => expect(new Set(fromValues().filter(Boolean)).size).toBeGreaterThan(1));
  });
});

describe('AnalyticsScreen — insufficient_data correlation', () => {
  it('renders the neutral badge + em-dash and never a fabricated coefficient', async () => {
    mockEndpoints({
      ...headline,
      correlation: { state: 'insufficient_data', coefficient: null, sample_size: 6 },
    });
    renderWithProviders(<AnalyticsScreen />);

    expect(await screen.findByText('Insufficient data')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText(/r =/)).not.toBeInTheDocument();
    expect(screen.getByText(/at least 8 aligned weekly buckets/)).toBeInTheDocument();
    expect(screen.getByText(/6 of 8 collected so far/)).toBeInTheDocument();
  });
});

describe('AnalyticsScreen — empty + error states', () => {
  it('renders only the empty state when no analytics evidence exists', async () => {
    mockEndpoints({
      ...headline,
      referral_volume: [],
      referral_share: [],
      sources: [],
      engine_visibility: [],
      correlation: { state: 'insufficient_data', coefficient: null, sample_size: 0 },
    });
    renderWithProviders(<AnalyticsScreen />);

    expect(await screen.findByText('No AI-referral data yet')).toBeInTheDocument();
    expect(screen.queryByTestId('analytics-toolbar')).not.toBeInTheDocument();
    const cta = screen.getByRole('link', { name: 'Open integration settings' });
    expect(cta).toHaveAttribute('href', '/settings?tab=integrations');
  });

  it('renders a retryable error alert when the headline request fails', async () => {
    mswServer.use(http.get(ANALYTICS_URL, () => new HttpResponse(null, { status: 500 })));
    renderWithProviders(<AnalyticsScreen />);

    // The client's bounded 5xx retry policy runs before the query errors.
    expect(await screen.findByRole('alert', {}, { timeout: 8000 })).toHaveTextContent(
      /Could not load LLM analytics/,
    );
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});
