/**
 * Cross-run Visibility trend helpers (F9 Trend mode).
 *
 * Pure, framework-free helpers the Trend view uses to turn the backend
 * `VisibilityTrendPoint[]` projection into the chart series, headline stats,
 * version-boundary markers, and start/latest ranking tables. The trend endpoint
 * is the single source of truth; nothing here recomputes a metric — it only
 * projects persisted values for display (invariant 7). Sentiment / average
 * position stay the not-yet-computed placeholder (decision B-2 / invariant 9).
 */
import type { TrendPoint } from '@/components/ui/trend-chart';
import type { LogicalEngine, VisibilityTrendPoint, VisibilityTrendRankingRow } from '@/lib/api/types';
import { ENGINE_ORDER } from '@/lib/providers/catalog';

/** Trend granularity — mirrors the backend `granularity=run|week|month`. */
export type TrendGranularity = 'run' | 'week' | 'month';

export const GRANULARITY_OPTIONS: readonly { value: TrendGranularity; label: string }[] = [
  { value: 'run', label: 'Per run' },
  { value: 'week', label: 'Weekly' },
  { value: 'month', label: 'Monthly' },
] as const;

export function granularityLabel(value: TrendGranularity): string {
  return GRANULARITY_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

/** Date-range presets. `all` sends no bounds; the rest send a UTC `from`. */
export type TrendRange = 'all' | '30d' | '90d' | '1y';

export const RANGE_OPTIONS: readonly { value: TrendRange; label: string }[] = [
  { value: '30d', label: 'Last 30 days' },
  { value: '90d', label: 'Last 90 days' },
  { value: '1y', label: 'Last 12 months' },
  { value: 'all', label: 'All time' },
] as const;

export function rangeLabel(value: TrendRange): string {
  return RANGE_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

/** Engines offered by the trend engine filter (canonical display order). */
export const TREND_ENGINES: readonly LogicalEngine[] = ENGINE_ORDER;

/**
 * Resolve a range preset into an inclusive UTC `from` bound (ISO 8601), or
 * `undefined` for "all time". `now` is injectable for deterministic tests.
 */
export function rangeToFrom(range: TrendRange, now: Date = new Date()): string | undefined {
  if (range === 'all') return undefined;
  const from = new Date(now.getTime());
  if (range === '30d') from.setUTCDate(from.getUTCDate() - 30);
  else if (range === '90d') from.setUTCDate(from.getUTCDate() - 90);
  else if (range === '1y') from.setUTCFullYear(from.getUTCFullYear() - 1);
  return from.toISOString();
}

/** Which headline metric a chart plots. */
export type TrendMetric = 'visibility_score' | 'sov' | 'brand_mention_rate' | 'owned_citation_rate';

/** Short x-axis label for a point's completion timestamp. */
function formatPointLabel(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

/** Full date label (used for the start/latest ranking card subtitles). */
export function formatPointDate(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

/** A metric's 0–100 value for a point (percentages scaled to whole percent). */
function metricValue(point: VisibilityTrendPoint, metric: TrendMetric): number | null {
  switch (metric) {
    case 'visibility_score':
      return point.visibility_score;
    case 'sov':
      return point.sov.mention === null ? null : point.sov.mention * 100;
    case 'brand_mention_rate':
      return point.brand_mention_rate === null ? null : point.brand_mention_rate * 100;
    case 'owned_citation_rate':
      return point.owned_citation_rate === null ? null : point.owned_citation_rate * 100;
  }
}

/**
 * Map the trend series into `TrendChart` points for `metric`. A point that
 * introduces a new analyzer/scoring version (its distinct version set differs
 * from the previous point's, or it spans a boundary) carries a `versionChange`
 * marker so the chart can flag it (invariant 4 / version continuity).
 */
export function toChartPoints(points: readonly VisibilityTrendPoint[], metric: TrendMetric): TrendPoint[] {
  let prevVersions: string | null = null;
  return points.map((point) => {
    const value = metricValue(point, metric);
    const versionKey = [...point.analyzer_versions, ...point.scoring_rule_versions].join('|');
    const changed = prevVersions !== null && versionKey !== prevVersions;
    prevVersions = versionKey;
    return {
      // Preserve unavailable metrics as null — the chart renders a GAP and an
      // "unavailable" label rather than coercing to a misleading zero.
      label: formatPointLabel(point.completed_at),
      value: value === null ? null : Math.round(value),
      versionChange:
        changed || point.spans_version_boundary
          ? { note: versionChangeNote(point) }
          : null,
    };
  });
}

/** Human note for a version-change marker (which version set now applies). */
function versionChangeNote(point: VisibilityTrendPoint): string {
  const scoring = point.scoring_rule_versions.join(', ') || 'unknown';
  return point.spans_version_boundary
    ? `Mixed scoring versions in this bucket (${scoring})`
    : `Scoring rule ${scoring} applied`;
}

/** A trailing note describing the first version boundary in the series, if any. */
export function versionMarkerSummary(points: readonly VisibilityTrendPoint[]): string | null {
  let prev: string | null = null;
  for (const point of points) {
    const key = [...point.analyzer_versions, ...point.scoring_rule_versions].join('|');
    if (point.spans_version_boundary) {
      return `${versionChangeNote(point)} from ${formatPointDate(point.completed_at)}`;
    }
    if (prev !== null && key !== prev) {
      return `${versionChangeNote(point)} from ${formatPointDate(point.completed_at)}`;
    }
    prev = key;
  }
  return null;
}

/** The persisted brand-mention VOLUME (count) for a point, or null.
 *
 * Derived from the point's `is_brand` ranking row's persisted `mention_count`
 * (summed across the bucket by the backend). This is a raw count, not a rate,
 * so it is surfaced as a volume stat rather than plotted on the 0–100 charts.
 */
function brandMentionCount(point: VisibilityTrendPoint): number | null {
  const brand = point.rankings.find((row) => row.is_brand);
  return brand ? brand.mention_count : null;
}

/** One headline stat: latest value + delta vs the prior point. */
export type TrendStat = {
  key: TrendMetric | 'response_sov' | 'brand_mention_count' | 'sentiment' | 'avg_position';
  label: string;
  /** Display value (already formatted), or the placeholder for null. */
  value: string;
  /** Signed delta text vs the prior point, or a "not computed" note. */
  delta: string;
  direction: 'up' | 'down' | 'flat';
  /** Whether this is a not-yet-computed placeholder metric (B-2). */
  placeholder: boolean;
};

function formatPct(value: number | null): string {
  return value === null ? '—' : `${Math.round(value)}%`;
}

function formatScoreValue(value: number | null): string {
  return value === null ? '—' : `${Math.round(value)}`;
}

/**
 * Signed delta of `latest` vs `prior` as display text + direction. `round`
 * whole-numbers both sides first (headline rates/scores) or compares raw counts
 * (brand-mention volume); `suffix` appends the unit (e.g. `%`).
 */
function delta(
  latest: number | null,
  prior: number | null,
  { round = false, suffix = '' }: { round?: boolean; suffix?: string } = {},
): { text: string; direction: 'up' | 'down' | 'flat' } {
  if (latest === null || prior === null) return { text: 'No prior run', direction: 'flat' };
  const diff = round ? Math.round(latest) - Math.round(prior) : latest - prior;
  if (diff === 0) return { text: 'No change vs. prior run', direction: 'flat' };
  const sign = diff > 0 ? '+' : '';
  return { text: `${sign}${diff}${suffix} vs. prior run`, direction: diff > 0 ? 'up' : 'down' };
}

function responseSov(point: VisibilityTrendPoint | null): number | null {
  return point && point.sov.response !== null ? point.sov.response * 100 : null;
}

/**
 * Headline stat row (all values are persisted, no recomputation — invariant 7):
 * Visibility Score, mention-level Share of Voice, response-level Share of Voice,
 * Brand mentions (VOLUME — the persisted `is_brand` `mention_count`, not a
 * rate), and Owned Citations rate — each with a delta vs the prior point — plus
 * the null Sentiment / Avg Position placeholders (decision B-2 / invariant 9).
 * Deltas are a "no prior run" note when there is only one point (no fake slope).
 */
export function trendStats(points: readonly VisibilityTrendPoint[]): TrendStat[] {
  const latest = points.length ? points[points.length - 1] : null;
  const prior = points.length > 1 ? points[points.length - 2] : null;

  const vs = latest ? metricValue(latest, 'visibility_score') : null;
  const vsPrior = prior ? metricValue(prior, 'visibility_score') : null;
  const sov = latest ? metricValue(latest, 'sov') : null;
  const sovPrior = prior ? metricValue(prior, 'sov') : null;
  const rsov = responseSov(latest);
  const rsovPrior = responseSov(prior);
  const bmc = latest ? brandMentionCount(latest) : null;
  const bmcPrior = prior ? brandMentionCount(prior) : null;
  const oc = latest ? metricValue(latest, 'owned_citation_rate') : null;
  const ocPrior = prior ? metricValue(prior, 'owned_citation_rate') : null;

  const scoreDelta = delta(vs, vsPrior, { round: true });
  const sovDelta = delta(sov, sovPrior, { round: true, suffix: '%' });
  const rsovDelta = delta(rsov, rsovPrior, { round: true, suffix: '%' });
  const bmcDelta = delta(bmc, bmcPrior);
  const ocDelta = delta(oc, ocPrior, { round: true, suffix: '%' });

  return [
    {
      key: 'visibility_score',
      label: 'Visibility Score',
      value: formatScoreValue(vs),
      delta: scoreDelta.text,
      direction: scoreDelta.direction,
      placeholder: false,
    },
    {
      key: 'sov',
      label: 'SOV (mention)',
      value: formatPct(sov),
      delta: sovDelta.text,
      direction: sovDelta.direction,
      placeholder: false,
    },
    {
      key: 'response_sov',
      label: 'SOV (response)',
      value: formatPct(rsov),
      delta: rsovDelta.text,
      direction: rsovDelta.direction,
      placeholder: false,
    },
    {
      key: 'brand_mention_count',
      label: 'Brand mentions',
      value: bmc === null ? '—' : `${bmc}`,
      delta: bmcDelta.text,
      direction: bmcDelta.direction,
      placeholder: false,
    },
    {
      key: 'owned_citation_rate',
      label: 'Owned Citations',
      value: formatPct(oc),
      delta: ocDelta.text,
      direction: ocDelta.direction,
      placeholder: false,
    },
    {
      key: 'sentiment',
      label: 'Sentiment',
      value: '—',
      delta: 'Not yet computed',
      direction: 'flat',
      placeholder: true,
    },
    {
      key: 'avg_position',
      label: 'Avg Position',
      value: '—',
      delta: 'Not yet computed',
      direction: 'flat',
      placeholder: true,
    },
  ];
}

/** Ranking rows for a point, kept SOV-sorted (rows already arrive sorted). */
export function sortedTrendRankings(rows: readonly VisibilityTrendRankingRow[]): VisibilityTrendRankingRow[] {
  return rows
    .slice()
    .sort((a, b) => (b.share_of_voice ?? 0) - (a.share_of_voice ?? 0) || a.name.localeCompare(b.name));
}

/** Latest + first-in-range points for the side-by-side ranking comparison. */
export function rankingBookends(points: readonly VisibilityTrendPoint[]): {
  latest: VisibilityTrendPoint | null;
  first: VisibilityTrendPoint | null;
} {
  if (!points.length) return { latest: null, first: null };
  return {
    latest: points[points.length - 1],
    first: points.length > 1 ? points[0] : null,
  };
}

/** Format a 0–1 rate as a whole-percent string, or the placeholder. */
export function formatTrendRate(rate: number | null): string {
  if (rate === null || Number.isNaN(rate)) return '—';
  return `${Math.round(rate * 100)}%`;
}
