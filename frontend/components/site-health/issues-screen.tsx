'use client';

import { useQuery } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { PageTitle } from '@/components/ui/typography';
import { IssuesCatalog } from '@/components/site-health/issues-catalog';
import { siteHealthQueries } from '@/lib/api/site-health';
import { useProjectContext } from '@/lib/project/project-context';

/**
 * Issues screen container (Slice 8, mockup 710).
 *
 * Resolves the active project's current (latest/selected) crawl via the
 * dashboard projection, then renders the grouped Issues catalog for that
 * crawl. If no crawl has produced issues yet, it directs the user to run Site
 * Health first (the catalog is per-crawl and there is nothing to group).
 */
export function IssuesScreen() {
  const { activeProject, isLoading: projectLoading } = useProjectContext();
  const projectId = activeProject?.id ?? null;

  const dashboardQuery = useQuery({
    ...siteHealthQueries.dashboard(projectId ?? ''),
    enabled: Boolean(projectId),
  });

  const crawl = dashboardQuery.data?.crawl ?? null;
  const loading = projectLoading || (Boolean(projectId) && dashboardQuery.isLoading);

  return (
    <div className="grid gap-6">
      <header className="grid gap-1">
        <p className="text-2xs font-semibold uppercase tracking-wider text-accent-text">On Page</p>
        <PageTitle>Issues</PageTitle>
        <p className="max-w-2xl text-sm text-secondary">
          All issues detected across your selected monitored pages, grouped by type. Fix the
          highest-severity issues first to improve both Technical and AEO scores.
        </p>
      </header>

      {!projectLoading && !projectId ? (
        <Alert tone="info">Select or create a project to view its Site Health issues.</Alert>
      ) : loading ? (
        <div className="grid gap-4">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : dashboardQuery.isError ? (
        <Alert tone="danger">Could not load Site Health. Please refresh.</Alert>
      ) : !crawl ? (
        <Card>
          <CardContent className="text-sm text-secondary">
            No Site Health crawl has run for this project yet. Run Site Health to discover and
            analyze pages, then issues will appear here.
          </CardContent>
        </Card>
      ) : (
        <IssuesCatalog crawlId={crawl.id} />
      )}
    </div>
  );
}
