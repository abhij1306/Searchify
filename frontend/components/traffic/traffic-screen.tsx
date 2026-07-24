'use client';

import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, Loader2, RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';

import { SegmentedControl } from '@/components/setup/segmented-control';
import { TrafficEmptyState } from '@/components/traffic/empty-state';
import { PagesTable } from '@/components/traffic/pages-table';
import { QueriesTable } from '@/components/traffic/queries-table';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dropdown,
  DropdownContent,
  DropdownItem,
  DropdownLabel,
  DropdownTrigger,
} from '@/components/ui/dropdown';
import { eyebrowClasses } from '@/components/ui/eyebrow';
import { Skeleton } from '@/components/ui/skeleton';
import { TrendChart } from '@/components/ui/trend-chart';
import { integrationsApi, type IntegrationSyncRun } from '@/lib/api/integrations';
import { queryKeys } from '@/lib/api/query-keys';
import {
  trafficApi,
  type TrafficDashboard,
  type TrafficSyncEnqueueResponse,
} from '@/lib/api/traffic';
import { useProjectContext } from '@/lib/project/project-context';
import { isActiveSyncRun, isSucceededSyncRun, TRAFFIC_SYNC_POLL_MS } from '@/lib/traffic/sync';
import {
  bucketAdverb,
  countAxisTicks,
  countDomainMax,
  formatSyncTimestamp,
  formatWindowDate,
  GRANULARITY_OPTIONS,
  isEmptyDashboard,
  RANGE_OPTIONS,
  rangeLabel,
  rangeToWindow,
  toChartPoints,
  trafficStats,
  type TrafficGranularity,
  type TrafficRange,
  type TrafficSeriesPoint,
  type TrafficStat,
} from '@/lib/traffic/traffic';
import { cn } from '@/lib/utils';

// Midnight filter-chip language (visibility-toolbar idiom): a non-default
// filter value flips the chip to the accent-soft active state.
const CHIP_ACTIVE_CLASS =
  'border-accent-border bg-accent-soft text-accent-text hover:border-accent-border hover:bg-accent-soft hover:text-accent-text';

/** Loading shimmer for the screen (also the route's Suspense fallback). */
export function TrafficSkeleton() {
  return (
    <div className="grid gap-6" aria-busy="true" data-testid="traffic-skeleton">
      <div className="flex flex-wrap items-center gap-2.5">
        <Skeleton className="h-[30px] w-44 rounded-full" />
        <Skeleton className="h-[38px] w-56 rounded-full" />
        <Skeleton className="ml-auto h-[30px] w-28 rounded-full" />
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-[104px]" />
        ))}
      </div>
      <div className="grid gap-6 xl:grid-cols-2">
        <Skeleton className="h-72" />
        <Skeleton className="h-72" />
      </div>
      <Skeleton className="h-80" />
    </div>
  );
}

function TrafficToolbar({
  range,
  onChangeRange,
  granularity,
  onChangeGranularity,
  note,
  syncing,
  syncPending,
  onSyncNow,
}: Readonly<{
  range: TrafficRange;
  onChangeRange: (range: TrafficRange) => void;
  granularity: TrafficGranularity;
  onChangeGranularity: (granularity: TrafficGranularity) => void;
  note: string;
  syncing: boolean;
  syncPending: boolean;
  onSyncNow: () => void;
}>) {
  return (
    <div className="flex flex-wrap items-center gap-2.5" data-testid="traffic-toolbar">
      <Dropdown>
        <DropdownTrigger asChild>
          <Button
            variant="secondary"
            size="sm"
            aria-label="Select date range"
            className={cn(range !== 'latest' && CHIP_ACTIVE_CLASS)}
          >
            <span className="text-muted">Range:</span>
            <span className="font-medium">{rangeLabel(range)}</span>
            <ChevronDown className="text-muted size-3" aria-hidden />
          </Button>
        </DropdownTrigger>
        <DropdownContent>
          <DropdownLabel>Date range</DropdownLabel>
          {RANGE_OPTIONS.map((option) => (
            <DropdownItem
              key={option.value}
              data-active={range === option.value}
              onSelect={() => onChangeRange(option.value)}
            >
              {option.label}
            </DropdownItem>
          ))}
        </DropdownContent>
      </Dropdown>

      <SegmentedControl
        value={granularity}
        onChange={onChangeGranularity}
        options={GRANULARITY_OPTIONS}
        ariaLabel="Snapshot granularity"
      />

      <div className="ml-auto flex items-center gap-3">
        <span className="text-2xs text-muted font-mono">{note}</span>
        <Button
          variant="secondary"
          size="sm"
          onClick={onSyncNow}
          disabled={syncing || syncPending}
          data-testid="sync-now-button"
        >
          {syncing || syncPending ? (
            <>
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
              Syncing…
            </>
          ) : (
            <>
              <RefreshCw className="size-3.5" aria-hidden />
              Sync now
            </>
          )}
        </Button>
      </div>
    </div>
  );
}

function StatCard({ stat }: Readonly<{ stat: TrafficStat }>) {
  const valueClass = stat.placeholder ? 'text-muted' : 'text-foreground';
  const deltaClass =
    stat.tone === 'up'
      ? 'text-score-high'
      : stat.tone === 'down'
        ? 'text-score-low'
        : 'text-muted';
  return (
    <Card data-testid={`stat-${stat.key}`}>
      <CardContent className="grid gap-1 p-4">
        <span className={eyebrowClasses}>{stat.label}</span>
        <span className={cn('mono text-2xl font-semibold', valueClass)}>{stat.value}</span>
        <span className={cn('text-xs', deltaClass)}>{stat.delta}</span>
      </CardContent>
    </Card>
  );
}

function TrendCard({
  title,
  description,
  series,
  percent = false,
  fixedDomain = false,
}: Readonly<{
  title: string;
  description: string;
  series: readonly TrafficSeriesPoint[];
  /** Scale a persisted fraction (wire CTR) onto the chart's 0–100 domain. */
  percent?: boolean;
  /** Keep TrendChart's default 0–100 domain (CTR/position); counts pass a real domain. */
  fixedDomain?: boolean;
}>) {
  const points = toChartPoints(series, { percent });
  const domainMax = fixedDomain ? undefined : countDomainMax(series);
  const yLabels = fixedDomain
    ? percent
      ? ['100%', '75%', '50%', '25%', '0%']
      : ['100', '75', '50', '25', '0']
    : countAxisTicks(domainMax ?? 10);
  const firstLabel = points[0]?.label ?? '';
  const lastLabel = points[points.length - 1]?.label ?? '';

  return (
    <Card data-testid={`trend-chart-${title.toLowerCase().replace(/\s+/g, '-')}`}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
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
              data={points}
              width={680}
              height={180}
              className="h-[180px] w-full"
              domainMax={domainMax}
            />
            {points.length > 1 ? (
              <div className="text-2xs text-muted mt-1 flex justify-between font-mono" aria-hidden>
                <span>{firstLabel}</span>
                <span>{lastLabel}</span>
              </div>
            ) : null}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return 'Something went wrong. Please try again.';
}

/**
 * Traffic screen (F6; mockups `analytics-dashboards-traffic-*.html`).
 *
 * One dashboard over the persisted Traffic projection: a toolbar (Range
 * dropdown-chip + day|week|month segmented granularity + Sync now), six
 * headline stat cards, four trend cards (impressions/clicks on truthful count
 * domains; CTR/position on the 0–100 default), and the top-pages/top-queries
 * keyset tables. The default "Latest synced window" preset sends no bounds so
 * the backend serves the freshest persisted snapshot; bounded presets send an
 * exact window and an unmatched one is surfaced honestly (the read endpoints
 * never recompute). Sync now fans out to the project's active mapped GSC/GA4
 * connections (C3), polls each run until terminal, then invalidates the
 * traffic queries so the new projection renders.
 */
export function TrafficScreen() {
  const queryClient = useQueryClient();
  const { activeProject, isLoading: isProjectLoading } = useProjectContext();
  const projectId = activeProject?.id ?? null;
  const workspaceId = activeProject?.workspace_id ?? null;

  const [range, setRange] = useState<TrafficRange>('latest');
  const [granularity, setGranularity] = useState<TrafficGranularity>('day');
  const [syncRuns, setSyncRuns] = useState<TrafficSyncEnqueueResponse>([]);
  const [syncStartedAt, setSyncStartedAt] = useState<string | null>(null);
  const [syncNotice, setSyncNotice] = useState<string | null>(null);

  const windowBounds = rangeToWindow(range);

  const dashboardQuery = useQuery({
    queryKey: queryKeys.traffic.dashboard(projectId ?? '', { ...windowBounds, granularity }),
    queryFn: ({ signal }) =>
      trafficApi.getTraffic(projectId ?? '', { ...windowBounds, granularity }, { signal }),
    enabled: Boolean(projectId),
  });

  // Workspace connections feed the "Last synced" note and the empty-state
  // copy variant (connected-but-not-yet-synced vs. connect-one).
  const connectionsQuery = useQuery({
    queryKey: queryKeys.integrations.connections(workspaceId),
    queryFn: ({ signal }) => integrationsApi.list({ signal }),
    enabled: Boolean(workspaceId),
  });

  const syncMutation = useMutation({
    mutationFn: () => trafficApi.syncNow(projectId ?? ''),
    onSuccess: (runs) => {
      if (runs.length === 0) {
        setSyncNotice(
          'No active Search Console or GA4 connection is mapped to this project yet — connect one in Settings to start syncing.',
        );
        return;
      }
      setSyncNotice(null);
      setSyncRuns(runs);
      setSyncStartedAt(new Date().toISOString());
    },
  });

  // Poll every enqueued run until it reaches a terminal queue status (the F5
  // `refetchInterval` idiom at TRAFFIC_SYNC_POLL_MS). `useQueries` keeps the
  // hook count fixed across the variable run fan-out, and the terminal
  // statuses are read straight from the polled query data — never mirrored
  // into state — so the completion transition only touches the query cache.
  const runQueries = useQueries({
    queries: syncRuns.map((run) => ({
      queryKey: queryKeys.integrations.sync(run.connection_id, run.sync_run_id),
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        integrationsApi.getSync(run.connection_id, run.sync_run_id, { signal }),
      refetchInterval: (query: { state: { data?: IntegrationSyncRun } }) => {
        const polled = query.state.data;
        if (!polled) return TRAFFIC_SYNC_POLL_MS;
        return isActiveSyncRun(polled.status) ? TRAFFIC_SYNC_POLL_MS : false;
      },
    })),
  });

  const runsEnqueued = syncRuns.length > 0;
  const allTerminal =
    runsEnqueued &&
    runQueries.every((query) => query.data !== undefined && !isActiveSyncRun(query.data.status));
  const syncing = runsEnqueued && !allTerminal;
  const syncOutcome = !allTerminal
    ? null
    : runQueries.every((query) => query.data && isSucceededSyncRun(query.data.status))
      ? 'succeeded'
      : 'failed';

  // Every queued run is terminal: the new projection is (being) persisted —
  // invalidate the traffic queries (and the connections' last-synced note).
  // (F5 idiom: the terminal transition only invalidates — the outcome banner
  // above is derived from the polled statuses, no state mirror.)
  useEffect(() => {
    if (!allTerminal) return;
    void queryClient.invalidateQueries({ queryKey: queryKeys.traffic.all });
    void queryClient.invalidateQueries({ queryKey: queryKeys.integrations.all });
  }, [allTerminal, queryClient]);

  const connections = connectionsQuery.data ?? [];
  const lastSynced = connections.reduce<string | null>((acc, connection) => {
    if (!connection.last_synced_at) return acc;
    return acc === null || connection.last_synced_at > acc ? connection.last_synced_at : acc;
  }, null);

  const toolbarNote =
    syncing && syncStartedAt
      ? `Started ${formatSyncTimestamp(syncStartedAt)}`
      : lastSynced
        ? `Last synced ${formatSyncTimestamp(lastSynced)}`
        : 'Never synced';

  if (isProjectLoading || (Boolean(projectId) && dashboardQuery.isLoading)) {
    return <TrafficSkeleton />;
  }

  if (!projectId) {
    return <Alert tone="info">Select or create a project to see its traffic.</Alert>;
  }

  if (dashboardQuery.isError) {
    return (
      <Alert tone="danger">Could not load traffic data. Check your connection and try again.</Alert>
    );
  }

  const dashboard = dashboardQuery.data as TrafficDashboard;
  const empty = isEmptyDashboard(dashboard);

  const syncBanner = syncing ? (
    <Alert tone="info" hideIcon>
      <span className="flex items-center gap-2.5" data-testid="sync-status-banner">
        <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden />
        <span>
          Sync in progress — refreshing Google Search Console and GA4 data. Charts and tables
          update when the sync completes.
        </span>
      </span>
    </Alert>
  ) : null;

  const toolbar = (
    <TrafficToolbar
      range={range}
      onChangeRange={setRange}
      granularity={granularity}
      onChangeGranularity={setGranularity}
      note={toolbarNote}
      syncing={syncing}
      syncPending={syncMutation.isPending}
      onSyncNow={() => syncMutation.mutate()}
    />
  );

  // No persisted snapshot at all (default mode): the project has never
  // projected traffic — the connect/first-sync empty state (mockup).
  if (empty && range === 'latest') {
    return (
      <div className="grid gap-5">
        {syncBanner}
        <TrafficEmptyState hasConnections={connections.length > 0} />
      </div>
    );
  }

  // A bounded preset with no matching persisted window: surfaced honestly
  // (read endpoints serve persisted snapshot windows only — never recompute).
  if (empty) {
    return (
      <div className="grid gap-5">
        {toolbar}
        {syncBanner}
        <Alert tone="info">
          No synced snapshot covers {formatWindowDate(windowBounds.from ?? '')} –{' '}
          {formatWindowDate(windowBounds.to ?? '')} yet. Traffic serves persisted sync windows
          only — switch to the latest synced window or run a sync.
        </Alert>
      </div>
    );
  }

  const stats = trafficStats(dashboard);
  const tableKey = `${windowBounds.from ?? ''}|${windowBounds.to ?? ''}`;

  return (
    <div className="grid gap-6">
      {toolbar}
      {syncBanner}
      {syncNotice ? <Alert tone="info">{syncNotice}</Alert> : null}
      {syncMutation.isError ? <Alert tone="danger">{errorMessage(syncMutation.error)}</Alert> : null}
      {syncOutcome === 'succeeded' ? (
        <Alert tone="success">Sync complete — charts and tables now render the new snapshot.</Alert>
      ) : null}
      {syncOutcome === 'failed' ? (
        <Alert tone="warning">
          Sync finished with errors — previously imported data is unchanged. Check Settings →
          Integrations for details.
        </Alert>
      ) : null}

      <div
        className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6"
        data-testid="traffic-stats"
      >
        {stats.map((stat) => (
          <StatCard key={stat.key} stat={stat} />
        ))}
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <TrendCard
          title="Impressions"
          description={`Google Search Console · ${bucketAdverb(granularity)}`}
          series={dashboard.series.impressions}
        />
        <TrendCard
          title="Clicks"
          description={`Google Search Console · ${bucketAdverb(granularity)}`}
          series={dashboard.series.clicks}
        />
        <TrendCard
          title="CTR"
          description="Click-through rate · 0–100% scale"
          series={dashboard.series.ctr}
          percent
          fixedDomain
        />
        <TrendCard
          title="Average position"
          description="Mean ranking position · 0–100 scale"
          series={dashboard.series.position}
          fixedDomain
        />
      </div>

      <PagesTable
        key={`pages-${tableKey}`}
        projectId={projectId}
        from={windowBounds.from}
        to={windowBounds.to}
      />
      <QueriesTable
        key={`queries-${tableKey}`}
        projectId={projectId}
        from={windowBounds.from}
        to={windowBounds.to}
      />
    </div>
  );
}
