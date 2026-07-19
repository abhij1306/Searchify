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
    discoveryPreviewQuery,
    projectSelectedTotal,
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
          previewRows={discoveryPreviewQuery.data?.items ?? []}
          onCancel={cancelCrawl}
          cancelPending={cancelMutation.isPending}
        />
      );
    case 'selection':
      return (
        <InventorySelection crawl={crawl} entitlement={entitlement} projectId={projectId} />
      );
    case 'analyzing':
      return (
        <AnalysisProgress
          crawl={crawl}
          pages={pagesQuery.data?.items ?? []}
          // Per-project active monitored count — the server-side "selected"
          // total for THIS project's crawl (the workspace-wide dashboard
          // quota would overcount in multi-project workspaces). Null until
          // loaded; the component falls back to pages.length, and the
          // terminal score_summary.selected_count takes precedence once
          // written.
          selectedTotal={projectSelectedTotal}
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
