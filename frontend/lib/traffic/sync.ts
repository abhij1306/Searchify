/**
 * Traffic sync-now polling idiom (F6).
 *
 * `POST /projects/{id}/traffic/sync` enqueues one integrations sync run per
 * active mapped GSC/GA4 connection (C3); the screen then polls each run via
 * `GET /integrations/{connection_id}/syncs/{sync_run_id}` at
 * `TRAFFIC_SYNC_POLL_MS` until the run reaches a terminal queue status.
 *
 * Mirrors the F5 settings polling idiom (`SYNC_RUN_POLL_MS` +
 * `isActiveSyncStatus` in `components/settings/integration-card.tsx`, which
 * are component-local there) and `ACTIVE_RUN_POLL_MS` in
 * `lib/visibility/use-visibility-dashboard.ts` — the same 3s cadence.
 */
import type { IntegrationSyncRun } from '@/lib/api/integrations';

/** Poll cadence for an in-flight traffic sync run (mirrors SYNC_RUN_POLL_MS). */
export const TRAFFIC_SYNC_POLL_MS = 3_000;

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
