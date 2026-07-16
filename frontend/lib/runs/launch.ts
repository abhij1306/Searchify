/**
 * Launch-dialog view-model + payload builder (F10).
 *
 * Turns the dialog's local selection state (a prompt set, a set of logical
 * engines, and a repetition count) into the `POST /audits` body (B5
 * `AuditCreate`). Pure + transport-free so the payload shape is unit-testable
 * independent of the dialog component.
 */
import type { LaunchAuditInput } from '@/lib/api/runs';
import type { LogicalEngine } from '@/lib/api/types';

export const MIN_REPETITIONS = 1;
export const MAX_REPETITIONS = 10;
export const DEFAULT_REPETITIONS = 3;

/** The dialog's local, still-being-edited selection. */
export type LaunchSelection = {
  projectId: string;
  promptSetId: string | null;
  engines: LogicalEngine[];
  repetitions: number;
};

/** Clamp a repetition count into the backend-accepted range. */
export function clampRepetitions(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_REPETITIONS;
  return Math.min(MAX_REPETITIONS, Math.max(MIN_REPETITIONS, Math.round(value)));
}

/**
 * True when the selection is launchable: a prompt set is chosen and at least one
 * engine is selected. (The backend also requires a configured provider route per
 * engine; the dialog only offers configured engines.)
 */
export function canLaunch(selection: LaunchSelection): boolean {
  return Boolean(selection.promptSetId) && selection.engines.length > 0;
}

/**
 * Build the `POST /audits` payload from a launchable selection. Throws if the
 * selection is not launchable — callers gate on `canLaunch` first.
 */
export function buildLaunchPayload(selection: LaunchSelection): LaunchAuditInput {
  if (!canLaunch(selection) || !selection.promptSetId) {
    throw new Error('Cannot build a launch payload from an incomplete selection.');
  }
  return {
    project_id: selection.projectId,
    prompt_set_id: selection.promptSetId,
    engines: [...selection.engines],
    repetitions: clampRepetitions(selection.repetitions),
  };
}

/** Toggle a logical engine in/out of the current selection (immutably). */
export function toggleEngine(engines: LogicalEngine[], engine: LogicalEngine): LogicalEngine[] {
  return engines.includes(engine)
    ? engines.filter((e) => e !== engine)
    : [...engines, engine];
}
