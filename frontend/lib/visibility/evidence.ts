/**
 * Execution-evidence display helpers (Mentions & Citations + Query Fanout tabs).
 *
 * Pure, framework-free projections over the loaded
 * `VisibilityExecutionEvidence[]` window. The endpoint is the single source of
 * truth: nothing here recomputes a metric, infers a mention/query, or claims a
 * global total the bounded newest-window cannot support (plan §Query Fanout).
 */
import type { VisibilityExecutionEvidence } from '@/lib/api/types';

/** A group of executions that share one frozen prompt, for presentation only. */
export type PromptGroup = {
  /** The grouping key: the frozen prompt snapshot id (stable, always unique). */
  promptSnapshotId: string;
  /** The nullable source prompt id (null once the source prompt is deleted). */
  promptId: string | null;
  /** The frozen prompt text (the group heading). */
  promptText: string;
  /** The executions in this group, kept in the endpoint's newest-first order. */
  executions: VisibilityExecutionEvidence[];
};

/**
 * CLIENT-group the loaded per-execution items by their frozen prompt, for
 * presentation only. Grouping key is `prompt_snapshot_id` so executions that
 * froze the SAME prompt text but differ (deleted vs live source prompt) stay
 * distinct. Group order follows first appearance in the (newest-first) window;
 * execution order inside a group is preserved. This never claims a global
 * prompt total or an average over the truncated window.
 */
export function groupByPrompt(items: readonly VisibilityExecutionEvidence[]): PromptGroup[] {
  const order: string[] = [];
  const groups = new Map<string, PromptGroup>();
  for (const item of items) {
    const key = item.prompt_snapshot_id;
    let group = groups.get(key);
    if (!group) {
      group = {
        promptSnapshotId: key,
        promptId: item.prompt_id,
        promptText: item.prompt_text || 'Untitled prompt',
        executions: [],
      };
      groups.set(key, group);
      order.push(key);
    }
    group.executions.push(item);
  }
  return order.map((key) => groups.get(key)!);
}

/** The distinct non-blank search-query strings for one execution, in order. */
export function queryTexts(item: VisibilityExecutionEvidence): string[] {
  return item.search_events.map((event) => event.query).filter((query) => query.trim().length > 0);
}

/** Human explanation for a count-only execution ("provider reported N searches"). */
export function countOnlyExplanation(item: VisibilityExecutionEvidence): string {
  const n = item.search_query_count;
  return `Query text unavailable; provider reported ${n} ${n === 1 ? 'search' : 'searches'}`;
}

/** Provenance summary line: task/analysis + artifact-or-fallback source. */
export function provenanceSummary(item: VisibilityExecutionEvidence): string {
  const source =
    item.event_source === 'raw_artifact'
      ? item.artifact_id
        ? `artifact ${shortId(item.artifact_id)}`
        : 'artifact'
      : item.event_source === 'audit_task'
        ? 'task (artifact pruned)'
        : 'no search source';
  return `Provenance: task ${shortId(item.task_id)} · analysis ${shortId(item.analysis_id)} · ${source}`;
}

/** A short, stable id fragment for compact provenance display. */
export function shortId(id: string): string {
  return id.slice(0, 8);
}

/** Format an execution completion timestamp, or a "date unavailable" note. */
export function formatExecutionDate(timestamp: string | null): string {
  if (!timestamp) return 'Date unavailable';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return 'Date unavailable';
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** All classified citations across the window, de-duplicated by execution+ordinal. */
export function totalCitationCount(items: readonly VisibilityExecutionEvidence[]): number {
  return items.reduce((sum, item) => sum + item.citations.length, 0);
}

/** All persisted mentions across the window. */
export function totalMentionCount(items: readonly VisibilityExecutionEvidence[]): number {
  return items.reduce((sum, item) => sum + item.mentions.length, 0);
}
