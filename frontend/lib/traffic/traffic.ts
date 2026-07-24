/**
 * Traffic dashboard helpers (F6/F7).
 *
 * Pure, framework-free helpers the `/traffic` screen uses to turn the backend
 * `TrafficDashboard` projection + paged stat rows into chart series, headline
 * stat cards, sort ids, and display strings. The read endpoints are the single
 * source of truth; nothing here recomputes a metric — it only projects
 * persisted values for display (invariant 7). Null metrics stay null and
 * render as the em-dash placeholder / chart gaps, never invented zeros.
 *
 * Mirrors `lib/visibility/trends.ts` (range presets, `rangeToWindow`, series
 * projections, headline stats) against the traffic contract
 * (`lib/api/traffic.ts`).
 */
import type { TrendPoint } from '@/components/ui/trend-chart';
import type { SnapshotGranularity, TrafficDashboard } from '@/lib/api/traffic';
import { formatCount, formatShortDate } from '@/lib/format';

// The shared display-format vocabulary (granularity options, bucket-date /
// window / timestamp formats, grouped counts, URL splitting) is OWNED by
// `@/lib/format` (invariant 2) — re-exported here under the traffic-local
// names so the screen + tables keep one domain import point.
export {
  bucketAdjective as bucketAdverb,
  formatCount,
  formatShortDate as formatSeriesLabel,
  formatUtcTimestamp as formatSyncTimestamp,
  formatWindowDate,
  GRANULARITY_OPTIONS,
  splitUrlParts,
} from '@/lib/format';

/** One dated point of a persisted metric series (nullable = chart gap). */
export type TrafficSeriesPoint = TrafficDashboard['series']['impressions'][number];

/** The snapshot bucket granularity served by the traffic read API. */
export type TrafficGranularity = SnapshotGranularity;

/** The per-bucket noun used in delta copy ("vs. prior day"). */
function bucketNoun(granularity: TrafficGranularity): string {
  return granularity;
}

/**
 * Date-range presets. `latest` sends NO window bounds — the backend serves the
 * project's latest persisted snapshot at the requested granularity, so the
 * default landing always renders the freshest projection. The bounded presets
 * send an exact `from`/`to` window (read endpoints serve persisted snapshot
 * windows only; an unmatched window yields the empty payload, which the screen
 * surfaces honestly rather than recomputing).
 */
export type TrafficRange = 'latest' | '7d' | '28d' | '90d';

export const RANGE_OPTIONS: readonly { value: TrafficRange; label: string }[] = [
  { value: 'latest', label: 'Latest synced window' },
  { value: '7d', label: 'Last 7 days' },
  { value: '28d', label: 'Last 28 days' },
  { value: '90d', label: 'Last 90 days' },
] as const;

export function rangeLabel(value: TrafficRange): string {
  return RANGE_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

const RANGE_DAYS: Record<Exclude<TrafficRange, 'latest'>, number> = {
  '7d': 7,
  '28d': 28,
  '90d': 90,
};

/** YYYY-MM-DD in UTC (the `date` query-param shape the API binds). */
function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/**
 * Resolve a range preset into `from`/`to` UTC date bounds, or `{}` for the
 * default latest-snapshot mode. `now` is injectable for deterministic tests.
 */
export function rangeToWindow(
  range: TrafficRange,
  now: Date = new Date(),
): { from?: string; to?: string } {
  if (range === 'latest') return {};
  const from = new Date(now.getTime());
  from.setUTCDate(from.getUTCDate() - RANGE_DAYS[range]);
  return { from: isoDate(from), to: isoDate(now) };
}

/**
 * Map a persisted series into `TrendChart` points. `percent: true` scales a
 * persisted FRACTION (the wire CTR, e.g. 0.0317) onto the chart's 0–100
 * domain, rounded to 2 decimals so dot labels never show float artifacts. A
 * null value stays null — the chart renders a gap, never a zero.
 */
export function toChartPoints(
  series: readonly TrafficSeriesPoint[],
  { percent = false }: { percent?: boolean } = {},
): TrendPoint[] {
  return series.map((point) => ({
    label: formatShortDate(point.date),
    value:
      point.value === null ? null : percent ? Math.round(point.value * 10000) / 100 : point.value,
  }));
}

/** Nice-ceiling steps for truthful count domains (1/1.5/2/2.5/3/4/5/6/8 × 10^n). */
const NICE_STEPS = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10] as const;

/**
 * Top of the Y domain for a count metric (impressions/clicks/sessions/
 * conversions): the smallest nice-step ceiling ≥ the series max, so the chart
 * scales truthfully (TrendChart's 0–100 default would clamp counts). Empty /
 * all-zero series get a minimal domain.
 */
export function countDomainMax(series: readonly TrafficSeriesPoint[]): number {
  const max = series.reduce((acc, point) => (point.value !== null && point.value > acc ? point.value : acc), 0);
  if (max <= 0) return 10;
  const exponent = Math.floor(Math.log10(max));
  for (const e of [exponent - 1, exponent, exponent + 1]) {
    const base = 10 ** e;
    for (const step of NICE_STEPS) {
      const candidate = step * base;
      if (candidate >= max) return candidate;
    }
  }
  return 10 ** (exponent + 1);
}

/** Compact axis tick: 60000 → `60K`, 1500 → `1.5K`, 500 → `500`. */
export function formatCountTick(value: number): string {
  if (value <= 0) return '0';
  if (value >= 1000) {
    const k = value / 1000;
    return `${Number.isInteger(k) ? k : Math.round(k * 10) / 10}K`;
  }
  return `${Math.round(value)}`;
}

/** Five evenly spaced y-axis labels for a count domain (top → 0). */
export function countAxisTicks(domainMax: number): string[] {
  return [1, 0.75, 0.5, 0.25, 0].map((ratio) => formatCountTick(domainMax * ratio));
}

/** Persisted CTR fraction → display percent (`0.0317` → `3.17%` at 2 digits). */
export function formatCtr(fraction: number, digits = 1): string {
  return `${(fraction * 100).toFixed(digits)}%`;
}

/** Mean ranking position (`8.4`). */
export function formatPosition(value: number): string {
  return value.toFixed(1);
}

/** The not-measured placeholder (null metrics — never a fabricated zero). */
export const NULL_PLACEHOLDER = '—';

// ---------------------------------------------------------------------------
// Headline stat cards
// ---------------------------------------------------------------------------

export type TrafficStatKey = 'impressions' | 'clicks' | 'ctr' | 'position' | 'sessions' | 'conversions';

export type TrafficStat = {
  key: TrafficStatKey;
  label: string;
  /** Display value (already formatted), or the placeholder for null. */
  value: string;
  /** Delta text vs the prior available bucket, or a "not measured" note. */
  delta: string;
  /** Favorable → 'up' (green), unfavorable → 'down' (red), else muted. */
  tone: 'up' | 'down' | 'flat';
  /** Whether this is a not-measured placeholder (null metric). */
  placeholder: boolean;
};

/** The last two AVAILABLE points of a series (nulls are unmeasured buckets). */
function latestPair(series: readonly TrafficSeriesPoint[]): { latest: number; prior: number } | null {
  const available = series.filter((point) => point.value !== null);
  if (available.length < 2) return null;
  return {
    latest: available[available.length - 1].value as number,
    prior: available[available.length - 2].value as number,
  };
}

type Delta = { text: string; tone: 'up' | 'down' | 'flat' };

/** Percent-change delta for a count metric (`+9.8% vs. prior day`). */
function countDelta(series: readonly TrafficSeriesPoint[], noun: string): Delta {
  const pair = latestPair(series);
  if (!pair) return { text: `No prior ${noun}`, tone: 'flat' };
  const diff = pair.latest - pair.prior;
  if (diff === 0) return { text: `No change vs. prior ${noun}`, tone: 'flat' };
  if (pair.prior === 0) {
    // No meaningful baseline: an absolute count delta stays truthful.
    return {
      text: `+${formatCount(diff)} vs. prior ${noun}`,
      tone: diff > 0 ? 'up' : 'down',
    };
  }
  const pct = (diff / pair.prior) * 100;
  const sign = pct > 0 ? '+' : '';
  return {
    text: `${sign}${pct.toFixed(1)}% vs. prior ${noun}`,
    tone: pct > 0 ? 'up' : 'down',
  };
}

/** Percentage-POINT delta for CTR (`+0.2 pts vs. prior day`). */
function ctrDelta(series: readonly TrafficSeriesPoint[], noun: string): Delta {
  const pair = latestPair(series);
  if (!pair) return { text: `No prior ${noun}`, tone: 'flat' };
  const diff = (pair.latest - pair.prior) * 100;
  if (diff === 0) return { text: `No change vs. prior ${noun}`, tone: 'flat' };
  const sign = diff > 0 ? '+' : '';
  return {
    text: `${sign}${diff.toFixed(1)} pts vs. prior ${noun}`,
    tone: diff > 0 ? 'up' : 'down',
  };
}

/** Absolute delta for average position — LOWER is better, so the tone inverts. */
function positionDelta(series: readonly TrafficSeriesPoint[], noun: string): Delta {
  const pair = latestPair(series);
  if (!pair) return { text: `No prior ${noun}`, tone: 'flat' };
  const diff = pair.latest - pair.prior;
  if (diff === 0) return { text: `No change vs. prior ${noun}`, tone: 'flat' };
  const sign = diff > 0 ? '+' : '−';
  return {
    text: `${sign}${Math.abs(diff).toFixed(1)} vs. prior ${noun}`,
    tone: diff < 0 ? 'up' : 'down',
  };
}

/**
 * The six headline stat cards (mockup `traffic-stats`): GSC impressions /
 * clicks / CTR / avg position + GA4 sessions / conversions, each a mono total
 * with a delta vs the prior available series bucket (a projection over
 * persisted values — the backend serves no prior-window totals, so the
 * comparison is honestly scoped to the displayed window). Null metrics render
 * the em-dash placeholder with a not-measured note (sessions/conversions are
 * null when no GA4 connection feeds the window).
 */
export function trafficStats(dashboard: TrafficDashboard): TrafficStat[] {
  const noun = bucketNoun(dashboard.granularity);
  const { totals, series } = dashboard;

  const countStat = (
    key: TrafficStatKey,
    label: string,
    value: number | null,
    metricSeries: readonly TrafficSeriesPoint[],
    nullNote: string,
  ): TrafficStat => {
    if (value === null) {
      return { key, label, value: NULL_PLACEHOLDER, delta: nullNote, tone: 'flat', placeholder: true };
    }
    const delta = countDelta(metricSeries, noun);
    return { key, label, value: formatCount(value), delta: delta.text, tone: delta.tone, placeholder: false };
  };

  const ctr = totals.ctr;
  const ctrD = ctrDelta(series.ctr, noun);
  const position = totals.position;
  const positionD = positionDelta(series.position, noun);

  return [
    countStat('impressions', 'Impressions', totals.impressions, series.impressions, ''),
    countStat('clicks', 'Clicks', totals.clicks, series.clicks, ''),
    ctr === null
      ? { key: 'ctr', label: 'CTR', value: NULL_PLACEHOLDER, delta: 'No impressions in window', tone: 'flat', placeholder: true }
      : { key: 'ctr', label: 'CTR', value: formatCtr(ctr, 2), delta: ctrD.text, tone: ctrD.tone, placeholder: false },
    position === null
      ? { key: 'position', label: 'Avg position', value: NULL_PLACEHOLDER, delta: 'No impressions in window', tone: 'flat', placeholder: true }
      : { key: 'position', label: 'Avg position', value: formatPosition(position), delta: positionD.text, tone: positionD.tone, placeholder: false },
    countStat('sessions', 'Sessions', totals.sessions, series.sessions, 'No GA4 data in window'),
    countStat('conversions', 'Conversions', totals.conversions, series.conversions, 'No GA4 data in window'),
  ];
}

/** An empty payload: every series empty (no persisted snapshot served). */
export function isEmptyDashboard(dashboard: TrafficDashboard): boolean {
  return Object.values(dashboard.series).every((series) => series.length === 0);
}

// ---------------------------------------------------------------------------
// Table sort + URL cell helpers
// ---------------------------------------------------------------------------

/** Display labels for the sortable metric keys (footer note copy). */
const SORT_METRIC_LABEL: Record<string, string> = {
  impressions: 'impressions',
  clicks: 'clicks',
  ctr: 'CTR',
  position: 'position',
  sessions: 'sessions',
  conversions: 'conversions',
};

/** The sort idiom: a leading `-` is descending (the "top rows" view). */
export function sortDirection(sort: string): 'ascending' | 'descending' {
  return sort.startsWith('-') ? 'descending' : 'ascending';
}

export function sortKey(sort: string): string {
  return sort.startsWith('-') ? sort.slice(1) : sort;
}

/** Footer note in the mockup voice (`Sorted by clicks, descending`). */
export function describeSort(sort: string): string {
  return `Sorted by ${SORT_METRIC_LABEL[sortKey(sort)] ?? sortKey(sort)}, ${sortDirection(sort)}`;
}

/**
 * Column-header click: a new column sorts descending first (the "top rows"
 * view); clicking the active column toggles its direction.
 */
export function toggleSort(current: string, key: string): string {
  if (current === `-${key}`) return key;
  return `-${key}`;
}
