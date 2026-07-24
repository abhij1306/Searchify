import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';

import { analyticsApi } from './analytics';
import { queryKeys } from './query-keys';
import { analyticsReferralRowSchema, llmAnalyticsSchema, strictValidate } from './schemas';
import { mswServer } from '@/test/msw-server';

const PROJECT = '11111111-1111-4111-8111-111111111111';
const REFERRAL = '22222222-2222-4222-8222-222222222222';

// Fixture shapes come from the approved plan
// (docs/integrations-traffic-analytics.md contract C4) + the analytics
// spec (docs/roadmap/llm-analytics.md §6): referral volume/share series,
// per-source breakdown, per-engine visibility series, correlation summary,
// keyset referrals envelope, theme rollup.
const headline = {
  project_id: PROJECT,
  window_start: '2026-07-16',
  window_end: '2026-07-22',
  granularity: 'day' as const,
  referral_volume: [
    { date: '2026-07-16', value: 12 },
    { date: '2026-07-17', value: null },
  ],
  referral_share: [
    { date: '2026-07-16', value: 4.2 },
    { date: '2026-07-17', value: null },
  ],
  sources: [
    { ai_source: 'chatgpt' as const, sessions: 34, share: 62.9 },
    { ai_source: 'perplexity' as const, sessions: 20, share: 37.1 },
  ],
  engine_visibility: [
    {
      logical_engine: 'chatgpt',
      series: [
        { date: '2026-07-16', value: 71 },
        { date: '2026-07-17', value: 74 },
      ],
    },
  ],
  correlation: { state: 'ok' as const, coefficient: 0.64, sample_size: 21 },
  analyzer_version: 'b6-analysis-1',
  formula_version: 'analytics-formula-1',
};

const referralRow = {
  id: REFERRAL,
  occurred_at: '2026-07-21T14:03:00Z',
  landing_url: 'https://searchify.io/blog/aeo-guide',
  referrer_host: 'chatgpt.com',
  is_ai_referral: true,
  ai_source: 'chatgpt' as const,
  logical_engine: 'chatgpt',
  confidence: 'exact' as const,
  match_signal: 'referrer' as const,
};

const themeRow = {
  theme: 'AEO tooling',
  intent: 'comparison' as const,
  total_completed: 18,
  brand_mention_rate: 0.61,
  visibility_score: 58.2,
  share_of_voice: 0.44,
};

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('analyticsApi.getAnalytics', () => {
  it('sends the window params and validates the headline projection', async () => {
    let seenUrl = '';
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/llm-analytics`, ({ request }) => {
        seenUrl = request.url;
        return HttpResponse.json(headline);
      }),
    );
    const result = await analyticsApi.getAnalytics(PROJECT, {
      from: '2026-07-16',
      to: '2026-07-22',
      granularity: 'day',
    });
    expect(result.correlation.state).toBe('ok');
    expect(result.correlation.coefficient).toBeCloseTo(0.64);
    expect(result.sources[0].ai_source).toBe('chatgpt');
    expect(result.engine_visibility[0].series).toHaveLength(2);
    // Nullable series points survive as gaps, never zeros.
    expect(result.referral_volume[1].value).toBeNull();
    const url = new URL(seenUrl);
    expect(url.searchParams.get('from')).toBe('2026-07-16');
    expect(url.searchParams.get('granularity')).toBe('day');
  });

  it('parses an insufficient_data correlation (null coefficient, small sample)', async () => {
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/llm-analytics`, () =>
        HttpResponse.json({
          ...headline,
          correlation: { state: 'insufficient_data', coefficient: null, sample_size: 3 },
        }),
      ),
    );
    const result = await analyticsApi.getAnalytics(PROJECT);
    expect(result.correlation).toEqual({
      state: 'insufficient_data',
      coefficient: null,
      sample_size: 3,
    });
  });
});

describe('analyticsApi.getReferrals', () => {
  it('validates the keyset envelope and sends source + window + cursor (C4)', async () => {
    let seenUrl = '';
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/llm-analytics/referrals`, ({ request }) => {
        seenUrl = request.url;
        return HttpResponse.json({ items: [referralRow], next_cursor: 'cursor-2' });
      }),
    );
    const page = await analyticsApi.getReferrals(PROJECT, {
      source: 'chatgpt',
      from: '2026-07-16',
      to: '2026-07-22',
      cursor: 'cursor-1',
    });
    expect(page.items).toHaveLength(1);
    expect(page.items[0].referrer_host).toBe('chatgpt.com');
    expect(page.items[0].confidence).toBe('exact');
    expect(page.next_cursor).toBe('cursor-2');
    const url = new URL(seenUrl);
    expect(url.searchParams.get('source')).toBe('chatgpt');
    expect(url.searchParams.get('cursor')).toBe('cursor-1');
  });

  it('accepts a non-AI referral row (other source, null engine + signal)', async () => {
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/llm-analytics/referrals`, () =>
        HttpResponse.json({
          items: [
            {
              ...referralRow,
              is_ai_referral: false,
              ai_source: 'other',
              logical_engine: null,
              confidence: 'heuristic',
              match_signal: null,
            },
          ],
          next_cursor: null,
        }),
      ),
    );
    const page = await analyticsApi.getReferrals(PROJECT);
    expect(page.items[0].ai_source).toBe('other');
    expect(page.items[0].match_signal).toBeNull();
  });
});

describe('analyticsApi.getThemes', () => {
  it('validates the theme rollup array and sends the window', async () => {
    let seenUrl = '';
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/llm-analytics/themes`, ({ request }) => {
        seenUrl = request.url;
        return HttpResponse.json([themeRow]);
      }),
    );
    const rows = await analyticsApi.getThemes(PROJECT, { from: '2026-07-16', to: '2026-07-22' });
    expect(rows).toHaveLength(1);
    expect(rows[0].theme).toBe('AEO tooling');
    expect(rows[0].intent).toBe('comparison');
    const url = new URL(seenUrl);
    expect(url.searchParams.get('from')).toBe('2026-07-16');
  });
});

describe('analytics schemas (drift policy)', () => {
  it('rejects an unknown ai_source', () => {
    expect(() =>
      strictValidate(analyticsReferralRowSchema, { ...referralRow, ai_source: 'bard' }, 'test'),
    ).toThrow(/test/);
  });

  it('rejects an unknown correlation state', () => {
    expect(() =>
      strictValidate(
        llmAnalyticsSchema,
        { ...headline, correlation: { state: 'estimated', coefficient: 0.5, sample_size: 4 } },
        'test',
      ),
    ).toThrow(/test/);
  });

  it('rejects an extra key on the headline projection', () => {
    expect(() =>
      strictValidate(llmAnalyticsSchema, { ...headline, prediction: 'up' }, 'test'),
    ).toThrow(/test/);
  });
});

describe('analytics query keys', () => {
  it('includes the project id and filters in every key', () => {
    expect(queryKeys.analytics.all).toEqual(['analytics']);
    expect(queryKeys.analytics.dashboard(PROJECT, { granularity: 'month' })).toEqual([
      'analytics',
      'dashboard',
      PROJECT,
      { granularity: 'month' },
    ]);
    expect(queryKeys.analytics.referrals(PROJECT, { source: 'gemini' })).toEqual([
      'analytics',
      'referrals',
      PROJECT,
      { source: 'gemini' },
    ]);
    expect(queryKeys.analytics.themes(PROJECT)).toEqual(['analytics', 'themes', PROJECT, {}]);
  });
});
