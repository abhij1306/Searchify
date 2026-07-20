'use client';

import { useMemo, useState } from 'react';
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
  primaryActionForPhase,
  resolveSiteHealthPhase,
  shouldPollCrawl,
  type InventoryMode,
  type PrimaryAction,
  type SiteHealthPhase,
} from '@/lib/site-health/status';

const POLL_INTERVAL_MS = 4_000;

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

  const phase: SiteHealthPhase = useMemo(() => resolveSiteHealthPhase(crawl, plan), [crawl, plan]);

  // Canonical-screen view-model: the same layout stays mounted through the
  // whole discover → select → analyze → scored flow; these two modifiers are
  // all that changes (which header control shows, what the inventory section
  // renders). Derived, never stored — the crawl shape is the single source.
  const primaryAction: PrimaryAction = primaryActionForPhase(phase, active);
  const inventoryMode: InventoryMode = inventoryModeForPhase(phase);

  // Per-PROJECT selected total for the analysis progress bar. The dashboard
  // quota `used` is workspace-wide, so a multi-project workspace would
  // overcount this crawl's queue — count this project's active monitored rows
  // instead. Fetched only while analyzing (the set is frozen during a run).
  const monitoredQuery = useQuery({
    ...siteHealthQueries.monitored(projectId ?? ''),
    enabled: Boolean(projectId) && phase === 'analyzing',
  });
  const projectSelectedTotal = useMemo(() => {
    const rows = monitoredQuery.data?.monitored_urls;
    if (!rows) return null;
    return rows.filter((row) => row.active).length;
  }, [monitoredQuery.data]);
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
  // UI back to the selection list after "Start analysis").
  const crawlStarting =
    createMutation.isPending ||
    (createMutation.isSuccess &&
      createMutation.data != null &&
      crawl?.id !== createMutation.data.id);

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
