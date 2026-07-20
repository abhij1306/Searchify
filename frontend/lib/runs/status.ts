/**
 * Run/execution status view-model helpers (F10).
 *
 * Maps the backend audit + task statuses (B5) and citation classifications (B6)
 * onto the F3 `Badge` value spaces, and answers "is this run still active?" so
 * the detail screen knows whether to keep polling. Pure functions — no
 * transport, no React — so they are trivially unit-testable.
 */
import type { AuditStatus, CitationClassification, ExecutionStatus } from '@/lib/api/types';
import type { ClassificationValue, RunStatusValue } from '@/components/ui/badge-variants';
import { titleCaseStatus } from '@/lib/utils';

/**
 * Audit statuses that are terminal — the run has stopped and needs no further
 * polling. Every other status means the run is still progressing.
 */
const TERMINAL_AUDIT_STATUSES: ReadonlySet<AuditStatus> = new Set<AuditStatus>([
  'completed',
  'partially_completed',
  'failed',
  'cancelled',
]);

/**
 * Audit statuses at which a cooperative cancel is still meaningful. Mirrors the
 * backend `AUDIT_ACTIVE_STATUSES` (core/config/audits.py): `reporting` is
 * intentionally EXCLUDED — by then execution + analysis are done and the state
 * machine rejects REPORTING → CANCELLED, so the cancel button must be disabled.
 */
const CANCELABLE_AUDIT_STATUSES: ReadonlySet<AuditStatus> = new Set<AuditStatus>([
  'draft',
  'validating',
  'queued',
  'running',
  'analyzing',
]);

/**
 * True while `/runs/[runId]` should keep polling `GET /audits/{id}`: the run is
 * not yet terminal (including `reporting`, which still transitions on its own).
 */
export function shouldPollAudit(status: AuditStatus): boolean {
  return !TERMINAL_AUDIT_STATUSES.has(status);
}

/**
 * True when the run can still be cancelled cooperatively. `reporting` and every
 * terminal status return false — the backend would reject the cancel.
 */
export function isAuditCancelable(status: AuditStatus): boolean {
  return CANCELABLE_AUDIT_STATUSES.has(status);
}

/**
 * Map an audit lifecycle status onto a run-status badge value. The badge family
 * has eight values (design.md §8); the extra backend statuses fold onto the
 * nearest visual: validating→queued, reporting→analyzing,
 * partially_completed→partial.
 */
export function auditBadgeValue(status: AuditStatus): RunStatusValue {
  switch (status) {
    case 'validating':
      return 'queued';
    case 'reporting':
      return 'analyzing';
    case 'partially_completed':
      return 'partial';
    default:
      return status;
  }
}

/** Human-readable label for an audit status. */
export function auditStatusLabel(status: AuditStatus): string {
  return titleCaseStatus(status);
}

/**
 * Map an execution/queue status onto a status badge value (success/warning/
 * danger/info). Succeeded is success; failed/cancelled are danger; retry_wait is
 * warning; everything in flight is info.
 */
export function executionBadgeValue(
  status: ExecutionStatus,
): 'success' | 'warning' | 'danger' | 'info' {
  switch (status) {
    case 'succeeded':
      return 'success';
    case 'failed':
    case 'cancelled':
      return 'danger';
    case 'retry_wait':
      return 'warning';
    default:
      return 'info';
  }
}

/** Human-readable label for an execution status. */
export function executionStatusLabel(status: ExecutionStatus): string {
  return titleCaseStatus(status);
}

/**
 * Map a citation classification onto the citation badge value space. The badge
 * family has three values (owned / competitor / third-party); the backend's
 * fourth class, `unintended` (an owned-but-unwanted domain), is surfaced under
 * the `owned` visual since it is still an owned-domain citation.
 */
export function classificationBadgeValue(
  classification: CitationClassification,
): ClassificationValue {
  switch (classification) {
    case 'owned':
    case 'unintended':
      return 'owned';
    case 'competitor':
      return 'competitor';
    default:
      return 'third-party';
  }
}

/** Short, stable date/time label for a timestamp (falls back to the raw value). */
export function formatDateTime(timestamp: string | null): string {
  if (!timestamp) return '—';
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

/** Human-readable label for a citation classification. */
export function classificationLabel(classification: CitationClassification): string {
  switch (classification) {
    case 'owned':
      return 'Owned';
    case 'unintended':
      return 'Owned (unintended)';
    case 'competitor':
      return 'Competitor';
    default:
      return 'Third-party';
  }
}
