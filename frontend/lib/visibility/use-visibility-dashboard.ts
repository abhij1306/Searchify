'use client';

import { useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';

import type { EngineFilter } from '@/components/visibility/visibility-toolbar';
import { queryKeys } from '@/lib/api/query-keys';
import { runsApi } from '@/lib/api/runs';
import { visibilityApi } from '@/lib/api/visibility';
import {
  findActiveRun,
  isEvidenceTab,
  normalizeTab,
  toPromptOptions,
  toRunOptions,
  type VisibilityTab,
} from '@/lib/visibility/dashboard';
import { shouldPollAudit } from '@/lib/runs/status';
import { rangeToFrom, type TrendGranularity, type TrendRange } from '@/lib/visibility/trends';

/** Newest-window size for the shared execution-evidence request (backend max 500). */
export const EVIDENCE_LIMIT = 100;

/**
 * Poll interval (ms) for the audits list while a run is in progress, matching
 * the run-detail page's cadence. Polling stops once every run is terminal, so
 * a finished run's snapshot appears here without a remount.
 */
export const ACTIVE_RUN_POLL_MS = 3_000;

/**
 * The Visibility workspace's URL-synced tab + shared filter state.
 *
 * The active tab is mirrored in `?tab=` (invalid values fall back to Overview)
 * so refresh / back / forward preserve it; local state keeps it responsive and
 * re-syncs from the URL on back/forward navigation. Shared filter STATE lives
 * here and persists across tab switches; hidden controls keep their state.
 * Ownership (plan §IA): selected run → Overview + both evidence tabs; logical
 * engine → all four; prompt → both evidence tabs; date range → Trends + both
 * evidence tabs; granularity → Trends only.
 */
export function useVisibilityFilters() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const urlTab = normalizeTab(searchParams?.get('tab'));

  const [activeTab, setActiveTab] = useState<VisibilityTab>(urlTab);
  useEffect(() => {
    // Intentional URL→state sync (external navigation is the source of truth).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setActiveTab(urlTab);
  }, [urlTab]);

  // Shared filter state (persists across tab switches).
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [engine, setEngine] = useState<EngineFilter>('all');
  const [promptId, setPromptId] = useState<string | null>(null);
  const [range, setRange] = useState<TrendRange>('90d');
  const [granularity, setGranularity] = useState<TrendGranularity>('run');

  function selectTab(tab: VisibilityTab) {
    setActiveTab(tab);
    const params = new URLSearchParams(searchParams?.toString() ?? '');
    params.set('tab', tab);
    router.replace(`${pathname}?${params.toString()}`);
  }

  // A narrowing filter (engine, bounded range, or a specific prompt) is active —
  // used to explain a filtered-empty result vs a genuinely empty history.
  const isFiltered = engine !== 'all' || range !== 'all' || promptId !== null;
  const isTrendFiltered = engine !== 'all' || range !== 'all';

  function clearEvidenceFilters() {
    setEngine('all');
    setRange('all');
    setPromptId(null);
  }

  return {
    activeTab,
    selectTab,
    selectedRunId,
    setSelectedRunId,
    engine,
    setEngine,
    promptId,
    setPromptId,
    range,
    setRange,
    granularity,
    setGranularity,
    isFiltered,
    isTrendFiltered,
    clearEvidenceFilters,
  };
}

/**
 * The project's dashboard-ready runs: the audits list, the run-selector
 * options, and the effective run — an explicit selection that still exists,
 * else the latest dashboard-ready run (which is also what the endpoint
 * defaults to when `audit_id` is omitted).
 */
function useRunSelection(projectId: string | null, selectedRunId: string | null) {
  const auditsQuery = useQuery({
    queryKey: queryKeys.runs.list({ project_id: projectId ?? '' }),
    queryFn: ({ signal }) => runsApi.listAudits({ project_id: projectId! }, { signal }),
    enabled: Boolean(projectId),
    // While any run is still progressing, keep the audits list fresh so an
    // in-progress run is visible here (not only on /runs/[runId]) and its
    // snapshot appears the moment it completes. Stops when all runs are
    // terminal.
    refetchInterval: (query) => {
      const audits = query.state.data;
      return audits?.some((audit) => shouldPollAudit(audit.status)) ? ACTIVE_RUN_POLL_MS : false;
    },
  });

  const runOptions = useMemo(() => toRunOptions(auditsQuery.data ?? []), [auditsQuery.data]);
  const activeRun = useMemo(() => findActiveRun(auditsQuery.data ?? []), [auditsQuery.data]);

  const activeRunId = useMemo(() => {
    if (selectedRunId && runOptions.some((run) => run.id === selectedRunId)) {
      return selectedRunId;
    }
    return runOptions[0]?.id ?? null;
  }, [runOptions, selectedRunId]);

  return {
    auditsQuery,
    runOptions,
    activeRun,
    activeRunId,
    hasRuns: runOptions.length > 0,
  };
}

/**
 * The shared execution-evidence queries for the two evidence tabs. ONE
 * identical cache key drives both tabs, so switching between Mentions &
 * Citations and Query Fanout reuses the cache instead of refetching.
 * `audit_id` + date bound intersect server-side.
 *
 * Prompt options for the evidence prompt selector must NOT collapse when a
 * prompt is selected, so they are derived from a parallel evidence query that
 * keeps the run/engine/date scope but omits `prompt_id`. When no prompt is
 * selected that key is identical to the main evidence query, so it reuses the
 * cache and issues no extra request; only a selected prompt filter triggers a
 * second (unfiltered-by-prompt) fetch to keep the list stable.
 */
function useEvidenceQueries(
  projectId: string | null,
  enabled: boolean,
  scope: Readonly<{
    activeRunId: string | null;
    promptId: string | null;
    engineParam: string | undefined;
    fromParam: string | undefined;
  }>,
) {
  const { activeRunId, promptId, engineParam, fromParam } = scope;
  const evidenceParams = {
    audit_id: activeRunId ?? undefined,
    prompt_id: promptId ?? undefined,
    engine: engineParam,
    from: fromParam,
    limit: EVIDENCE_LIMIT,
  };
  const keyFilters = {
    audit_id: activeRunId ?? null,
    engine: engineParam ?? null,
    from: fromParam ?? null,
    limit: EVIDENCE_LIMIT,
  };

  const evidenceQuery = useQuery({
    queryKey: queryKeys.visibility.evidence(projectId ?? '', {
      ...keyFilters,
      prompt_id: promptId ?? null,
    }),
    queryFn: ({ signal }) =>
      visibilityApi.getVisibilityEvidence(projectId!, evidenceParams, { signal }),
    enabled,
  });

  const promptOptionsQuery = useQuery({
    queryKey: queryKeys.visibility.evidence(projectId ?? '', {
      ...keyFilters,
      prompt_id: null,
    }),
    queryFn: ({ signal }) =>
      visibilityApi.getVisibilityEvidence(
        projectId!,
        { ...evidenceParams, prompt_id: undefined },
        { signal },
      ),
    enabled,
  });
  const promptOptions = useMemo(
    () => toPromptOptions(promptOptionsQuery.data?.items ?? []),
    [promptOptionsQuery.data],
  );

  return { evidenceQuery, promptOptions };
}

/**
 * The Visibility workspace's per-tab queries. Only the relevant query runs per
 * tab: the selected-run projection for Overview, the trend series for Trends,
 * and the shared execution-evidence query (one identical cache key) for either
 * evidence tab — so switching between the two evidence tabs reuses the cache.
 */
export function useVisibilityQueries(
  projectId: string | null,
  filters: ReturnType<typeof useVisibilityFilters>,
) {
  const { activeTab, selectedRunId, engine, promptId, range, granularity } = filters;

  const { auditsQuery, runOptions, activeRun, activeRunId, hasRuns } = useRunSelection(
    projectId,
    selectedRunId,
  );

  const evidenceTab = isEvidenceTab(activeTab);
  const engineParam = engine === 'all' ? undefined : engine;
  // Resolve the range preset to a `from` bound once per range change. Computing
  // it inline would call `new Date()` on every render and churn the query key.
  const fromParam = useMemo(() => rangeToFrom(range), [range]);

  // Overview: the selected-run projection. Enabled only on the Overview tab.
  // The projection is NOT engine-scoped server-side (the endpoint takes only
  // `audit_id`); engine is applied client-side in `EngineComparison`. So engine
  // is deliberately absent from the key — including it would force a refetch of
  // identical data (and a skeleton flash) on every engine change.
  const visibilityQuery = useQuery({
    queryKey: queryKeys.visibility.project(projectId ?? '', activeRunId ?? undefined),
    queryFn: ({ signal }) =>
      visibilityApi.getProjectVisibility(
        projectId!,
        activeRunId ? { audit_id: activeRunId } : undefined,
        { signal },
      ),
    enabled: Boolean(projectId) && hasRuns && activeTab === 'overview',
  });

  // Trends: the cross-run series. Enabled only on the Trends tab. Engine, date
  // range, and granularity fold into the query key.
  const trendQuery = useQuery({
    queryKey: queryKeys.visibility.trends(projectId ?? '', {
      engine: engineParam ?? null,
      from: fromParam ?? null,
      granularity,
    }),
    queryFn: ({ signal }) =>
      visibilityApi.getVisibilityTrends(
        projectId!,
        { engine: engineParam, from: fromParam, granularity },
        { signal },
      ),
    enabled: Boolean(projectId) && hasRuns && activeTab === 'trends',
  });

  // Shared execution-evidence + prompt options for the two evidence tabs.
  const { evidenceQuery, promptOptions } = useEvidenceQueries(
    projectId,
    Boolean(projectId) && hasRuns && evidenceTab,
    { activeRunId, promptId, engineParam, fromParam },
  );

  return {
    auditsQuery,
    runOptions,
    activeRun,
    activeRunId,
    hasRuns,
    visibilityQuery,
    trendQuery,
    evidenceQuery,
    promptOptions,
  };
}
