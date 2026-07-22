'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
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
          <CardContent className="grid justify-items-center gap-3 py-10 text-center">
            <span className="text-accent-text text-2xs inline-flex items-center gap-1.5 font-mono font-medium tracking-[0.08em] uppercase">
              <span className="bg-accent size-1.5 rounded-full" aria-hidden />
              Issues
            </span>
            <h2 className="font-display text-foreground text-lg font-semibold">
              No Site Health crawl yet
            </h2>
            <p className="text-secondary max-w-md text-sm">
              Run Site Health to discover and analyze this project&apos;s pages — grouped issues
              will appear here once a crawl finishes.
            </p>
            <Button variant="secondary" asChild>
              <Link href="/site-health">Go to Site Health</Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <IssuesCatalog crawlId={crawl.id} />
      )}
    </div>
  );
}
