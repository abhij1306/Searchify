'use client';

import { AnalysisProgress } from '@/components/site-health/analysis-progress';
import { DiscoveryProgress } from '@/components/site-health/discovery-progress';
import { HealthDashboard } from '@/components/site-health/health-dashboard';
import { InventorySelection } from '@/components/site-health/inventory-selection';
import { EmptyPhaseCard, TerminalPhaseCard } from '@/components/site-health/screen-states';
import type { useSiteHealthScreen } from '@/lib/site-health/use-site-health-screen';
import type { SiteHealthEntitlement } from '@/lib/api/types';

/**
 * Renders exactly one phase panel (empty / discovering / selection / analyzing
 * / terminal / dashboard) from the screen hook's resolved state. Pure switch —
 * all behavior lives in `useSiteHealthScreen`.
 */
export function PhasePanel({
  screen,
  entitlement,
  projectId,
}: Readonly<{
  screen: ReturnType<typeof useSiteHealthScreen>;
  entitlement: SiteHealthEntitlement;
  projectId: string;
}>) {
  const {
    phase,
    crawl,
    active,
    dashboardQuery,
    pagesQuery,
    projectSelectedTotal,
    projectSelectedError,
    createMutation,
    cancelMutation,
    startCrawl,
    cancelCrawl,
  } = screen;

  if (phase === 'empty') {
    return <EmptyPhaseCard onStart={startCrawl} startPending={createMutation.isPending} />;
  }
  if (!crawl) return null;

  switch (phase) {
    case 'discovering':
      return (
        <DiscoveryProgress
          crawl={crawl}
          entitlement={entitlement}
          active={active}
          onCancel={cancelCrawl}
          cancelPending={cancelMutation.isPending}
        />
      );
    case 'selection':
      return (
        <InventorySelection
          crawl={crawl}
          entitlement={entitlement}
          projectId={projectId}
          // A cancelled crawl keeps its discovered inventory but can no longer
          // run analysis itself — selections persist, and "Start analysis"
          // launches a fresh crawl that seeds them as analyze tasks.
          crawlInactive={!active}
          onStartAnalysis={startCrawl}
          startPending={createMutation.isPending}
        />
      );
    case 'analyzing':
      return (
        <AnalysisProgress
          crawl={crawl}
          pages={pagesQuery.data?.items ?? []}
          // Surface the page-window fetch state so a failed/loading query does
          // not masquerade as a valid empty "no pages" table. React Query keeps
          // the prior `data` during a refetch, so existing rows stay visible;
          // these flags only drive an error alert / initial loading hint.
          pagesError={pagesQuery.isError}
          pagesLoading={pagesQuery.isLoading}
          // Per-project active monitored count — the server-side "selected"
          // total for THIS project's crawl (the workspace-wide dashboard
          // quota would overcount in multi-project workspaces). Null until
          // loaded; the component falls back to pages.length, and the
          // terminal score_summary.selected_count takes precedence once
          // written.
          selectedTotal={projectSelectedTotal}
          selectedError={projectSelectedError}
          onCancel={cancelCrawl}
          cancelPending={cancelMutation.isPending}
        />
      );
    case 'terminal':
      return (
        <TerminalPhaseCard
          crawl={crawl}
          onStart={startCrawl}
          startPending={createMutation.isPending}
        />
      );
    case 'dashboard':
      return dashboardQuery.data ? (
        <HealthDashboard dashboard={dashboardQuery.data} crawl={crawl} active={active} />
      ) : null;
    default:
      return null;
  }
}
