import { describe, expect, it } from 'vitest';

import type { TrafficDashboard } from '@/lib/api/traffic';

import {
  bucketAdverb,
  countAxisTicks,
  countDomainMax,
  describeSort,
  formatCount,
  formatCountTick,
  formatCtr,
  formatPosition,
  formatSeriesLabel,
  formatSyncTimestamp,
  isEmptyDashboard,
  rangeLabel,
  rangeToWindow,
  sortDirection,
  sortKey,
  splitUrlParts,
  toChartPoints,
  toggleSort,
  trafficStats,
  type TrafficSeriesPoint,
} from './traffic';

const NOW = new Date('2026-07-23T18:14:00Z');

function point(date: string, value: number | null): TrafficSeriesPoint {
  return { date, value };
}

function dashboard(overrides: Record<string, unknown> = {}): TrafficDashboard {
  return {
    project_id: '11111111-1111-4111-8111-111111111111',
    window_start: '2026-06-24',
    window_end: '2026-07-23',
    granularity: 'day',
    totals: {
      impressions: 1162000,
      clicks: 36838,
      ctr: 0.0317,
      position: 8.4,
      sessions: 41208,
      conversions: 1386,
    },
    series: {
      impressions: [point('2026-07-22', 49200), point('2026-07-23', 51800)],
      clicks: [point('2026-07-22', 1554), point('2026-07-23', 1642)],
      ctr: [point('2026-07-22', 0.0297), point('2026-07-23', 0.0317)],
      position: [point('2026-07-22', 8.9), point('2026-07-23', 8.3)],
      sessions: [point('2026-07-22', 1700), point('2026-07-23', 1902)],
      conversions: [point('2026-07-22', 58), point('2026-07-23', 64)],
    },
    formula_version: 'traffic-formula-1',
    normalization_version: 'traffic-normalization-1',
    ...overrides,
  } as TrafficDashboard;
}

describe('rangeToWindow', () => {
  it('sends no bounds for the default latest-synced-window preset', () => {
    expect(rangeToWindow('latest', NOW)).toEqual({});
  });

  it('resolves bounded presets into UTC from/to dates', () => {
    expect(rangeToWindow('7d', NOW)).toEqual({ from: '2026-07-16', to: '2026-07-23' });
    expect(rangeToWindow('28d', NOW)).toEqual({ from: '2026-06-25', to: '2026-07-23' });
    expect(rangeToWindow('90d', NOW)).toEqual({ from: '2026-04-24', to: '2026-07-23' });
  });

  it('labels every preset', () => {
    expect(rangeLabel('latest')).toBe('Latest synced window');
    expect(rangeLabel('28d')).toBe('Last 28 days');
  });
});

describe('toChartPoints', () => {
  it('maps dates to short labels and keeps values as-is by default', () => {
    const points = toChartPoints([point('2026-06-24', 36200), point('2026-07-23', 51800)]);
    expect(points).toEqual([
      { label: 'Jun 24', value: 36200 },
      { label: 'Jul 23', value: 51800 },
    ]);
  });

  it('scales persisted fractions onto the 0–100 domain when percent is set', () => {
    const points = toChartPoints([point('2026-07-23', 0.0317)], { percent: true });
    expect(points[0].value).toBeCloseTo(3.17, 5);
  });

  it('preserves null values as chart gaps — never coerced to zero', () => {
    const points = toChartPoints([point('2026-07-08', null), point('2026-07-09', 10)]);
    expect(points[0].value).toBeNull();
    expect(points[1].value).toBe(10);
  });
});

describe('countDomainMax + countAxisTicks', () => {
  it('ceils to the smallest nice-step domain above the series max', () => {
    expect(countDomainMax([point('2026-07-01', 51800)])).toBe(60000);
    expect(countDomainMax([point('2026-07-01', 1642)])).toBe(2000);
    expect(countDomainMax([point('2026-07-01', 10)])).toBe(10);
    expect(countDomainMax([point('2026-07-01', 950)])).toBe(1000);
  });

  it('ignores nulls and falls back to a minimal domain when empty/zero', () => {
    expect(countDomainMax([point('2026-07-01', null)])).toBe(10);
    expect(countDomainMax([])).toBe(10);
    expect(countDomainMax([point('2026-07-01', 0)])).toBe(10);
  });

  it('formats compact axis ticks (60K domain and 2K domain)', () => {
    expect(countAxisTicks(60000)).toEqual(['60K', '45K', '30K', '15K', '0']);
    expect(countAxisTicks(2000)).toEqual(['2K', '1.5K', '1K', '500', '0']);
  });

  it('formatCountTick handles sub-1K and fractional-K values', () => {
    expect(formatCountTick(0)).toBe('0');
    expect(formatCountTick(500)).toBe('500');
    expect(formatCountTick(1500)).toBe('1.5K');
    expect(formatCountTick(45000)).toBe('45K');
  });
});

describe('display formatters', () => {
  it('formats counts with grouping, CTR fractions as percents, positions at 1dp', () => {
    expect(formatCount(1162000)).toBe('1,162,000');
    expect(formatCtr(0.0317, 2)).toBe('3.17%');
    expect(formatCtr(0.038)).toBe('3.8%');
    expect(formatPosition(8.44)).toBe('8.4');
  });

  it('formats series labels + sync timestamps in explicit UTC', () => {
    expect(formatSeriesLabel('2026-06-24')).toBe('Jun 24');
    expect(formatSyncTimestamp('2026-07-23T18:14:00Z')).toBe('Jul 23, 2026 · 18:14 UTC');
  });

  it('bucketAdverb names the card subtitle cadence', () => {
    expect(bucketAdverb('day')).toBe('daily');
    expect(bucketAdverb('week')).toBe('weekly');
    expect(bucketAdverb('month')).toBe('monthly');
  });
});

describe('trafficStats', () => {
  it('builds the six headline cards with deltas vs the prior available bucket', () => {
    const stats = trafficStats(dashboard());
    expect(stats.map((s) => s.key)).toEqual([
      'impressions',
      'clicks',
      'ctr',
      'position',
      'sessions',
      'conversions',
    ]);
    const byKey = Object.fromEntries(stats.map((s) => [s.key, s]));
    expect(byKey.impressions.value).toBe('1,162,000');
    expect(byKey.impressions.delta).toBe('+5.3% vs. prior day');
    expect(byKey.impressions.tone).toBe('up');
    expect(byKey.ctr.value).toBe('3.17%');
    expect(byKey.ctr.delta).toBe('+0.2 pts vs. prior day');
    expect(byKey.position.value).toBe('8.4');
    // A LOWER position is favorable: the tone inverts.
    expect(byKey.position.delta).toBe('−0.6 vs. prior day');
    expect(byKey.position.tone).toBe('up');
    expect(byKey.sessions.value).toBe('41,208');
    expect(byKey.conversions.value).toBe('1,386');
  });

  it('renders null GA4 metrics as the em-dash placeholder with a note', () => {
    const stats = trafficStats(
      dashboard({
        totals: {
          impressions: 100,
          clicks: 4,
          ctr: 0.04,
          position: 3.2,
          sessions: null,
          conversions: null,
        },
      }),
    );
    const byKey = Object.fromEntries(stats.map((s) => [s.key, s]));
    expect(byKey.sessions.value).toBe('—');
    expect(byKey.sessions.placeholder).toBe(true);
    expect(byKey.sessions.delta).toBe('No GA4 data in window');
    expect(byKey.conversions.value).toBe('—');
  });

  it('renders null ctr/position as placeholders (zero-impression window)', () => {
    const stats = trafficStats(
      dashboard({
        totals: {
          impressions: 0,
          clicks: 0,
          ctr: null,
          position: null,
          sessions: 10,
          conversions: 1,
        },
      }),
    );
    const byKey = Object.fromEntries(stats.map((s) => [s.key, s]));
    expect(byKey.ctr.value).toBe('—');
    expect(byKey.position.value).toBe('—');
  });

  it('notes the missing baseline when a series has fewer than two available points', () => {
    const stats = trafficStats(
      dashboard({
        series: {
          impressions: [point('2026-07-23', 100)],
          clicks: [point('2026-07-22', null), point('2026-07-23', 5)],
          ctr: [],
          position: [],
          sessions: [],
          conversions: [],
        },
      }),
    );
    const byKey = Object.fromEntries(stats.map((s) => [s.key, s]));
    expect(byKey.impressions.delta).toBe('No prior day');
    expect(byKey.impressions.tone).toBe('flat');
    expect(byKey.clicks.delta).toBe('No prior day');
  });

  it('uses the month noun at month granularity', () => {
    const stats = trafficStats(dashboard({ granularity: 'month' }));
    expect(stats[0].delta).toContain('vs. prior month');
  });
});

describe('isEmptyDashboard', () => {
  it('is true only when every series is empty (the absent-snapshot payload)', () => {
    const empty = dashboard({
      window_start: '',
      window_end: '',
      totals: {
        impressions: 0,
        clicks: 0,
        ctr: null,
        position: null,
        sessions: null,
        conversions: null,
      },
      series: { impressions: [], clicks: [], ctr: [], position: [], sessions: [], conversions: [] },
    });
    expect(isEmptyDashboard(empty)).toBe(true);
    expect(isEmptyDashboard(dashboard())).toBe(false);
  });
});

describe('sort helpers', () => {
  it('parses the leading-dash direction idiom', () => {
    expect(sortDirection('-clicks')).toBe('descending');
    expect(sortDirection('clicks')).toBe('ascending');
    expect(sortKey('-clicks')).toBe('clicks');
  });

  it('describes the active sort in the footer-note voice', () => {
    expect(describeSort('-clicks')).toBe('Sorted by clicks, descending');
    expect(describeSort('ctr')).toBe('Sorted by CTR, ascending');
  });

  it('sorts a new column descending first and toggles the active column', () => {
    expect(toggleSort('-clicks', 'impressions')).toBe('-impressions');
    expect(toggleSort('-clicks', 'clicks')).toBe('clicks');
    expect(toggleSort('clicks', 'clicks')).toBe('-clicks');
  });
});

describe('splitUrlParts', () => {
  it('splits a full URL into a muted host + path', () => {
    expect(splitUrlParts('https://acme-running.example.com/blog/best-trail-running-shoes')).toEqual(
      { host: 'acme-running.example.com', rest: '/blog/best-trail-running-shoes' },
    );
  });

  it('keeps the root path for a bare homepage', () => {
    expect(splitUrlParts('https://acme.com/')).toEqual({ host: 'acme.com', rest: '/' });
  });

  it('handles scheme-less hosts and non-URL values', () => {
    expect(splitUrlParts('acme.com/blog')).toEqual({ host: 'acme.com', rest: '/blog' });
    expect(splitUrlParts('not a url')).toEqual({ host: '', rest: 'not a url' });
  });
});
