'use client';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { PhasePanel } from '@/components/site-health/phase-panel';
import { ScreenHeader, ScreenSkeleton } from '@/components/site-health/screen-states';
import { useProjectContext } from '@/lib/project/project-context';
import { useSiteHealthScreen } from '@/lib/site-health/use-site-health-screen';

/**
 * Site Health screen container (Slice 7).
 *
 * Resolves the active project, then delegates all data orchestration
 * (entitlement, dashboard, pages, mutations, export, phase resolution) to
 * `useSiteHealthScreen` and phase rendering (discovery → selection → analysis
 * → dashboard) to `PhasePanel`.
 */
export function SiteHealthScreen() {
  const { activeProject, isLoading: projectLoading } = useProjectContext();
  const projectId = activeProject?.id ?? null;

  const screen = useSiteHealthScreen(projectId);
  const {
    entitlementQuery,
    dashboardQuery,
    crawl,
    active,
    phase,
    createMutation,
    startCrawl,
    runExport,
    exporting,
    exportError,
  } = screen;

  if (projectLoading || (projectId && (entitlementQuery.isLoading || dashboardQuery.isLoading))) {
    return <ScreenSkeleton />;
  }

  if (!projectId) {
    return (
      <div className="grid gap-6">
        <ScreenHeader />
        <Alert tone="info">Select or create a project to analyze its site health.</Alert>
      </div>
    );
  }

  if (entitlementQuery.isError || dashboardQuery.isError) {
    return (
      <div className="grid gap-6">
        <ScreenHeader />
        <Alert tone="danger">Could not load Site Health. Please refresh.</Alert>
      </div>
    );
  }

  const headerActions =
    crawl && (phase === 'dashboard' || phase === 'terminal') ? (
      <div className="flex items-center gap-2">
        {phase === 'dashboard' ? (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => runExport('csv', 'pages')}
            disabled={exporting}
          >
            {exporting ? 'Exporting…' : 'Export'}
          </Button>
        ) : null}
        <Button size="sm" onClick={startCrawl} disabled={createMutation.isPending || active}>
          {createMutation.isPending ? 'Starting…' : 'Re-crawl now'}
        </Button>
      </div>
    ) : null;

  return (
    <div className="grid gap-6">
      <ScreenHeader actions={headerActions} />

      {exportError ? <Alert tone="danger">{exportError}</Alert> : null}
      {createMutation.isError ? (
        <Alert tone="danger">Could not start a crawl. It may already be running.</Alert>
      ) : null}

      <PhasePanel screen={screen} entitlement={entitlementQuery.data!} projectId={projectId} />
    </div>
  );
}
