'use client';

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { PageTitle } from '@/components/ui/typography';
import { AnalysisProgress } from '@/components/site-health/analysis-progress';
import { DiscoveryProgress } from '@/components/site-health/discovery-progress';
import { HealthDashboard } from '@/components/site-health/health-dashboard';
import { InventorySelection } from '@/components/site-health/inventory-selection';
import { queryKeys } from '@/lib/api/query-keys';
import { siteHealthApi, siteHealthMutations, siteHealthQueries } from '@/lib/api/site-health';
import { useProjectContext } from '@/lib/project/project-context';
import type { SiteCrawl } from '@/lib/api/types';
import { downloadCrawlExport } from '@/lib/site-health/download';
import { useCrawlEvents } from '@/lib/site-health/use-crawl-events';
import {
  isDiscoveryTerminal,
  shouldPollCrawl,
} from '@/lib/site-health/status';

const POLL_INTERVAL_MS = 4_000;

/** Which phase of the Site Health flow to render for the active crawl. */
type Phase = 'empty' | 'discovering' | 'selection' | 'analyzing' | 'dashboard';

function resolvePhase(crawl: SiteCrawl | null, plan: 'free' | 'starter'): Phase {
  if (!crawl) return 'empty';
  // Terminal crawls with score data → completed dashboard.
  if (['completed', 'partially_completed'].includes(crawl.status) || crawl.score_summary) {
    // Still analyzing pages? treat running analysis as the analyzing phase.
    if (crawl.analysis_status === 'running' || crawl.analysis_status === 'pending') {
      if (crawl.analyzed_count > 0) return 'dashboard';
    }
    return 'dashboard';
  }
  // Discovery still running.
  if (!isDiscoveryTerminal(crawl.discovery_status)) return 'discovering';
  // Discovery done. Free auto-analyzes its sample (no manual selection); Starter
  // stages a monitored set unless analysis has already started.
  if (crawl.analysis_status === 'running') return 'analyzing';
  if (plan === 'starter' && crawl.analysis_status === 'pending') return 'selection';
  return 'analyzing';
}

/**
 * Site Health screen container (Slice 7).
 *
 * Resolves the active project, entitlement, and latest crawl, then renders the
 * right phase (discovery → selection → analysis → dashboard). Progress is
 * POLLING-FIRST: the crawl + dashboard + pages queries poll while the crawl is
 * active; the credentialed SSE stream is only an invalidation accelerator (a
 * dropped stream never stops progress). Exports are authenticated blob
 * downloads so a non-default workspace's `X-Workspace-Id` is preserved.
 */
export function SiteHealthScreen() {
  const { activeProject, isLoading: projectLoading } = useProjectContext();
  const projectId = activeProject?.id ?? null;
  const queryClient = useQueryClient();
  const [exportError, setExportError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const entitlementQuery = useQuery(siteHealthQueries.entitlements());

  const dashboardQuery = useQuery({
    ...siteHealthQueries.dashboard(projectId ?? ''),
    enabled: Boolean(projectId),
    refetchInterval: (query) => {
      const crawl = query.state.data?.crawl;
      return crawl && shouldPollCrawl(crawl) ? POLL_INTERVAL_MS : false;
    },
  });

  const crawl = dashboardQuery.data?.crawl ?? null;
  const active = crawl ? shouldPollCrawl(crawl) : false;
  const plan = entitlementQuery.data?.plan_key ?? 'free';

  // SSE invalidation accelerator (polling stays the baseline).
  useCrawlEvents(crawl?.id, projectId, active);

  // Poll pages while active so per-page rows advance without a reload.
  const pagesQuery = useQuery({
    ...siteHealthQueries.pages(crawl?.id ?? '', { limit: 200 }),
    enabled: Boolean(crawl?.id),
    refetchInterval: active ? POLL_INTERVAL_MS : false,
  });

  const phase = useMemo(() => resolvePhase(crawl, plan), [crawl, plan]);

  // Preview the admitted-URL inventory during discovery (bounded to this crawl).
  const discoveryPreviewQuery = useQuery({
    ...siteHealthQueries.inventory(crawl?.id ?? '', { limit: 25 }),
    enabled: Boolean(crawl?.id) && phase === 'discovering',
    refetchInterval: active ? POLL_INTERVAL_MS : false,
  });

  const createMutation = useMutation({
    ...siteHealthMutations.createCrawl(),
    onSuccess: () => {
      if (projectId) {
        queryClient.invalidateQueries({ queryKey: queryKeys.siteHealth.dashboard(projectId) });
      }
    },
  });
  const cancelMutation = useMutation({
    ...siteHealthMutations.cancelCrawl(),
    onSuccess: (updated) => {
      if (projectId) {
        queryClient.setQueryData(queryKeys.siteHealth.dashboard(projectId), {
          ...dashboardQuery.data,
          crawl: updated,
        });
        queryClient.invalidateQueries({ queryKey: queryKeys.siteHealth.dashboard(projectId) });
      }
    },
  });

  const startCrawl = () => projectId && createMutation.mutate({ project_id: projectId });
  const cancelCrawl = () => crawl && cancelMutation.mutate(crawl.id);

  const runExport = async (format: 'csv' | 'md', view: 'inventory' | 'pages' | 'issues') => {
    if (!crawl) return;
    setExportError(null);
    setExporting(true);
    try {
      await downloadCrawlExport(crawl.id, format, view);
    } catch {
      setExportError('Export failed. Please try again.');
    } finally {
      setExporting(false);
    }
  };

  if (projectLoading || (projectId && (entitlementQuery.isLoading || dashboardQuery.isLoading))) {
    return <ScreenSkeleton />;
  }

  if (!projectId) {
    return (
      <div className="grid gap-6">
        <Header />
        <Alert tone="info">Select or create a project to analyze its site health.</Alert>
      </div>
    );
  }

  if (entitlementQuery.isError || dashboardQuery.isError) {
    return (
      <div className="grid gap-6">
        <Header />
        <Alert tone="danger">Could not load Site Health. Please refresh.</Alert>
      </div>
    );
  }

  const entitlement = entitlementQuery.data!;
  const previewRows = pagesQuery.data?.items ?? [];

  return (
    <div className="grid gap-6">
      <Header
        actions={
          crawl && phase === 'dashboard' ? (
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => runExport('csv', 'pages')}
                disabled={exporting}
              >
                {exporting ? 'Exporting…' : 'Export'}
              </Button>
              <Button size="sm" onClick={startCrawl} disabled={createMutation.isPending}>
                {createMutation.isPending ? 'Starting…' : 'Re-crawl now'}
              </Button>
            </div>
          ) : null
        }
      />

      {exportError ? <Alert tone="danger">{exportError}</Alert> : null}
      {createMutation.isError ? (
        <Alert tone="danger">Could not start a crawl. It may already be running.</Alert>
      ) : null}

      {phase === 'empty' ? (
        <Card>
          <CardContent className="grid gap-3 py-8 text-center">
            <p className="text-sm text-secondary">
              Discover and analyze your site&apos;s pages for AI search optimization.
            </p>
            <div className="flex justify-center">
              <Button onClick={startCrawl} disabled={createMutation.isPending}>
                {createMutation.isPending ? 'Starting…' : 'Start discovery'}
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {phase === 'discovering' && crawl ? (
        <DiscoveryProgress
          crawl={crawl}
          entitlement={entitlement}
          previewRows={discoveryPreviewQuery.data?.items ?? []}
          onCancel={cancelCrawl}
          cancelPending={cancelMutation.isPending}
        />
      ) : null}

      {phase === 'selection' && crawl ? (
        <InventorySelection crawl={crawl} entitlement={entitlement} projectId={projectId} />
      ) : null}

      {phase === 'analyzing' && crawl ? (
        <AnalysisProgress
          crawl={crawl}
          pages={previewRows}
          onCancel={cancelCrawl}
          cancelPending={cancelMutation.isPending}
        />
      ) : null}

      {phase === 'dashboard' && crawl && dashboardQuery.data ? (
        <HealthDashboard dashboard={dashboardQuery.data} crawl={crawl} active={active} />
      ) : null}
    </div>
  );
}

function Header({ actions }: Readonly<{ actions?: React.ReactNode }>) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <PageTitle kicker="On Page">Site Health</PageTitle>
        <p className="mt-1 max-w-2xl text-sm text-secondary">
          Discover and analyze your site&apos;s pages for AI search optimization.
        </p>
      </div>
      {actions}
    </div>
  );
}

function ScreenSkeleton() {
  return (
    <div className="grid gap-6" aria-hidden>
      <Skeleton className="h-8 w-48" />
      <Card>
        <CardContent className="grid gap-3">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-40 w-full" />
        </CardContent>
      </Card>
    </div>
  );
}
