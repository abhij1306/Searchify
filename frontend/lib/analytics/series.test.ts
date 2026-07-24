import { describe, expect, it } from 'vitest';

import { bucketAdjective, bucketAdjectiveTitle, bucketCountLabel, rangeToWindow } from './options';
import {
  CORRELATION_MIN_SAMPLE,
  aiSourceLabel,
  correlationDisplay,
  countDomainMax,
  countYLabels,
  formatBucketDate,
  formatInt,
  formatOccurredAt,
  formatPercent,
  isAnalyticsEmpty,
  latestValue,
  sortEngineVisibility,
  sourceSegments,
  splitLandingUrl,
  toCountChartPoints,
  toPercentChartPoints,
  totalSourceSessions,
} from './series';
import type { LlmAnalytics } from '@/lib/api/analytics';

describe('analytics options vocabulary', () => {
  it('resolves range presets to UTC date window bounds (latest sends none)', () => {
    const now = new Date('2026-07-24T12:00:00Z');
    expect(rangeToWindow('latest', now)).toEqual({});
    expect(rangeToWindow('30d', now)).toEqual({ from: '2026-06-24', to: '2026-07-24' });
    expect(rangeToWindow('90d', now)).toEqual({ from: '2026-04-25', to: '2026-07-24' });
    expect(rangeToWindow('1y', now)).toEqual({ from: '2025-07-24', to: '2026-07-24' });
  });

  it('labels buckets per granularity', () => {
    expect(bucketAdjective('day')).toBe('daily');
    expect(bucketAdjective('week')).toBe('weekly');
    expect(bucketAdjective('month')).toBe('monthly');
    expect(bucketAdjectiveTitle('week')).toBe('Weekly');
    expect(bucketCountLabel('week', 13)).toBe('13 weeks');
    expect(bucketCountLabel('day', 1)).toBe('1 day');
    expect(bucketCountLabel('month', 4)).toBe('4 months');
  });
});

describe('chart point mapping', () => {
  it('maps count series with nulls preserved as gaps', () => {
    expect(
      toCountChartPoints([
        { date: '2026-04-27', value: 98.4 },
        { date: '2026-05-04', value: null },
      ]),
    ).toEqual([
      { label: 'Apr 27', value: 98 },
      { label: 'May 4', value: null },
    ]);
  });

  it('scales 0–1 share fractions onto the 0–100% chart scale', () => {
    expect(
      toPercentChartPoints([
        { date: '2026-04-27', value: 0.026 },
        { date: '2026-05-04', value: null },
      ]),
    ).toEqual([
      { label: 'Apr 27', value: 2.6 },
      { label: 'May 4', value: null },
    ]);
  });

  it('formats bucket dates field-wise (no timezone day-shift)', () => {
    expect(formatBucketDate('2026-04-27')).toBe('Apr 27');
    expect(formatBucketDate('2026-12-01')).toBe('Dec 1');
    expect(formatBucketDate('not-a-date')).toBe('not-a-date');
  });
});

describe('countDomainMax + countYLabels', () => {
  it('never drops below the 100 default and ceilings counts truthfully', () => {
    expect(countDomainMax([])).toBe(100);
    expect(countDomainMax([42])).toBe(100);
    expect(countDomainMax([247])).toBe(300);
    expect(countDomainMax([1150])).toBe(2000);
  });

  it('spaces five integer labels from the ceiling to zero', () => {
    expect(countYLabels(100)).toEqual(['100', '75', '50', '25', '0']);
    expect(countYLabels(300)).toEqual(['300', '225', '150', '75', '0']);
  });
});

describe('latestValue', () => {
  it('returns the last available value, skipping trailing gaps', () => {
    expect(
      latestValue([
        { date: 'a', value: 1 },
        { date: 'b', value: null },
      ]),
    ).toBe(1);
    expect(latestValue([{ date: 'a', value: null }])).toBeNull();
    expect(latestValue([])).toBeNull();
  });
});

describe('formatting helpers', () => {
  it('groups integers for the donut center', () => {
    expect(formatInt(1847)).toBe('1,847');
    expect(formatInt(207)).toBe('207');
  });

  it('formats 0–1 fractions as percents with an em-dash for null', () => {
    expect(formatPercent(0.624)).toBe('62%');
    expect(formatPercent(0.026, 1)).toBe('2.6%');
    expect(formatPercent(null)).toBe('—');
  });

  it('formats occurred_at timestamps and passes drift through', () => {
    expect(formatOccurredAt('2026-07-23T20:41:00Z')).toMatch(/Jul 23, 2026/);
    expect(formatOccurredAt('garbage')).toBe('garbage');
  });

  it('splits landing URLs into host + path, falling back to the raw string', () => {
    expect(splitLandingUrl('https://acme-running.example.com/blog/trail?x=1')).toEqual({
      host: 'acme-running.example.com',
      rest: '/blog/trail?x=1',
    });
    expect(splitLandingUrl('not a url')).toEqual({ host: '', rest: 'not a url' });
  });
});

describe('source breakdown', () => {
  const sources = [
    { ai_source: 'perplexity' as const, sessions: 38, share: 0.38 },
    { ai_source: 'chatgpt' as const, sessions: 62, share: 0.62 },
  ];

  it('builds donut segments sessions-descending with token color classes', () => {
    const segments = sourceSegments(sources);
    expect(segments.map((segment) => segment.label)).toEqual(['ChatGPT', 'Perplexity']);
    expect(segments[0]).toMatchObject({ value: 62, colorClass: 'stroke-accent' });
    expect(segments[1]).toMatchObject({ value: 38, colorClass: 'stroke-citation-owned' });
  });

  it('sums the donut center total', () => {
    expect(totalSourceSessions(sources)).toBe(100);
  });

  it('labels every AI source', () => {
    expect(aiSourceLabel('google_ai_overview')).toBe('Google AI Overview');
    expect(aiSourceLabel('other')).toBe('Other');
  });
});

describe('sortEngineVisibility', () => {
  it('orders engines canonically, unknown engines last', () => {
    const sorted = sortEngineVisibility([
      { logical_engine: 'claude', series: [] },
      { logical_engine: 'copilot', series: [] },
      { logical_engine: 'chatgpt', series: [] },
      { logical_engine: 'gemini', series: [] },
    ]);
    expect(sorted.map((row) => row.logical_engine)).toEqual([
      'chatgpt',
      'gemini',
      'claude',
      'copilot',
    ]);
  });
});

describe('correlationDisplay', () => {
  it('frames an ok coefficient as descriptive, not a forecast', () => {
    const display = correlationDisplay({ state: 'ok', coefficient: 0.68, sample_size: 12 }, 'week');
    expect(display.value).toBe('r = 0.68');
    expect(display.insufficient).toBe(false);
    expect(display.badge).toBe('n = 12 weekly buckets');
    expect(display.description).toContain('Descriptive — not a forecast');
  });

  it('renders insufficient_data as an em-dash with the sample progress, never a number', () => {
    const display = correlationDisplay(
      { state: 'insufficient_data', coefficient: null, sample_size: 6 },
      'week',
    );
    expect(display.value).toBe('—');
    expect(display.insufficient).toBe(true);
    expect(display.badge).toBe('Insufficient data');
    expect(display.description).toContain(
      `at least ${CORRELATION_MIN_SAMPLE} aligned weekly buckets`,
    );
    expect(display.description).toContain(`6 of ${CORRELATION_MIN_SAMPLE} collected so far`);
    expect(display.description).not.toContain('r =');
  });

  it('explains a zero-variance insufficient state without fake progress', () => {
    const display = correlationDisplay(
      { state: 'insufficient_data', coefficient: null, sample_size: 12 },
      'month',
    );
    expect(display.value).toBe('—');
    expect(display.description).toContain('monthly');
    expect(display.description).not.toContain('collected so far');
  });

  it('never trusts an ok state with a null coefficient', () => {
    const display = correlationDisplay({ state: 'ok', coefficient: null, sample_size: 12 }, 'day');
    expect(display.value).toBe('—');
    expect(display.insufficient).toBe(true);
  });
});

describe('isAnalyticsEmpty', () => {
  const base = {
    project_id: '11111111-1111-4111-8111-111111111111',
    window_start: '',
    window_end: '',
    granularity: 'week' as const,
    referral_volume: [],
    referral_share: [],
    sources: [],
    engine_visibility: [],
    correlation: { state: 'insufficient_data' as const, coefficient: null, sample_size: 0 },
    analyzer_version: 'b6-analysis-1',
    formula_version: 'analytics-formula-1',
  };

  it('is empty only when referral, source, and visibility evidence are all absent', () => {
    expect(isAnalyticsEmpty(base as LlmAnalytics)).toBe(true);
    expect(
      isAnalyticsEmpty({
        ...base,
        referral_volume: [{ date: '2026-07-20', value: 0 }],
      } as LlmAnalytics),
    ).toBe(false);
    expect(
      isAnalyticsEmpty({
        ...base,
        engine_visibility: [{ logical_engine: 'chatgpt', series: [] }],
      } as LlmAnalytics),
    ).toBe(false);
  });
});
