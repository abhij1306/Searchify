'use client';

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { EngineComparison } from '@/components/visibility/engine-comparison';
import { RankingsTable } from '@/components/visibility/rankings-table';
import { VisibilityEmptyState } from '@/components/visibility/empty-state';
import { VisibilityScoreCard } from '@/components/visibility/score-card';
import { VisibilityToolbar } from '@/components/visibility/visibility-toolbar';
import { queryKeys } from '@/lib/api/query-keys';
import { runsApi } from '@/lib/api/runs';
import { visibilityApi } from '@/lib/api/visibility';
import { useProjectContext } from '@/lib/project/project-context';
import { DEFAULT_FILTERS, toRunOptions, type VisibilityFilters } from '@/lib/visibility/dashboard';

/**
 * Visibility dashboard container (F9).
 *
 * Resolves the active project (F5 context), lists its audits to build the run
 * selector, and fetches the selected-run projection from B6's
 * `/projects/{id}/visibility?audit_id=` via `visibility.ts`. The selected run
 * defaults to the latest dashboard-ready audit; engine / prompt-type filters
 * are folded into the query key so switching them re-derives the view. Renders
 * the empty state when the project has no completed runs.
 */
export function VisibilityDashboard() {
  const { activeProject, isLoading: isProjectLoading } = useProjectContext();
  const projectId = activeProject?.id ?? null;

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [filters, setFilters] = useState<VisibilityFilters>(DEFAULT_FILTERS);

  const auditsQuery = useQuery({
    queryKey: queryKeys.runs.list({ project_id: projectId ?? '' }),
    queryFn: ({ signal }) => runsApi.listAudits({ project_id: projectId! }, { signal }),
    enabled: Boolean(projectId),
  });

  const runOptions = useMemo(() => toRunOptions(auditsQuery.data ?? []), [auditsQuery.data]);

  // The effective run: an explicit selection that still exists, else the latest
  // dashboard-ready run (which is also what the endpoint defaults to).
  const activeRunId = useMemo(() => {
    if (selectedRunId && runOptions.some((run) => run.id === selectedRunId)) {
      return selectedRunId;
    }
    return runOptions[0]?.id ?? null;
  }, [runOptions, selectedRunId]);

  const hasRuns = runOptions.length > 0;

  const visibilityQuery = useQuery({
    queryKey: queryKeys.visibility.project(projectId ?? '', activeRunId ?? undefined, {
      engine: filters.engine,
      promptType: filters.promptType,
    }),
    queryFn: ({ signal }) =>
      visibilityApi.getProjectVisibility(
        projectId!,
        activeRunId ? { audit_id: activeRunId } : undefined,
        { signal },
      ),
    enabled: Boolean(projectId) && hasRuns,
  });

  const isBootstrapping = isProjectLoading || (Boolean(projectId) && auditsQuery.isLoading);

  if (isBootstrapping) {
    return <DashboardSkeleton />;
  }

  if (!projectId) {
    return (
      <Alert tone="info">
        Select or create a project to see its AI-visibility results.
      </Alert>
    );
  }

  if (auditsQuery.isError) {
    return (
      <Alert tone="danger">
        Could not load this project&apos;s runs. Check your connection and try again.
      </Alert>
    );
  }

  if (!hasRuns) {
    return <VisibilityEmptyState />;
  }

  const visibility = visibilityQuery.data;

  return (
    <div className="grid gap-6">
      <VisibilityToolbar
        runs={runOptions}
        activeRunId={activeRunId}
        onSelectRun={setSelectedRunId}
        filters={filters}
        onChangeFilters={setFilters}
        visibility={visibility}
      />

      {visibilityQuery.isError ? (
        <Alert tone="danger">
          Could not load visibility metrics for this run. Try another run or refresh.
        </Alert>
      ) : null}

      {visibilityQuery.isLoading || !visibility ? (
        <DashboardSkeleton />
      ) : (
        <>
          <div className="grid gap-6 lg:grid-cols-[minmax(260px,1fr)_2fr]">
            <VisibilityScoreCard visibility={visibility} />
            <RankingsTable visibility={visibility} />
          </div>
          <EngineComparison visibility={visibility} filter={filters.engine} />
        </>
      )}
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="grid gap-6" aria-hidden>
      <div className="grid gap-6 lg:grid-cols-[minmax(260px,1fr)_2fr]">
        <Card>
          <CardContent className="grid justify-items-center gap-4">
            <Skeleton className="size-28 rounded-full" />
            <Skeleton className="h-4 w-40" />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="grid gap-3">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardContent className="grid gap-4 md:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
