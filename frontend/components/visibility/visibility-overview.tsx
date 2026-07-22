'use client';

import type { UseQueryResult } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { Alert } from '@/components/ui/alert';
import { DashboardSkeleton } from '@/components/visibility/dashboard-skeleton';
import { EngineComparison } from '@/components/visibility/engine-comparison';
import { RankingsTable } from '@/components/visibility/rankings-table';
import { VisibilityScoreCard } from '@/components/visibility/score-card';
import type { Visibility } from '@/lib/api/types';
import type { VisibilityFilters } from '@/lib/visibility/dashboard';

/**
 * Overview tab — the selected-run Visibility composition (the default tab).
 *
 * This is the unchanged MVP selected-run view moved out of the old dashboard:
 * the Visibility Score card, the brand-vs-competitor Rankings table (which
 * carries both SOV definitions and the sentiment / avg-position placeholders),
 * and the per-engine / logical-engine comparison + Share-of-Voice donut. It
 * reads the same `GET /projects/{id}/visibility?audit_id=` selected-run
 * projection and preserves the same loading / error / empty behavior.
 *
 * It does NOT append trend charts or raw evidence below the fold — those belong
 * to the Trends / Mentions & Citations / Query Fanout tabs.
 */
export function VisibilityOverview({
  query,
  engineFilter,
}: Readonly<{
  query: UseQueryResult<Visibility, unknown>;
  engineFilter: VisibilityFilters['engine'];
}>) {
  const visibility = query.data;

  // Precedence: data wins; otherwise a terminal error shows only the alert
  // (no skeleton), and everything else is still loading.
  let body: ReactNode;
  if (visibility) {
    body = (
      <>
        <div className="grid gap-5 lg:grid-cols-[minmax(260px,1fr)_2fr]">
          <VisibilityScoreCard visibility={visibility} />
          <RankingsTable visibility={visibility} />
        </div>
        <EngineComparison visibility={visibility} filter={engineFilter} />
      </>
    );
  } else if (query.isError) {
    body = null;
  } else {
    body = <DashboardSkeleton />;
  }

  return (
    <div className="grid gap-5">
      {query.isError ? (
        <Alert tone="danger">
          Could not load visibility metrics for this run. Try another run or refresh.
        </Alert>
      ) : null}

      {body}
    </div>
  );
}
