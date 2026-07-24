/**
 * LLM Analytics projection helpers (F8/F9).
 *
 * Pure, framework-free helpers that turn the backend `LlmAnalytics`
 * projection + referral rows into chart points, donut segments, and display
 * copy. The endpoints are the single source of truth; nothing here
 * recomputes a metric — it only projects persisted values for display
 * (invariant 7). Unavailable buckets stay `null` (chart gaps, never a
 * misleading zero), and the correlation `insufficient_data` state never
 * surfaces a fabricated coefficient (invariant 9).
 */
import type { DonutSegment } from '@/components/ui/donut';
import type { TrendPoint } from '@/components/ui/trend-chart';
import type {
  AiSource,
  AnalyticsCorrelation,
  LlmAnalytics,
} from '@/lib/api/analytics';
import { ENGINE_ORDER } from '@/lib/providers/catalog';
import { formatShortDate } from '@/lib/format';
import { bucketAdjective, type AnalyticsGranularity } from './options';

// The bucket-date / grouped-count / URL-split formatters are OWNED by
// `@/lib/format` (shared with the traffic surface, invariant 2) —
// re-exported here under the analytics-local names.
export {
  formatShortDate as formatBucketDate,
  formatCount as formatInt,
  splitUrlParts as splitLandingUrl,
} from '@/lib/format';

// Mirrors backend `app/core/config/analytics.py` CORRELATION_MIN_SAMPLE — the
// minimum aligned bucket count before a Pearson coefficient is reported.
export const CORRELATION_MIN_SAMPLE = 8;

// Mirrors backend `app/core/config/analytics.py` ANALYTICS_REFERRALS_PAGE_SIZE
// — the fixed referrals page size named in the table footer note.
export const REFERRALS_PAGE_SIZE = 50;

type SeriesPoint = { date: string; value: number | null };

/**
 * Whole-number series (referral-volume counts, 0–100 engine scores) → chart
 * points. A `null` bucket stays a gap — never coerced to zero.
 */
export function toCountChartPoints(points: readonly SeriesPoint[]): TrendPoint[] {
  return points.map((point) => ({
    label: formatShortDate(point.date),
    value: point.value === null ? null : Math.round(point.value),
  }));
}

/**
 * Referral-share series (persisted 0–1 fractions) → chart points on the
 * fixed 0–100% scale, one decimal ("2.6" per the mockup).
 */
export function toPercentChartPoints(points: readonly SeriesPoint[]): TrendPoint[] {
  return points.map((point) => ({
    label: formatShortDate(point.date),
    value: point.value === null ? null : Math.round(point.value * 1000) / 10,
  }));
}

/**
 * Truthful Y ceiling for a count series (the F4 `domainMax` prop): never
 * below the default 100 so low-volume windows don't exaggerate; otherwise a
 * one-significant-digit ceiling (247 → 300, 1150 → 2000).
 */
export function countDomainMax(values: readonly number[]): number {
  const max = values.length ? Math.max(...values) : 0;
  if (max <= 100) return 100;
  const magnitude = 10 ** Math.floor(Math.log10(max));
  return Math.ceil(max / magnitude) * magnitude;
}

/** Five evenly spaced integer Y-axis labels from `domainMax` down to 0. */
export function countYLabels(domainMax: number): string[] {
  return [1, 0.75, 0.5, 0.25, 0].map((fraction) => `${Math.round(domainMax * fraction)}`);
}

/** The latest AVAILABLE (non-null) value of a series, or null when none. */
export function latestValue(points: readonly SeriesPoint[]): number | null {
  for (let index = points.length - 1; index >= 0; index -= 1) {
    const value = points[index].value;
    if (value !== null) return value;
  }
  return null;
}

/** A persisted 0–1 fraction as a percent string ("62%"); em-dash for null. */
export function formatPercent(fraction: number | null, decimals = 0): string {
  if (fraction === null) return '—';
  return `${(fraction * 100).toFixed(decimals)}%`;
}

// ---------------------------------------------------------------------------
// AI-source vocabulary (the backend config-owned rule table's enum)
// ---------------------------------------------------------------------------

export const AI_SOURCES: readonly AiSource[] = [
  'chatgpt',
  'gemini',
  'claude',
  'perplexity',
  'copilot',
  'google_ai_overview',
  'other',
] as const;

export const AI_SOURCE_LABELS: Record<AiSource, string> = {
  chatgpt: 'ChatGPT',
  gemini: 'Gemini',
  claude: 'Claude',
  perplexity: 'Perplexity',
  copilot: 'Copilot',
  google_ai_overview: 'Google AI Overview',
  other: 'Other',
};

export function aiSourceLabel(source: AiSource): string {
  return AI_SOURCE_LABELS[source] ?? source;
}

// Per-source data-viz colors mapped onto bridged semantic tokens (token-only,
// no raw hex — design.md §11). ChatGPT keeps the brand accent; the rest reuse
// the categorical hues the design system already defines.
export const AI_SOURCE_STROKE: Record<AiSource, string> = {
  chatgpt: 'stroke-accent',
  perplexity: 'stroke-citation-owned',
  gemini: 'stroke-citation-third-party',
  claude: 'stroke-run-analyzing',
  copilot: 'stroke-info',
  google_ai_overview: 'stroke-warning',
  other: 'stroke-subtle',
};

export const AI_SOURCE_FILL: Record<AiSource, string> = {
  chatgpt: 'bg-accent',
  perplexity: 'bg-citation-owned',
  gemini: 'bg-citation-third-party',
  claude: 'bg-run-analyzing',
  copilot: 'bg-info',
  google_ai_overview: 'bg-warning',
  other: 'bg-subtle',
};

/**
 * Source-breakdown rows → donut segments, sessions-descending (ties keep the
 * canonical `AI_SOURCES` order) so the legend is stable across refetches.
 */
export function sourceSegments(sources: LlmAnalytics['sources']): DonutSegment[] {
  const order = new Map(AI_SOURCES.map((source, index) => [source, index]));
  return sources
    .slice()
    .sort(
      (a, b) =>
        b.sessions - a.sessions ||
        (order.get(a.ai_source) ?? AI_SOURCES.length) -
          (order.get(b.ai_source) ?? AI_SOURCES.length),
    )
    .map((row) => ({
      label: aiSourceLabel(row.ai_source),
      value: row.sessions,
      colorClass: AI_SOURCE_STROKE[row.ai_source],
    }));
}

/** Total classified sessions across the source breakdown (donut center). */
export function totalSourceSessions(sources: LlmAnalytics['sources']): number {
  return sources.reduce((sum, row) => sum + row.sessions, 0);
}

/** Engine-visibility rows in the canonical audited-engine order (inv. 10). */
export function sortEngineVisibility(
  rows: LlmAnalytics['engine_visibility'],
): LlmAnalytics['engine_visibility'] {
  const order = new Map(ENGINE_ORDER.map((engine, index) => [engine as string, index]));
  return rows.slice().sort((a, b) => {
    const ai = order.get(a.logical_engine) ?? Number.MAX_SAFE_INTEGER;
    const bi = order.get(b.logical_engine) ?? Number.MAX_SAFE_INTEGER;
    return ai - bi || a.logical_engine.localeCompare(b.logical_engine);
  });
}

// ---------------------------------------------------------------------------
// Correlation card copy
// ---------------------------------------------------------------------------

export type CorrelationDisplay = {
  /** The big mono value ("r = 0.68", or the em-dash placeholder). */
  value: string;
  insufficient: boolean;
  /** Header/meta badge text ("Insufficient data" or "n = 12 weekly buckets"). */
  badge: string;
  description: string;
};

/**
 * Correlation card display copy. `insufficient_data` renders the neutral
 * badge + em-dash and explains what is missing — NEVER a fabricated
 * coefficient (invariant 9); `ok` renders the persisted coefficient framed as
 * descriptive, not a forecast (mockup copy).
 */
export function correlationDisplay(
  correlation: AnalyticsCorrelation,
  granularity: AnalyticsGranularity,
): CorrelationDisplay {
  const adjective = bucketAdjective(granularity);
  if (correlation.state === 'ok' && correlation.coefficient !== null) {
    return {
      value: `r = ${correlation.coefficient.toFixed(2)}`,
      insufficient: false,
      badge: `n = ${correlation.sample_size} ${adjective} buckets`,
      description: `Pearson coefficient between ${adjective} cross-engine visibility and AI-referral sessions. Descriptive — not a forecast.`,
    };
  }
  return {
    value: '—',
    insufficient: true,
    badge: 'Insufficient data',
    description:
      correlation.sample_size < CORRELATION_MIN_SAMPLE
        ? `A Pearson coefficient is reported once at least ${CORRELATION_MIN_SAMPLE} aligned ${adjective} buckets of visibility and referral data exist — ${correlation.sample_size} of ${CORRELATION_MIN_SAMPLE} collected so far.`
        : `The aligned ${adjective} buckets show no variation yet, so a Pearson coefficient is not defined. Descriptive — not a forecast.`,
  };
}

// ---------------------------------------------------------------------------
// Referral drill-down row display
// ---------------------------------------------------------------------------

/** `occurred_at` → "Jul 23, 2026, 08:41 PM"; unparseable drift passes through. */
export function formatOccurredAt(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** The whole-screen empty state: no referral, source, or visibility evidence. */
export function isAnalyticsEmpty(data: LlmAnalytics): boolean {
  return (
    data.referral_volume.length === 0 &&
    data.sources.length === 0 &&
    data.engine_visibility.length === 0
  );
}
