import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';

import { queryKeys } from './query-keys';
import { strictValidate, trafficQueryRowSchema, trafficTotalsSchema } from './schemas';
import { trafficApi } from './traffic';
import { mswServer } from '@/test/msw-server';

const PROJECT = '11111111-1111-4111-8111-111111111111';
const SITE_URL = '22222222-2222-4222-8222-222222222222';
const CONN = '33333333-3333-4333-8333-333333333333';
const CONN2 = '44444444-4444-4444-8444-444444444444';
const SYNC = '55555555-5555-4555-8555-555555555555';
const SYNC2 = '66666666-6666-4666-8666-666666666666';

// Fixture shapes come from the approved plan
// (docs/integrations-traffic-analytics.md contracts C3/C4) + the traffic
// spec (docs/roadmap/traffic.md §6): totals + dated series with NULLABLE
// points (chart gaps), keyset envelopes, per-run 202 enqueue objects.
const dashboard = {
  project_id: PROJECT,
  window_start: '2026-07-16',
  window_end: '2026-07-22',
  granularity: 'day' as const,
  totals: {
    impressions: 1250,
    clicks: 86,
    ctr: 0.0688,
    position: 14.2,
    sessions: 64,
    conversions: 3,
  },
  series: {
    impressions: [
      { date: '2026-07-16', value: 180 },
      { date: '2026-07-17', value: null },
    ],
    clicks: [
      { date: '2026-07-16', value: 12 },
      { date: '2026-07-17', value: null },
    ],
    ctr: [
      { date: '2026-07-16', value: 0.0667 },
      { date: '2026-07-17', value: null },
    ],
    position: [
      { date: '2026-07-16', value: 13.9 },
      { date: '2026-07-17', value: null },
    ],
    sessions: [
      { date: '2026-07-16', value: 9 },
      { date: '2026-07-17', value: null },
    ],
    conversions: [
      { date: '2026-07-16', value: 1 },
      { date: '2026-07-17', value: null },
    ],
  },
  formula_version: 'traffic-formula-1',
  normalization_version: 'traffic-normalization-1',
};

const pageRow = {
  canonical_url: 'https://searchify.io/blog/aeo-guide',
  site_url_id: SITE_URL,
  impressions: 420,
  clicks: 31,
  ctr: 0.0738,
  position: 12.4,
  sessions: 25,
  conversions: 2,
};

const queryRow = {
  normalized_query: 'aeo guide',
  impressions: 96,
  clicks: 8,
  ctr: 0.0833,
  position: 9.1,
};

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('trafficApi.getTraffic', () => {
  it('sends the window params and validates the dashboard projection', async () => {
    let seenUrl = '';
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/traffic`, ({ request }) => {
        seenUrl = request.url;
        return HttpResponse.json(dashboard);
      }),
    );
    const result = await trafficApi.getTraffic(PROJECT, {
      from: '2026-07-16',
      to: '2026-07-22',
      granularity: 'day',
    });
    expect(result.totals.impressions).toBe(1250);
    expect(result.totals.ctr).toBeCloseTo(0.0688);
    const url = new URL(seenUrl);
    expect(url.searchParams.get('from')).toBe('2026-07-16');
    expect(url.searchParams.get('to')).toBe('2026-07-22');
    expect(url.searchParams.get('granularity')).toBe('day');
  });

  it('preserves nullable series points (chart gaps), never coercing to zero', async () => {
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/traffic`, () => HttpResponse.json(dashboard)),
    );
    const result = await trafficApi.getTraffic(PROJECT);
    expect(result.series.impressions[1]).toEqual({ date: '2026-07-17', value: null });
    expect(result.series.position[0].value).toBeCloseTo(13.9);
  });

  it('accepts an empty-history payload (empty series, null rate totals)', async () => {
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/traffic`, () =>
        HttpResponse.json({
          ...dashboard,
          totals: {
            impressions: 0,
            clicks: 0,
            ctr: null,
            position: null,
            sessions: null,
            conversions: null,
          },
          series: {
            impressions: [],
            clicks: [],
            ctr: [],
            position: [],
            sessions: [],
            conversions: [],
          },
        }),
      ),
    );
    const result = await trafficApi.getTraffic(PROJECT);
    expect(result.totals.ctr).toBeNull();
    expect(result.series.clicks).toEqual([]);
  });
});

describe('trafficApi.getPages / getQueries', () => {
  it('validates the keyset page envelope and sends sort + cursor (C4)', async () => {
    let seenUrl = '';
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/traffic/pages`, ({ request }) => {
        seenUrl = request.url;
        return HttpResponse.json({ items: [pageRow], next_cursor: 'cursor-2' });
      }),
    );
    const page = await trafficApi.getPages(PROJECT, {
      from: '2026-07-16',
      to: '2026-07-22',
      sort: '-impressions',
      cursor: 'cursor-1',
    });
    expect(page.items).toHaveLength(1);
    expect(page.items[0].canonical_url).toBe('https://searchify.io/blog/aeo-guide');
    expect(page.items[0].site_url_id).toBe(SITE_URL);
    expect(page.next_cursor).toBe('cursor-2');
    const url = new URL(seenUrl);
    expect(url.searchParams.get('sort')).toBe('-impressions');
    expect(url.searchParams.get('cursor')).toBe('cursor-1');
  });

  it('validates the keyset query envelope (queries have no GA4 metrics)', async () => {
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/traffic/queries`, () =>
        HttpResponse.json({ items: [queryRow], next_cursor: null }),
      ),
    );
    const page = await trafficApi.getQueries(PROJECT, { from: '2026-07-16', to: '2026-07-22' });
    expect(page.items[0].normalized_query).toBe('aeo guide');
    expect(page.items[0].position).toBeCloseTo(9.1);
    expect(page.next_cursor).toBeNull();
  });

  it('fails loud when a page row carries an extra key (e.g. a leaked total)', async () => {
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/traffic/pages`, () =>
        HttpResponse.json({ items: [{ ...pageRow, total_count: 9000 }], next_cursor: null }),
      ),
    );
    await expect(trafficApi.getPages(PROJECT)).rejects.toThrow(/traffic.getPages/);
  });
});

describe('trafficApi.syncNow', () => {
  it('validates the 202 per-run enqueue array (C3)', async () => {
    mswServer.use(
      http.post(`/api/v1/projects/${PROJECT}/traffic/sync`, () =>
        HttpResponse.json(
          [
            { sync_run_id: SYNC, connection_id: CONN, status: 'queued' },
            { sync_run_id: SYNC2, connection_id: CONN2, status: 'queued' },
          ],
          { status: 202 },
        ),
      ),
    );
    const runs = await trafficApi.syncNow(PROJECT);
    // One object per queued run: the client polls each via
    // integrationsApi.getSync(connection_id, sync_run_id).
    expect(runs).toHaveLength(2);
    expect(runs[0]).toEqual({ sync_run_id: SYNC, connection_id: CONN, status: 'queued' });
    expect(runs[1].connection_id).toBe(CONN2);
  });

  it('accepts an empty run array (no active mapped connections)', async () => {
    mswServer.use(
      http.post(`/api/v1/projects/${PROJECT}/traffic/sync`, () =>
        HttpResponse.json([], { status: 202 }),
      ),
    );
    await expect(trafficApi.syncNow(PROJECT)).resolves.toEqual([]);
  });
});

describe('traffic schemas (drift policy)', () => {
  it('rejects a fractional impressions total', () => {
    expect(() =>
      strictValidate(trafficTotalsSchema, { ...dashboard.totals, impressions: 12.5 }, 'test'),
    ).toThrow(/test/);
  });

  it('rejects sessions on a query row (GSC-only metrics)', () => {
    expect(() =>
      strictValidate(trafficQueryRowSchema, { ...queryRow, sessions: 3 }, 'test'),
    ).toThrow(/test/);
  });
});

describe('traffic query keys', () => {
  it('includes the project id and filters in every key', () => {
    expect(queryKeys.traffic.all).toEqual(['traffic']);
    expect(queryKeys.traffic.dashboard(PROJECT, { granularity: 'week' })).toEqual([
      'traffic',
      'dashboard',
      PROJECT,
      { granularity: 'week' },
    ]);
    expect(queryKeys.traffic.pages(PROJECT, { cursor: 'c1' })).toEqual([
      'traffic',
      'pages',
      PROJECT,
      { cursor: 'c1' },
    ]);
    expect(queryKeys.traffic.queries(PROJECT)).toEqual(['traffic', 'queries', PROJECT, {}]);
  });
});
