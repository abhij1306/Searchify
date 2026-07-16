'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useParams } from 'next/navigation';

import { Alert } from '@/components/ui/alert';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { PageTitle, SectionTitle } from '@/components/ui/typography';
import { ExecutionsTable } from '@/components/runs/executions-table';
import { ProgressPanel } from '@/components/runs/progress-panel';
import { queryKeys } from '@/lib/api/query-keys';
import { runsApi } from '@/lib/api/runs';
import { shouldPollAudit } from '@/lib/runs/status';

/** Poll interval (ms) while a run is active. Polling is the baseline; SSE is optional. */
const POLL_INTERVAL_MS = 3_000;

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return 'Something went wrong. Please try again.';
}

/**
 * Run detail screen (F10, design.md §9.7).
 *
 * A progress panel (status + requested/completed/failed counts + cancel +
 * CSV/MD export links) over an executions table. Progress is POLLING-FIRST: the
 * audit + executions queries refetch on an interval while the run is active and
 * stop once it terminalizes. (SSE via `/events?stream=true` is an optional
 * enhancement per the plan; polling is the reliable baseline.)
 */
export default function RunDetailPage() {
  const params = useParams<{ runId: string }>();
  const runId = params.runId;
  const queryClient = useQueryClient();

  const auditQuery = useQuery({
    queryKey: queryKeys.runs.detail(runId),
    queryFn: ({ signal }) => runsApi.getAudit(runId, { signal }),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && shouldPollAudit(status) ? POLL_INTERVAL_MS : false;
    },
  });

  const active = auditQuery.data ? shouldPollAudit(auditQuery.data.status) : false;

  const executionsQuery = useQuery({
    queryKey: queryKeys.runs.executions(runId),
    queryFn: ({ signal }) => runsApi.listExecutions(runId, { signal }),
    refetchInterval: active ? POLL_INTERVAL_MS : false,
  });

  const cancelMutation = useMutation({
    mutationFn: () => runsApi.cancelAudit(runId),
    onSuccess: (audit) => {
      queryClient.setQueryData(queryKeys.runs.detail(runId), audit);
      queryClient.invalidateQueries({ queryKey: queryKeys.runs.executions(runId) });
      // A cancel changes the run's status, so the runs list and any
      // status-dependent visibility view are now stale — refetch both.
      queryClient.invalidateQueries({ queryKey: queryKeys.runs.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.visibility.all });
    },
  });

  const executions = executionsQuery.data ?? [];

  return (
    <div className="grid gap-6">
      <div>
        <Link href="/runs" className="text-xs font-medium text-accent-text hover:underline">
          ← Back to runs
        </Link>
        <PageTitle kicker="Run" className="mt-2">
          Run detail
        </PageTitle>
      </div>

      {auditQuery.isError ? (
        <Alert tone="danger">Could not load this run. {errorMessage(auditQuery.error)}</Alert>
      ) : auditQuery.isLoading || !auditQuery.data ? (
        <Card>
          <CardContent className="grid gap-3">
            <Skeleton className="h-6 w-40" />
            <Skeleton className="h-16 w-full" />
          </CardContent>
        </Card>
      ) : (
        <ProgressPanel
          audit={auditQuery.data}
          onCancel={() => cancelMutation.mutate()}
          cancelPending={cancelMutation.isPending}
          cancelError={cancelMutation.isError ? errorMessage(cancelMutation.error) : null}
        />
      )}

      <div className="grid gap-3">
        <SectionTitle>Executions</SectionTitle>
        {executionsQuery.isError ? (
          <Alert tone="danger">Could not load executions.</Alert>
        ) : executionsQuery.isLoading ? (
          <Card>
            <CardContent className="grid gap-3">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </CardContent>
          </Card>
        ) : executions.length === 0 ? (
          <Card>
            <CardContent className="py-8 text-center text-sm text-secondary">
              No executions yet. They appear as the run is planned and processed.
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="p-0">
              <ExecutionsTable auditId={runId} executions={executions} />
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
