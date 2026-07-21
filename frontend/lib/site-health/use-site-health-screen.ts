'use client';

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { queryKeys } from '@/lib/api/query-keys';
import { siteHealthMutations, siteHealthQueries } from '@/lib/api/site-health';
import type { SiteCrawl, SiteHealthEntitlement } from '@/lib/api/types';
import {
  downloadCrawlExport,
  type ExportFormat,
  type ExportView,
} from '@/lib/site-health/download';
import { useCrawlEvents } from '@/lib/site-health/use-crawl-events';
import {
  inventoryModeForPhase,
  POLL_INTERVAL_MS,
  primaryActionForPhase,
  resolveSiteHealthPhase,
  shouldPollCrawl,
  type InventoryMode,
  type PrimaryAction,
  type SiteHealthPhase,
} from '@/lib/site-health/status';

/**
 * Data orchestration for the Site Health screen (Slice 7).
 *
 * Owns the entitlement / dashboard / pages / discovery-preview / monitored
 * queries, the create/cancel mutations, the export flow, and the phase
 * resolution. Progress is POLLING-FIRST: the crawl + dashboard + pages queries
 * poll while the crawl is active; the credentialed SSE stream is only an
 * invalidation accelerator (a dropped stream never stops progress). Exports are
 * authenticated blob downloads so a non-default workspace's `X-Workspace-Id`
 * is preserved.
 */
export function useSiteHealthScreen(projectId: string | null) {
  const queryClient = useQueryClient();
  const [exportError, setExportError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const entitlementQuery = useQuery(siteHealthQueries.entitlements());

  const dashboardQuery = useQuery({
    ...siteHealthQueries.dashboard(projectId ?? ''),
    enabled: Boolean(projectId),
    refetchInterval: (query) => {
      const polled = query.state.data?.crawl;
      return polled && shouldPollCrawl(polled) ? POLL_INTERVAL_MS : false;
    },
  });

  const crawl: SiteCrawl | null = dashboardQuery.data?.crawl ?? null;
  const active = crawl ? shouldPollCrawl(crawl) : false;
  const plan: SiteHealthEntitlement['plan_key'] = entitlementQuery.data?.plan_key ?? 'free';

  // SSE invalidation accelerator (polling stays the baseline).
  useCrawlEvents(crawl?.id, projectId, active);

  // Poll pages while active so per-page rows advance without a reload. Scoped
  // to `monitored: true` so the per-page table shows only selected rows. This
  // is a bounded WINDOW (first 200 by URL order) for the table + live score
  // preview — with env-raised limits the monitored set may be far larger, so
  // the progress COUNTS come from server counters (crawl `analyzed_count` /
  // `failed_count` and the dashboard quota), never from this page fetch.
  const pagesQuery = useQuery({
    ...siteHealthQueries.pages(crawl?.id ?? '', { limit: 200, monitored: true }),
    enabled: Boolean(crawl?.id),
    refetchInterval: active ? POLL_INTERVAL_MS : false,
  });

  // Per-PROJECT monitored set. Feeds BOTH the phase resolution (an active
  // crawl with a committed monitored set is an analysis run from creation —
  // its analyze tasks are seeded before `analysis_status` leaves 'pending')
  // and the analysis progress totals. The dashboard quota `used` is
  // workspace-wide, so a multi-project workspace would overcount this crawl's
  // queue — count this project's active monitored rows instead. Selection
  // commits write this cache directly (`useMonitoredSelection`), so a commit
  // moves the phase forward without waiting for a refetch.
  const monitoredQuery = useQuery({
    ...siteHealthQueries.monitored(projectId ?? ''),
    enabled: Boolean(projectId),
  });
  const projectSelectedTotal = useMemo(() => {
    const rows = monitoredQuery.data?.monitored_urls;
    if (!rows) return null;
    return rows.filter((row) => row.active).length;
  }, [monitoredQuery.data]);

  const phase: SiteHealthPhase = useMemo(
    () => resolveSiteHealthPhase(crawl, plan, (projectSelectedTotal ?? 0) > 0),
    [crawl, plan, projectSelectedTotal],
  );

  // Canonical-screen view-model: the same layout stays mounted through the
  // whole discover → select → analyze → scored flow; these two modifiers are
  // all that changes (which header control shows, what the inventory section
  // renders). Derived, never stored — the crawl shape is the single source.
  const primaryAction: PrimaryAction = primaryActionForPhase(phase, active);
  const inventoryMode: InventoryMode = inventoryModeForPhase(phase);
  // Surface a failed monitored-count fetch rather than silently disabling the
  // analysis view: the count query is best-effort (the counters degrade to the
  // visible window), but the error is exposed so the screen can note it.
  const projectSelectedError = monitoredQuery.isError;

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

  // "Crawl starting": the user launched a fresh crawl (Start discovery /
  // Start analysis / Re-crawl) and the new run has not yet replaced the
  // dashboard's crawl — either the create request is still in flight, or it
  // succeeded but the dashboard query still returns the previous crawl. The
  // screen keeps its current content frozen behind a starting notice instead
  // of re-resolving the OLD crawl's phase (which is what used to bounce the
  // UI back to the selection list after "Start analysis"). Scoped to the
  // create's own project so a sticky success from another project (after a
  // project switch) can never freeze this screen.
  const crawlStarting =
    createMutation.variables?.project_id === projectId &&
    (createMutation.isPending ||
      (createMutation.isSuccess &&
        createMutation.data != null &&
        crawl?.id !== createMutation.data.id));

  // Once the dashboard shows the created crawl, the create mutation is
  // consumed — reset it so its sticky isSuccess/data can't re-trigger
  // crawlStarting if `crawl` later changes independently (a re-crawl from
  // another tab, a cache revert). Pending and pre-confirmation states are
  // untouched: reset only fires after the id match confirms the handoff.
  const createdCrawlId = createMutation.isSuccess ? (createMutation.data?.id ?? null) : null;
  useEffect(() => {
    if (createdCrawlId != null && crawl?.id === createdCrawlId) {
      createMutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- createMutation is a new object each render; keying on the ids is the stable equivalent.
  }, [createdCrawlId, crawl?.id]);

  const runExport = async (format: ExportFormat, view: ExportView) => {
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

  return {
    entitlementQuery,
    dashboardQuery,
    pagesQuery,
    crawl,
    active,
    phase,
    primaryAction,
    inventoryMode,
    projectSelectedTotal,
    projectSelectedError,
    crawlStarting,
    createMutation,
    cancelMutation,
    startCrawl,
    cancelCrawl,
    runExport,
    exporting,
    exportError,
  };
}
