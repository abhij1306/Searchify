/**
 * Visibility dashboard helpers (F9).
 *
 * Pure, framework-free helpers the dashboard uses to pick the default run,
 * format the projection for display, and apply the client-side engine filter.
 * The B6 `/projects/{id}/visibility` endpoint is a selected-run projection
 * keyed only by `audit_id`; engine / prompt-type filters are display filters
 * that also participate in the React Query key (so switching them refetches /
 * re-derives) — no server-side filter is invented here.
 */
import { ENGINE_ORDER } from '@/lib/providers/catalog';
import type { AuditStatus, LogicalEngine, RankingRow, Visibility, VisibilityEngine } from '@/lib/api/types';

/** Audit statuses that carry a dashboard-ready metric snapshot (B6). */
export const DASHBOARD_STATUSES: readonly AuditStatus[] = ['completed', 'partially_completed'];

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
  /** Prompt-type scope (roadmap server-side; MVP participates in the key). */
  promptType: PromptTypeFilter;
};

export const DEFAULT_FILTERS: VisibilityFilters = { engine: 'all', promptType: 'all' };

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

/** Rankings already arrive sorted by SOV desc from B6; keep that order stable. */
export function sortedRankings(rankings: readonly RankingRow[]): RankingRow[] {
  return rankings
    .slice()
    .sort((a, b) => (b.share_of_voice ?? 0) - (a.share_of_voice ?? 0) || a.name.localeCompare(b.name));
}
