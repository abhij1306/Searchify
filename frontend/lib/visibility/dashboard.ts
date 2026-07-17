/**
 * Visibility dashboard helpers (F9).
 *
 * Pure, framework-free helpers the dashboard uses to pick the default run,
 * format the projection for display, and apply the client-side engine filter.
 * The B6 `/projects/{id}/visibility` endpoint is a selected-run projection
 * keyed only by `audit_id`; the engine filter is a client-side display filter
 * that also participates in the React Query key. There is NO per-prompt-type
 * (branded / non-branded) breakdown in `VisibilityResponse` at MVP, so that
 * control is a disabled "coming soon" affordance and does not affect the query.
 */
import { ENGINE_ORDER } from '@/lib/providers/catalog';
import type {
  AuditStatus,
  LogicalEngine,
  RankingRow,
  Visibility,
  VisibilityEngine,
  VisibilityExecutionEvidence,
} from '@/lib/api/types';

/** Audit statuses that carry a dashboard-ready metric snapshot (B6). */
export const DASHBOARD_STATUSES: readonly AuditStatus[] = ['completed', 'partially_completed'];

/**
 * The four Visibility workspace tabs, in display order. Exactly these four —
 * no Sources / Topics / Sentiment (plan §IA). `overview` is the default.
 *   - overview:            selected-run score / SOV / provider comparison / rankings
 *   - trends:              cross-run metrics + charts + ranking movement
 *   - mentions-citations:  persisted mention/citation evidence
 *   - query-fanout:        frozen prompts + generated search-query evidence
 */
export type VisibilityTab = 'overview' | 'trends' | 'mentions-citations' | 'query-fanout';

/** The ordered tab definitions (id + human label) rendered by the tablist. */
export const VISIBILITY_TABS: readonly { id: VisibilityTab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'trends', label: 'Trends' },
  { id: 'mentions-citations', label: 'Mentions & Citations' },
  { id: 'query-fanout', label: 'Query Fanout' },
] as const;

/** The default (and invalid-value fallback) tab. */
export const DEFAULT_TAB: VisibilityTab = 'overview';

/** Narrow an arbitrary `?tab=` value to a known tab, else the default. */
export function normalizeTab(value: string | null | undefined): VisibilityTab {
  return VISIBILITY_TABS.some((tab) => tab.id === value) ? (value as VisibilityTab) : DEFAULT_TAB;
}

/** The two evidence tabs share one execution-evidence query + cache key. */
export function isEvidenceTab(tab: VisibilityTab): boolean {
  return tab === 'mentions-citations' || tab === 'query-fanout';
}

/**
 * Prompt-type filter options, kept for the disabled "coming soon" control. The
 * backend `VisibilityResponse` has no per-prompt-type breakdown at MVP, so this
 * is display-only and never sent to the API / folded into the query key.
 */
export const PROMPT_TYPE_OPTIONS = [
  { value: 'all', label: 'All prompts' },
  { value: 'branded', label: 'Branded' },
  { value: 'non_branded', label: 'Non-branded' },
] as const;

export type PromptTypeFilter = (typeof PROMPT_TYPE_OPTIONS)[number]['value'];

/** Filters that drive the visibility query key + client-side display. */
export type VisibilityFilters = {
  /** `'all'` shows every engine; otherwise narrows the per-engine comparison. */
  engine: LogicalEngine | 'all';
};

export const DEFAULT_FILTERS: VisibilityFilters = { engine: 'all' };

/** A run option for the selector, ordered latest-first. */
export type RunOption = {
  id: string;
  status: AuditStatus;
  label: string;
  completedAt: string | null;
};

/** Is this a status the dashboard projection can be built from? */
export function isDashboardStatus(status: AuditStatus): boolean {
  return DASHBOARD_STATUSES.includes(status);
}

/**
 * Build the run-selector options from the project's audits: keep only
 * dashboard-ready runs, newest first (by completed-at, then created-at) —
 * mirroring the backend's default-run resolution so the first option is the
 * one the endpoint defaults to when `audit_id` is omitted.
 */
export function toRunOptions(
  audits: ReadonlyArray<{
    id: string;
    status: AuditStatus;
    completed_at: string | null;
    created_at: string;
  }>,
): RunOption[] {
  return audits
    .filter((audit) => isDashboardStatus(audit.status))
    .slice()
    .sort((a, b) => {
      const aKey = a.completed_at ?? a.created_at;
      const bKey = b.completed_at ?? b.created_at;
      return bKey.localeCompare(aKey);
    })
    .map((audit) => ({
      id: audit.id,
      status: audit.status,
      completedAt: audit.completed_at,
      label: formatRunLabel(audit.completed_at ?? audit.created_at),
    }));
}

/** Short, stable label for a run — the completion date/time. */
export function formatRunLabel(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export { engineLabel } from '@/lib/providers/catalog';

/**
 * Per-engine comparison rows for display: apply the engine filter, then order
 * by the canonical engine order so cards are stable across runs.
 */
export function visibleEngines(
  visibility: Visibility,
  filter: VisibilityFilters['engine'],
): VisibilityEngine[] {
  const rows = filter === 'all'
    ? visibility.per_engine
    : visibility.per_engine.filter((engine) => engine.logical_engine === filter);
  const order = new Map(ENGINE_ORDER.map((engine, index) => [engine as string, index]));
  return rows
    .slice()
    .sort((a, b) => {
      const ai = order.get(a.logical_engine) ?? Number.MAX_SAFE_INTEGER;
      const bi = order.get(b.logical_engine) ?? Number.MAX_SAFE_INTEGER;
      return ai - bi || a.logical_engine.localeCompare(b.logical_engine);
    });
}

/** Engines actually present in a projection (for the engine-filter options). */
export function presentEngines(visibility: Visibility | undefined): LogicalEngine[] {
  if (!visibility) return [];
  const present = new Set(visibility.per_engine.map((engine) => engine.logical_engine));
  return ENGINE_ORDER.filter((engine) => present.has(engine));
}

/** Format a 0–1 rate as a whole-percent string, or the placeholder. */
export function formatRate(rate: number | null): string {
  if (rate === null || Number.isNaN(rate)) return PLACEHOLDER;
  return `${Math.round(rate * 100)}%`;
}

/** Format a 0–100 score as a whole number, or the placeholder. */
export function formatScore(score: number | null): string {
  if (score === null || Number.isNaN(score)) return PLACEHOLDER;
  return `${Math.round(score)}`;
}

/** The not-yet-computed placeholder for sentiment + avg-position (B-2). */
export const PLACEHOLDER = '—';

/**
 * A prompt option for the Query Fanout evidence prompt selector. This is
 * EVIDENCE filtering (restrict by `AuditPromptSnapshot.prompt_id`), NOT the
 * Overview prompt-type taxonomy affordance — it never claims prompt-type
 * semantics. A deleted source prompt has a null `prompt_id` and cannot be
 * selected by a current id, so it is offered only under "All prompts".
 */
export type PromptOption = {
  /** The source prompt id; only selectable options carry a non-null id. */
  id: string;
  /** The frozen prompt text (display label). */
  label: string;
};

/**
 * Derive the selectable prompt options from loaded evidence items: distinct
 * source `prompt_id`s (deleted prompts with a null id are excluded — they stay
 * under "All prompts"), labelled by their frozen prompt text, kept in a stable
 * (first-seen, newest-first) order.
 */
export function toPromptOptions(
  items: readonly VisibilityExecutionEvidence[],
): PromptOption[] {
  const seen = new Map<string, string>();
  for (const item of items) {
    if (item.prompt_id && !seen.has(item.prompt_id)) {
      seen.set(item.prompt_id, item.prompt_text || item.prompt_id);
    }
  }
  return [...seen.entries()].map(([id, label]) => ({ id, label }));
}

/** Rankings already arrive sorted by SOV desc from B6; keep that order stable. */
export function sortedRankings(rankings: readonly RankingRow[]): RankingRow[] {
  return rankings
    .slice()
    .sort((a, b) => (b.share_of_voice ?? 0) - (a.share_of_voice ?? 0) || a.name.localeCompare(b.name));
}
