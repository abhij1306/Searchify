'use client';

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { DashboardSkeleton } from '@/components/visibility/dashboard-skeleton';
import { VisibilityEmptyState } from '@/components/visibility/empty-state';
import { FanoutEvidence } from '@/components/visibility/fanout-evidence';
import { MentionsCitations } from '@/components/visibility/mentions-citations';
import { VisibilityOverview } from '@/components/visibility/visibility-overview';
import { VisibilityTabs } from '@/components/visibility/visibility-tabs';
import { VisibilityToolbar, type EngineFilter } from '@/components/visibility/visibility-toolbar';
import { VisibilityTrends } from '@/components/visibility/visibility-trends';
import { queryKeys } from '@/lib/api/query-keys';
import { runsApi } from '@/lib/api/runs';
import { visibilityApi } from '@/lib/api/visibility';
import { useProjectContext } from '@/lib/project/project-context';
import {
  isEvidenceTab,
  normalizeTab,
  toPromptOptions,
  toRunOptions,
  type VisibilityTab,
} from '@/lib/visibility/dashboard';
import {
  rangeToFrom,
  type TrendGranularity,
  type TrendRange,
} from '@/lib/visibility/trends';

/** Newest-window size for the shared execution-evidence request (backend max 500). */
const EVIDENCE_LIMIT = 100;

/**
 * Visibility workspace container (F9, four-tab IA).
 *
 * Resolves the active project (F5 context), lists its audits to build the run
 * selector, and orchestrates one workspace shell: a shared filter bar
 * (`visibility-toolbar.tsx`) above an accessible tablist (`visibility-tabs.tsx`)
 * with exactly four panels — Overview, Trends, Mentions & Citations, and Query
 * Fanout.
 *
 * Shared filter STATE lives here and persists across tab switches; hidden
 * controls keep their state. Ownership (plan §IA): selected run → Overview +
 * both evidence tabs; logical engine → all four; prompt → both evidence tabs;
 * date range → Trends + both evidence tabs; granularity → Trends only. When an
 * evidence request has both `audit_id` and a date bound, the backend intersects
 * them.
 *
 * The active tab is mirrored in `?tab=` (invalid values fall back to Overview)
 * so refresh / back / forward preserve it. Only the relevant query runs per tab:
 * the selected-run projection for Overview, the trend series for Trends, and the
 * shared execution-evidence query (one identical cache key) for either evidence
 * tab — so switching between the two evidence tabs reuses the cache.
 */
export function VisibilityDashboard() {
  const { activeProject, isLoading: isProjectLoading } = useProjectContext();
  const projectId = activeProject?.id ?? null;

  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const urlTab = normalizeTab(searchParams?.get('tab'));

  // The active tab is mirrored in the URL; local state keeps it responsive and
  // re-syncs from the URL on back/forward navigation.
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

  const auditsQuery = useQuery({
    queryKey: queryKeys.runs.list({ project_id: projectId ?? '' }),
    queryFn: ({ signal }) => runsApi.listAudits({ project_id: projectId! }, { signal }),
    enabled: Boolean(projectId),
  });

  const runOptions = useMemo(() => toRunOptions(auditsQuery.data ?? []), [auditsQuery.data]);

  // The effective run: an explicit selection that still exists, else the latest
  // dashboard-ready run (which is also what the endpoint defaults to).
  const activeRunId = useMemo(() => {
    if (selectedRunId && runOptions.some((run) => run.id === selectedRunId)) {
      return selectedRunId;
    }
    return runOptions[0]?.id ?? null;
  }, [runOptions, selectedRunId]);

  const hasRuns = runOptions.length > 0;
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

  // Shared execution-evidence: ONE identical cache key drives both evidence
  // tabs, so switching between Mentions & Citations and Query Fanout reuses the
  // cache instead of refetching. `audit_id` + date bound intersect server-side.
  const evidenceParams = {
    audit_id: activeRunId ?? undefined,
    prompt_id: promptId ?? undefined,
    engine: engineParam,
    from: fromParam,
    limit: EVIDENCE_LIMIT,
  };
  const evidenceQuery = useQuery({
    queryKey: queryKeys.visibility.evidence(projectId ?? '', {
      audit_id: activeRunId ?? null,
      prompt_id: promptId ?? null,
      engine: engineParam ?? null,
      from: fromParam ?? null,
      limit: EVIDENCE_LIMIT,
    }),
    queryFn: ({ signal }) => visibilityApi.getVisibilityEvidence(projectId!, evidenceParams, { signal }),
    enabled: Boolean(projectId) && hasRuns && evidenceTab,
  });

  // Prompt options for the evidence prompt selector must NOT collapse when a
  // prompt is selected, so they are derived from a parallel evidence query that
  // keeps the run/engine/date scope but omits `prompt_id`. When no prompt is
  // selected this key is identical to the main evidence query above, so it
  // reuses the cache and issues no extra request; only a selected prompt filter
  // triggers a second (unfiltered-by-prompt) fetch to keep the list stable.
  const promptOptionsQuery = useQuery({
    queryKey: queryKeys.visibility.evidence(projectId ?? '', {
      audit_id: activeRunId ?? null,
      prompt_id: null,
      engine: engineParam ?? null,
      from: fromParam ?? null,
      limit: EVIDENCE_LIMIT,
    }),
    queryFn: ({ signal }) =>
      visibilityApi.getVisibilityEvidence(
        projectId!,
        { ...evidenceParams, prompt_id: undefined },
        { signal },
      ),
    enabled: Boolean(projectId) && hasRuns && evidenceTab,
  });
  const promptOptions = useMemo(
    () => toPromptOptions(promptOptionsQuery.data?.items ?? []),
    [promptOptionsQuery.data],
  );

  // A narrowing filter (engine, bounded range, or a specific prompt) is active —
  // used to explain a filtered-empty result vs a genuinely empty history.
  const isFiltered = engine !== 'all' || range !== 'all' || promptId !== null;
  const isTrendFiltered = engine !== 'all' || range !== 'all';

  function clearEvidenceFilters() {
    setEngine('all');
    setRange('all');
    setPromptId(null);
  }

  const isBootstrapping = isProjectLoading || (Boolean(projectId) && auditsQuery.isLoading);

  if (isBootstrapping) {
    return <DashboardSkeleton />;
  }

  if (!projectId) {
    return (
      <Alert tone="info">
        Select or create a project to see its AI-visibility results.
      </Alert>
    );
  }

  if (auditsQuery.isError) {
    return (
      <Alert tone="danger">
        Could not load this project&apos;s runs. Check your connection and try again.
      </Alert>
    );
  }

  // Preserve the launch-your-first-audit empty state: a project with no
  // completed runs has nothing to show in any tab.
  if (!hasRuns) {
    return <VisibilityEmptyState />;
  }

  let panel: ReactNode;
  if (activeTab === 'trends') {
    panel = <VisibilityTrends query={trendQuery} hasRuns={hasRuns} isFiltered={isTrendFiltered} />;
  } else if (activeTab === 'mentions-citations') {
    panel = (
      <MentionsCitations
        query={evidenceQuery}
        isFiltered={isFiltered}
        onClearFilters={clearEvidenceFilters}
        limit={EVIDENCE_LIMIT}
      />
    );
  } else if (activeTab === 'query-fanout') {
    panel = (
      <FanoutEvidence
        query={evidenceQuery}
        isFiltered={isFiltered}
        onClearFilters={clearEvidenceFilters}
        limit={EVIDENCE_LIMIT}
      />
    );
  } else {
    panel = <VisibilityOverview query={visibilityQuery} engineFilter={engine} />;
  }

  return (
    <div className="grid gap-5">
      <VisibilityToolbar
        activeTab={activeTab}
        runs={runOptions}
        activeRunId={activeRunId}
        onSelectRun={setSelectedRunId}
        engine={engine}
        onChangeEngine={setEngine}
        promptOptions={promptOptions}
        promptId={promptId}
        onChangePrompt={setPromptId}
        range={range}
        onChangeRange={setRange}
        granularity={granularity}
        onChangeGranularity={setGranularity}
      />
      <VisibilityTabs activeTab={activeTab} onSelectTab={selectTab} panel={panel} />
    </div>
  );
}
