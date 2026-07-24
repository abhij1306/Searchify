'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { AccentEyebrow } from '@/components/ui/eyebrow';
import { Skeleton } from '@/components/ui/skeleton';
import { displayHeadingLgClasses } from '@/components/ui/typography';
import { OpportunitiesCatalog } from '@/components/opportunities/opportunities-catalog';
import {
  opportunitiesApi,
  opportunitiesMutations,
  opportunitiesQueries,
} from '@/lib/api/opportunities';
import { queryKeys } from '@/lib/api/query-keys';
import type { OpportunitySummary } from '@/lib/api/types';
import { useProjectContext } from '@/lib/project/project-context';
import { severityCount } from '@/lib/site-health/issues';
import { formatAudited } from '@/lib/site-health/status';

/**
 * Opportunities screen container (approved mockup: summary strip + catalog).
 *
 * Resolves the active project, renders the latest recompute snapshot as the
 * summary strip (API-owned counts — never a client re-count) with the
 * Recompute action + export links, then the priority-sorted catalog. A
 * project that has never been recomputed gets the empty state with a
 * Recompute CTA (and copy pointing at running an audit/crawl first).
 */
export function OpportunitiesScreen() {
  const { activeProject, isLoading: projectLoading } = useProjectContext();
  const projectId = activeProject?.id ?? null;

  const summaryQuery = useQuery({
    ...opportunitiesQueries.summary(projectId ?? ''),
    enabled: Boolean(projectId),
  });
  const summary = summaryQuery.data ?? null;
  const loading = projectLoading || (Boolean(projectId) && summaryQuery.isLoading);

  return (
    <div className="grid gap-6">
      {!projectLoading && !projectId ? (
        <Alert tone="info">Select or create a project to view its opportunities.</Alert>
      ) : loading ? (
        <div className="grid gap-4">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : summaryQuery.isError ? (
        <Alert tone="danger">Could not load opportunities. Please refresh.</Alert>
      ) : projectId && summary && !summary.computed ? (
        <NeverComputed projectId={projectId} />
      ) : projectId && summary ? (
        <>
          <SummaryStrip projectId={projectId} summary={summary} />
          <OpportunitiesCatalog projectId={projectId} />
        </>
      ) : null}
    </div>
  );
}

/** Recompute mutation + invalidation shared by the strip and the empty state. */
function useRecompute() {
  const queryClient = useQueryClient();
  return useMutation({
    ...opportunitiesMutations.recompute(),
    onSuccess: async () => {
      // A recompute supersedes the whole live set — the entire namespace
      // (summary, every list page/filter, details) is stale.
      await queryClient.invalidateQueries({ queryKey: queryKeys.opportunities.all });
    },
  });
}

function RecomputeButton({
  projectId,
  variant = 'primary',
}: Readonly<{ projectId: string; variant?: 'primary' | 'secondary' }>) {
  const recompute = useRecompute();
  return (
    <Button
      variant={variant}
      size="sm"
      disabled={recompute.isPending}
      onClick={() => recompute.mutate({ projectId })}
    >
      {recompute.isPending ? 'Recomputing…' : 'Recompute'}
    </Button>
  );
}

function NeverComputed({ projectId }: Readonly<{ projectId: string }>) {
  return (
    <Card>
      <CardContent className="grid justify-items-center gap-3 py-10 text-center">
        <AccentEyebrow>Opportunities</AccentEyebrow>
        <h2 className={displayHeadingLgClasses}>No opportunities computed yet</h2>
        <p className="text-secondary max-w-md text-sm">
          Opportunities are derived from your latest visibility audit and Site Health crawl.
          Run those first, then recompute to surface the highest-priority actions here.
        </p>
        <RecomputeButton projectId={projectId} variant="secondary" />
      </CardContent>
    </Card>
  );
}

function SummaryTile({ label, value }: Readonly<{ label: string; value: number }>) {
  return (
    <div className="grid gap-0.5">
      <span className="text-2xs text-muted font-mono tracking-[0.08em] uppercase">
        {label}
      </span>
      <span className="mono text-foreground text-xl font-semibold">{value}</span>
    </div>
  );
}

function SummaryStrip({
  projectId,
  summary,
}: Readonly<{ projectId: string; summary: OpportunitySummary }>) {
  const severityCounts = summary.counts_by_severity;
  const typeCounts = summary.counts_by_type;
  return (
    <Card>
      <CardContent className="grid gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="grid gap-1">
            <AccentEyebrow>Opportunity snapshot</AccentEyebrow>
            <p className="text-muted text-xs">
              Computed {formatAudited(summary.computed_at)} · analyzer{' '}
              {summary.analyzer_version} · formula {summary.formula_version}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" asChild>
              <a href={opportunitiesApi.exportUrl(projectId, 'csv')}>Export CSV</a>
            </Button>
            <Button variant="secondary" size="sm" asChild>
              <a href={opportunitiesApi.exportUrl(projectId, 'md')}>Export MD</a>
            </Button>
            <RecomputeButton projectId={projectId} />
          </div>
        </div>
        <div className="flex flex-wrap gap-x-8 gap-y-3">
          <SummaryTile label="Total" value={summary.total_count} />
          <SummaryTile label="Open" value={summary.counts_by_status.open ?? 0} />
          <SummaryTile label="High" value={severityCount(severityCounts, 'high')} />
          <SummaryTile label="Medium" value={severityCount(severityCounts, 'medium')} />
          <SummaryTile label="Low" value={severityCount(severityCounts, 'low')} />
          <SummaryTile label="Visibility" value={typeCounts.visibility ?? 0} />
          <SummaryTile label="Site" value={typeCounts.site ?? 0} />
          <SummaryTile label="Topic" value={typeCounts.topic ?? 0} />
        </div>
      </CardContent>
    </Card>
  );
}
