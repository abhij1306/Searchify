'use client';

import { InventorySection } from '@/components/site-health/inventory-section';
import { ScoreSection } from '@/components/site-health/score-section';
import { StatusStrip } from '@/components/site-health/status-strip';
import type { useSiteHealthScreen } from '@/lib/site-health/use-site-health-screen';
import type { SiteHealthEntitlement } from '@/lib/api/types';

/**
 * The canonical Site Health dashboard layout.
 *
 * ONE composed screen that stays mounted through the entire discover → select
 * → analyze → scored lifecycle: a status/progress strip, the score cards, and
 * the page inventory. Phase changes update each section's DATA and mode —
 * they never swap the layout for a different panel, so starting, cancelling,
 * or finishing a crawl visibly updates the screen the user is already on.
 * (The per-URL crawl detail view and the issues screen remain the only other
 * screens in the flow.)
 */
export function SiteHealthDashboardLayout({
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
    inventoryMode,
    crawl,
    active,
    dashboardQuery,
    pagesQuery,
    projectSelectedTotal,
    projectSelectedError,
    crawlStarting,
    createMutation,
    cancelMutation,
    startCrawl,
  } = screen;

  return (
    <div className="grid gap-6" data-testid="site-health-canonical">
      <StatusStrip
        crawl={crawl}
        phase={phase}
        entitlement={entitlement}
        cancelPending={cancelMutation.isPending}
        crawlStarting={crawlStarting}
        pages={pagesQuery.data?.items ?? []}
        selectedTotal={projectSelectedTotal}
        selectedError={projectSelectedError}
      />

      <ScoreSection
        crawl={crawl}
        dashboard={dashboardQuery.data}
        pages={pagesQuery.data?.items ?? []}
        analyzing={phase === 'analyzing'}
        selectedTotal={projectSelectedTotal}
      />

      <InventorySection
        mode={inventoryMode}
        crawl={crawl}
        entitlement={entitlement}
        projectId={projectId}
        active={active}
        pages={pagesQuery.data?.items ?? []}
        // Surface the page-window fetch state so a failed/loading query does
        // not masquerade as a valid empty "no pages" table. React Query keeps
        // the prior `data` during a refetch, so existing rows stay visible.
        pagesError={pagesQuery.isError}
        pagesLoading={pagesQuery.isLoading}
        onStartAnalysis={startCrawl}
        startPending={createMutation.isPending}
      />
    </div>
  );
}
