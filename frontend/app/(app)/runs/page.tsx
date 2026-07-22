'use client';

import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { LaunchDialog } from '@/components/runs/launch-dialog';
import { FilterChip } from '@/components/runs/filter-chip';
import { RunsTable } from '@/components/runs/runs-table';
import { queryKeys } from '@/lib/api/query-keys';
import { runsApi } from '@/lib/api/runs';
import type { Audit } from '@/lib/api/types';
import { shouldPollAudit } from '@/lib/runs/status';
import { useActiveProject } from '@/lib/project/project-context';

/** Poll interval (ms) for the runs list while any run is active. */
const POLL_INTERVAL_MS = 3_000;

/**
 * Status filter chips (designs/shell-runs-midnight.html). "Running" buckets
 * every in-flight status (queued/running/analyzing/reporting/…) — the full
 * eight-state badge system stays visible under "All".
 */
type StatusFilter = 'all' | 'completed' | 'running' | 'failed';

const STATUS_FILTERS: { id: StatusFilter; label: string; match: (audit: Audit) => boolean }[] = [
  { id: 'all', label: 'All', match: () => true },
  { id: 'completed', label: 'Completed', match: (audit) => audit.status === 'completed' },
  { id: 'running', label: 'Running', match: (audit) => shouldPollAudit(audit.status) },
  { id: 'failed', label: 'Failed', match: (audit) => audit.status === 'failed' },
];

/**
 * Runs list screen (F10, design.md §9.7).
 *
 * Lists the active project's audits (status + requested/completed/failed counts
 * + created timestamp) behind status filter chips, with a "Launch audit"
 * button opening the launch dialog. On a successful launch it routes into the
 * new run's detail page. Scoped to the active project via F5 context.
 */
export default function RunsPage() {
  const project = useActiveProject();
  const projectId = project?.id ?? null;
  const router = useRouter();
  const [launchOpen, setLaunchOpen] = useState(false);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');

  const runsQuery = useQuery({
    queryKey: queryKeys.runs.list({ project_id: projectId ?? '' }),
    queryFn: ({ signal }) => runsApi.listAudits({ project_id: projectId as string }, { signal }),
    enabled: Boolean(projectId),
    // Keep statuses/counts live while any run is progressing; stop once all
    // runs are terminal.
    refetchInterval: (query) => {
      const audits = query.state.data;
      return audits?.some((audit) => shouldPollAudit(audit.status)) ? POLL_INTERVAL_MS : false;
    },
  });

  const audits = useMemo(() => runsQuery.data ?? [], [runsQuery.data]);
  const filteredAudits = useMemo(
    () =>
      audits.filter(
        STATUS_FILTERS.find((filter) => filter.id === statusFilter)?.match ?? (() => true),
      ),
    [audits, statusFilter],
  );
  const anyActive = audits.some((audit) => shouldPollAudit(audit.status));

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-center gap-2">
        <div
          className="flex flex-wrap items-center gap-2"
          role="group"
          aria-label="Filter by status"
        >
          {STATUS_FILTERS.map((filter) => (
            <FilterChip
              key={filter.id}
              active={statusFilter === filter.id}
              onClick={() => setStatusFilter(filter.id)}
              count={audits.filter(filter.match).length}
            >
              {filter.label}
            </FilterChip>
          ))}
        </div>
        <Button className="ml-auto" onClick={() => setLaunchOpen(true)} disabled={!projectId}>
          Launch audit
        </Button>
      </div>

      {!projectId ? (
        <Alert tone="info">Select or create a project to launch runs.</Alert>
      ) : runsQuery.isError ? (
        <Alert tone="danger">Could not load runs. Check your connection and try again.</Alert>
      ) : runsQuery.isLoading ? (
        <Card>
          <CardContent className="grid gap-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </CardContent>
        </Card>
      ) : audits.length === 0 ? (
        <Card>
          <CardContent className="grid justify-items-center gap-3 py-16 text-center">
            <p className="text-2xs text-muted font-mono font-medium tracking-[0.08em] uppercase">
              Runs
            </p>
            <p className="font-display text-foreground text-xl font-semibold">No runs yet</p>
            <p className="text-secondary max-w-md text-sm">
              Launch your first audit to measure how AI engines answer questions about your brand.
            </p>
            <Button variant="ghost" className="mt-1" onClick={() => setLaunchOpen(true)}>
              Launch your first audit
            </Button>
          </CardContent>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <CardHeader className="flex-row flex-wrap items-baseline justify-between gap-2 border-b-0 pb-3">
            <CardTitle className="text-base">All runs</CardTitle>
            {anyActive ? (
              <span className="mono text-muted text-2xs inline-flex items-center gap-1.5">
                <span
                  className="bg-accent inline-block size-1.5 animate-pulse rounded-full"
                  aria-hidden
                />
                polling every 3s while a run is active
              </span>
            ) : null}
          </CardHeader>
          <CardContent className="p-0">
            {filteredAudits.length === 0 ? (
              <p className="text-secondary border-border-subtle border-t px-6 py-10 text-center text-sm">
                No{' '}
                {STATUS_FILTERS.find((filter) => filter.id === statusFilter)?.label.toLowerCase()}{' '}
                runs.
              </p>
            ) : (
              <RunsTable audits={filteredAudits} />
            )}
          </CardContent>
        </Card>
      )}

      {projectId ? (
        <LaunchDialog
          open={launchOpen}
          onOpenChange={setLaunchOpen}
          projectId={projectId}
          onLaunched={(audit) => router.push(`/runs/${audit.id}`)}
        />
      ) : null}
    </div>
  );
}
