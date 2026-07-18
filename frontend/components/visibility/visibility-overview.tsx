'use client';

import type { UseQueryResult } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { Alert } from '@/components/ui/alert';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
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
        <div className="grid gap-6 lg:grid-cols-[minmax(260px,1fr)_2fr]">
          <VisibilityScoreCard visibility={visibility} />
          <RankingsTable visibility={visibility} />
        </div>
        <EngineComparison visibility={visibility} filter={engineFilter} />
      </>
    );
  } else if (query.isError) {
    body = null;
  } else {
    body = <OverviewSkeleton />;
  }

  return (
    <div className="grid gap-6">
      {query.isError ? (
        <Alert tone="danger">
          Could not load visibility metrics for this run. Try another run or refresh.
        </Alert>
      ) : null}

      {body}
    </div>
  );
}

function OverviewSkeleton() {
  return (
    <div className="grid gap-6" aria-hidden>
      <div className="grid gap-6 lg:grid-cols-[minmax(260px,1fr)_2fr]">
        <Card>
          <CardContent className="grid justify-items-center gap-4">
            <Skeleton className="size-28 rounded-full" />
            <Skeleton className="h-4 w-40" />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="grid gap-3">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardContent className="grid gap-4 md:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
