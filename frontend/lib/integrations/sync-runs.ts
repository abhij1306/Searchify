/**
 * Sync-run polling idiom — the one owner (F5 settings + F6 traffic).
 *
 * Both the Settings→Integrations card (`POST /integrations/{id}/syncs`
 * on-demand runs) and the /traffic screen (`POST /projects/{id}/traffic/sync`
 * enqueuing one run per active mapped connection) poll each run via
 * `GET /integrations/{connection_id}/syncs/{sync_run_id}` at
 * `SYNC_RUN_POLL_MS` until the run reaches a terminal queue status — the
 * same 3s cadence as `ACTIVE_RUN_POLL_MS` in
 * `lib/visibility/use-visibility-dashboard.ts`.
 */
import type { IntegrationSyncRun } from '@/lib/api/integrations';

/** Poll cadence for an in-flight sync run (mirrors ACTIVE_RUN_POLL_MS). */
export const SYNC_RUN_POLL_MS = 3_000;

type SyncRunStatus = IntegrationSyncRun['status'];

/** Non-terminal queue statuses — an active run keeps polling + disables Sync now. */
export function isActiveSyncRun(status: SyncRunStatus): boolean {
  return (
    status === 'queued' || status === 'leased' || status === 'running' || status === 'retry_wait'
  );
}

/** A terminal run succeeded only on the `succeeded` status. */
export function isSucceededSyncRun(status: SyncRunStatus): boolean {
  return status === 'succeeded';
}
