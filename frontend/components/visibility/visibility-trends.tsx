'use client';

import Link from 'next/link';
import type { UseQueryResult } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardEyebrow, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { TrendChart } from '@/components/ui/trend-chart';
import { NO_RANKINGS_MESSAGE, RankingRowsTable } from '@/components/visibility/ranking-rows';
import { cn } from '@/lib/utils';
import type { VisibilityTrendPoint } from '@/lib/api/types';
import {
  formatPointDate,
  rankingBookends,
  sortedTrendRankings,
  toChartPoints,
  trendStats,
  versionMarkerSummary,
  type TrendStat,
} from '@/lib/visibility/trends';

/**
 * Cross-run Visibility Trend view (design.md §9.6 Trend mode).
 *
 * Renders the trend workflow over the `VisibilityTrendPoint[]` projection:
 *   - a headline stat row (Visibility Score, SOV, brand mentions, owned
 *     citations, plus the null Sentiment / Avg Position placeholders),
 *   - two accessible trend charts (Visibility Score + Share of Voice) reusing
 *     the single `TrendChart` owner, with version-boundary markers,
 *   - side-by-side start-of-range vs latest ranking-history tables.
 * It also covers the loading skeleton, request-error, no-history, filtered-empty
 * and single-point ("add another run") states. Sentiment / average position are
 * always the "—" not-yet-computed placeholder (decision B-2 / invariant 9);
 * partial-run points are shown without hiding them. The toolbar (engine / date
 * / granularity controls) lives in `visibility-toolbar.tsx`; this component owns
 * only the trend body.
 */
export function VisibilityTrends({
  query,
  hasRuns,
  isFiltered,
}: Readonly<{
  query: UseQueryResult<VisibilityTrendPoint[], unknown>;
  hasRuns: boolean;
  isFiltered: boolean;
}>) {
  if (query.isLoading) {
    return <TrendsSkeleton />;
  }

  if (query.isError) {
    return (
      <Alert tone="danger">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>Could not load the visibility trend. Check your connection and try again.</span>
          <Button variant="secondary" size="sm" onClick={() => query.refetch()}>
            Retry
          </Button>
        </div>
      </Alert>
    );
  }

  const points = query.data ?? [];

  if (points.length === 0) {
    return isFiltered ? (
      <Card>
        <CardContent className="grid justify-items-center gap-2 py-12 text-center">
          <CardEyebrow>Trends</CardEyebrow>
          <h2 className="font-display text-foreground text-lg font-semibold">
            No runs match these filters
          </h2>
          <p className="text-secondary max-w-md text-sm">
            No completed audits fall inside the selected engine and date range. Widen the range or
            clear the engine filter to see more history.
          </p>
        </CardContent>
      </Card>
    ) : (
      <Card>
        <CardContent className="grid justify-items-center gap-4 py-12 text-center">
          <CardEyebrow>Trends</CardEyebrow>
          <div className="grid gap-1">
            <h2 className="font-display text-foreground text-lg font-semibold">
              No trend history yet
            </h2>
            <p className="text-secondary max-w-md text-sm">
              {hasRuns
                ? 'This project has runs but no dashboard-ready snapshots to plot yet. Once more audits complete, their history shows up here.'
                : 'Launch audits over time to see how your Visibility Score and Share of Voice move across runs.'}
            </p>
          </div>
          <Button asChild variant="ghost" size="md">
            <Link href="/runs">Go to Runs</Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  const stats = trendStats(points);
  const versionNote = versionMarkerSummary(points);
  const onePoint = points.length === 1;

  return (
    <div className="grid gap-6">
      {onePoint ? (
        <Alert tone="info">
          Only one completed run is in range, so there is no movement to plot yet. Add another run
          to see the trend.
        </Alert>
      ) : null}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
        {stats.map((stat) => (
          <StatCard key={stat.key} stat={stat} />
        ))}
      </div>

      <section className="grid gap-6">
        <TrendCard
          title="Visibility Score"
          description="Cross-run trend across completed audits"
          badge={`${points.length} ${points.length === 1 ? 'run' : 'runs'}`}
          points={points}
          metric="visibility_score"
          yLabels={['100', '75', '50', '25', '0']}
          versionNote={versionNote}
        />
        <TrendCard
          title="Share of Voice"
          description="Brand mention share vs. competitors over time"
          points={points}
          metric="sov"
          yLabels={['100%', '75%', '50%', '25%', '0%']}
          versionNote={null}
        />

        <div className="grid gap-6 lg:grid-cols-2">
          <RankingHistoryCard title="Rankings (Latest)" point={rankingBookends(points).latest} />
          <RankingHistoryCard
            title="Rankings (Start of Range)"
            point={rankingBookends(points).first}
            emptyNote="Add another run to compare the start of the range."
          />
        </div>
      </section>
    </div>
  );
}

function StatCard({ stat }: Readonly<{ stat: TrendStat }>) {
  const valueClass = stat.placeholder ? 'text-muted' : 'text-foreground';
  const deltaClass =
    stat.direction === 'up'
      ? 'text-score-high'
      : stat.direction === 'down'
        ? 'text-score-low'
        : 'text-muted';
  return (
    <Card>
      <CardContent className="grid gap-1 p-4">
        <span className="text-2xs text-muted font-mono font-medium tracking-[0.08em] uppercase">
          {stat.label}
        </span>
        <span className={cn('mono text-2xl font-semibold', valueClass)}>{stat.value}</span>
        <span className={cn('text-xs', deltaClass)}>{stat.delta}</span>
      </CardContent>
    </Card>
  );
}

function TrendCard({
  title,
  description,
  badge,
  points,
  metric,
  yLabels,
  versionNote,
}: Readonly<{
  title: string;
  description: string;
  badge?: string;
  points: readonly VisibilityTrendPoint[];
  metric: Parameters<typeof toChartPoints>[1];
  yLabels: string[];
  versionNote: string | null;
}>) {
  const chartPoints = toChartPoints(points, metric);
  const firstLabel = chartPoints[0]?.label ?? '';
  const lastLabel = chartPoints[chartPoints.length - 1]?.label ?? '';

  return (
    <Card data-testid={`trend-chart-${metric}`}>
      <CardHeader className="flex-row items-start justify-between gap-2">
        <div className="grid gap-1">
          <CardTitle>{title}</CardTitle>
          <p className="text-secondary text-sm">{description}</p>
        </div>
        {badge ? <Badge variant="neutral">{badge}</Badge> : null}
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="flex gap-3">
          <div
            className="text-2xs text-muted flex flex-col justify-between py-1 font-mono"
            aria-hidden
          >
            {yLabels.map((y) => (
              <span key={y}>{y}</span>
            ))}
          </div>
          <div className="min-w-0 flex-1">
            <TrendChart
              label={title}
              data={chartPoints}
              width={680}
              height={180}
              className="h-[180px] w-full"
            />
            {chartPoints.length > 1 ? (
              <div className="text-2xs text-muted mt-1 flex justify-between font-mono" aria-hidden>
                <span>{firstLabel}</span>
                <span>{lastLabel}</span>
              </div>
            ) : null}
          </div>
        </div>
        {versionNote ? (
          <div className="text-secondary flex items-center gap-2 text-xs">
            <span className="bg-warning size-2 rounded-full" aria-hidden />
            <span>{versionNote}</span>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function RankingHistoryCard({
  title,
  point,
  emptyNote,
}: Readonly<{
  title: string;
  point: VisibilityTrendPoint | null;
  emptyNote?: string;
}>) {
  const rows = point ? sortedTrendRankings(point.rankings) : [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {point ? (
          <p className="text-secondary text-sm">{formatPointDate(point.completed_at)}</p>
        ) : null}
      </CardHeader>
      <CardContent className="p-0">
        {!point || rows.length === 0 ? (
          <p className="text-secondary p-[var(--card-padding)] text-sm">
            {emptyNote ?? NO_RANKINGS_MESSAGE}
          </p>
        ) : (
          <RankingRowsTable rows={rows} />
        )}
      </CardContent>
    </Card>
  );
}

function TrendsSkeleton() {
  return (
    <div className="grid gap-6" aria-hidden>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
        {[0, 1, 2, 3, 4, 5, 6].map((i) => (
          <Card key={i}>
            <CardContent className="grid gap-2 p-4">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-6 w-12" />
            </CardContent>
          </Card>
        ))}
      </div>
      <Card>
        <CardContent className="grid gap-4">
          <Skeleton className="h-[180px] w-full" />
        </CardContent>
      </Card>
    </div>
  );
}
