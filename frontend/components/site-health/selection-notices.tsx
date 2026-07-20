'use client';

import { Alert } from '@/components/ui/alert';
import type { QuotaStatus } from '@/lib/site-health/selection';

/**
 * The stacked alert strip for the monitored-selection flow: bulk failure,
 * over-quota warning, stale-version merge notice, and commit failure. Purely
 * presentational — visibility rules mirror the container's mutation state.
 */
export function SelectionNotices({
  bulkError,
  bulkErrorMessage,
  quota,
  staleNotice,
  replaceError,
}: Readonly<{
  bulkError: boolean;
  bulkErrorMessage: string | null;
  quota: QuotaStatus | null;
  staleNotice: boolean;
  replaceError: boolean;
}>) {
  return (
    <>
      {bulkError && !staleNotice ? (
        <Alert tone="danger">
          {bulkErrorMessage ?? 'Could not apply the bulk selection. Please try again.'}
        </Alert>
      ) : null}
      {quota?.overLimit ? (
        <Alert tone="warning">
          You&apos;ve selected {quota.staged} pages — your plan allows {quota.limit}. Remove{' '}
          {quota.staged - quota.limit} to continue.
        </Alert>
      ) : null}
      {staleNotice ? (
        <Alert tone="info">
          The monitored set changed since you started. We merged your edits onto the latest version
          — review and resubmit.
        </Alert>
      ) : null}
      {replaceError && !staleNotice ? (
        <Alert tone="danger">Could not save your selection. Please try again.</Alert>
      ) : null}
    </>
  );
}
