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
 * → analyze → scored lifecycle: the score cards, a compact status/progress
 * row, and the page inventory. Phase changes update each section's DATA and
 * mode — they never swap the layout for a different panel, so starting,
 * cancelling, or finishing a crawl visibly updates the screen the user is
 * already on. (The per-URL crawl detail view and the issues screen remain the
 * only other screens in the flow.)
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
    cancelMutation,
    startCrawl,
  } = screen;  return (
    <div className="grid gap-6" data-testid="site-health-canonical">
      <ScoreSection
        crawl={crawl}
        dashboard={dashboardQuery.data}
        pages={pagesQuery.data?.items ?? []}
        // Live running-mean fallback applies whenever analysis may still be
        // producing scores — the analyzing phase, and an ACTIVE crawl already
        // showing a mid-run dashboard projection (phase 'dashboard' via
        // hasScoreData with null metric fields).
        analyzing={phase === 'analyzing' || (phase === 'dashboard' && active)}
        selectedTotal={projectSelectedTotal}
      />

      {/* One compact row under the score cards — narration + inline counters,
          never a separate full-height progress panel. */}
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

      <InventorySection
        mode={inventoryMode}
        crawl={crawl}
        entitlement={entitlement}
        projectId={projectId}
        active={active}
        onStartAnalysis={startCrawl}
        // Disabled for the FULL starting window (create in flight AND the
        // post-success gap until the dashboard returns the new crawl) so a
        // second click can never fire a duplicate create.
        startPending={crawlStarting}
      />
    </div>
  );
}
