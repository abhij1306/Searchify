'use client';

import type { ReactNode } from 'react';

import { Alert } from '@/components/ui/alert';
import { DashboardSkeleton } from '@/components/visibility/dashboard-skeleton';
import { VisibilityEmptyState } from '@/components/visibility/empty-state';
import { FanoutEvidence } from '@/components/visibility/fanout-evidence';
import { MentionsCitations } from '@/components/visibility/mentions-citations';
import { VisibilityOverview } from '@/components/visibility/visibility-overview';
import { VisibilityTabs } from '@/components/visibility/visibility-tabs';
import { VisibilityToolbar } from '@/components/visibility/visibility-toolbar';
import { VisibilityTrends } from '@/components/visibility/visibility-trends';
import { useProjectContext } from '@/lib/project/project-context';
import {
  EVIDENCE_LIMIT,
  useVisibilityFilters,
  useVisibilityQueries,
} from '@/lib/visibility/use-visibility-dashboard';

/**
 * Visibility workspace container (F9, four-tab IA).
 *
 * Resolves the active project (F5 context) and orchestrates one workspace
 * shell: a shared filter bar (`visibility-toolbar.tsx`) above an accessible
 * tablist (`visibility-tabs.tsx`) with exactly four panels — Overview, Trends,
 * Mentions & Citations, and Query Fanout. Tab/filter state lives in
 * `useVisibilityFilters` (URL-synced `?tab=`); the per-tab queries live in
 * `useVisibilityQueries` (only the relevant query runs per tab). When an
 * evidence request has both `audit_id` and a date bound, the backend
 * intersects them.
 */
export function VisibilityDashboard() {
  const { activeProject, isLoading: isProjectLoading } = useProjectContext();
  const projectId = activeProject?.id ?? null;

  const filters = useVisibilityFilters();
  const {
    auditsQuery,
    runOptions,
    activeRunId,
    hasRuns,
    visibilityQuery,
    trendQuery,
    evidenceQuery,
    promptOptions,
  } = useVisibilityQueries(projectId, filters);

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
  if (filters.activeTab === 'trends') {
    panel = (
      <VisibilityTrends query={trendQuery} hasRuns={hasRuns} isFiltered={filters.isTrendFiltered} />
    );
  } else if (filters.activeTab === 'mentions-citations') {
    panel = (
      <MentionsCitations
        query={evidenceQuery}
        isFiltered={filters.isFiltered}
        onClearFilters={filters.clearEvidenceFilters}
        limit={EVIDENCE_LIMIT}
      />
    );
  } else if (filters.activeTab === 'query-fanout') {
    panel = (
      <FanoutEvidence
        query={evidenceQuery}
        isFiltered={filters.isFiltered}
        onClearFilters={filters.clearEvidenceFilters}
        limit={EVIDENCE_LIMIT}
      />
    );
  } else {
    panel = <VisibilityOverview query={visibilityQuery} engineFilter={filters.engine} />;
  }

  return (
    <div className="grid gap-5">
      <VisibilityToolbar
        activeTab={filters.activeTab}
        runs={runOptions}
        activeRunId={activeRunId}
        onSelectRun={filters.setSelectedRunId}
        engine={filters.engine}
        onChangeEngine={filters.setEngine}
        promptOptions={promptOptions}
        promptId={filters.promptId}
        onChangePrompt={filters.setPromptId}
        range={filters.range}
        onChangeRange={filters.setRange}
        granularity={filters.granularity}
        onChangeGranularity={filters.setGranularity}
      />
      <VisibilityTabs activeTab={filters.activeTab} onSelectTab={filters.selectTab} panel={panel} />
    </div>
  );
}
