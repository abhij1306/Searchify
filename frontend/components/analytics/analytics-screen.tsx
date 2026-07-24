'use client';

import { useMemo, useState } from 'react';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { ChevronDown } from 'lucide-react';

import { AnalyticsEmptyState } from '@/components/analytics/empty-state';
import { ReferralsTable } from '@/components/analytics/referrals-table';
import { SegmentedControl } from '@/components/setup/segmented-control';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardEyebrow,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Donut } from '@/components/ui/donut';
import {
  Dropdown,
  DropdownContent,
  DropdownItem,
  DropdownLabel,
  DropdownTrigger,
} from '@/components/ui/dropdown';
import { scoreBand, scoreBandText } from '@/components/ui/score-band';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { TrendChart, type TrendPoint } from '@/components/ui/trend-chart';
import { analyticsApi, type LlmAnalytics, type LlmAnalyticsThemeRow } from '@/lib/api/analytics';
import { queryKeys } from '@/lib/api/query-keys';
import {
  GRANULARITY_OPTIONS,
  RANGE_OPTIONS,
  bucketAdjectiveTitle,
  bucketCountLabel,
  rangeLabel,
  rangeToFrom,
  type AnalyticsGranularity,
  type AnalyticsRange,
} from '@/lib/analytics/options';
import {
  correlationDisplay,
  countDomainMax,
  countYLabels,
  formatInt,
  formatPercent,
  isAnalyticsEmpty,
  latestValue,
  sortEngineVisibility,
  sourceSegments,
  toCountChartPoints,
  toPercentChartPoints,
  totalSourceSessions,
} from '@/lib/analytics/series';
import { engineLabel } from '@/lib/providers/catalog';
import { useProjectContext } from '@/lib/project/project-context';
import { cn } from '@/lib/utils';

// Midnight filter-chip language (visibility-toolbar idiom): a non-default
// filter value flips the chip to the accent-soft active state.
const CHIP_ACTIVE_CLASS =
  'border-accent-border bg-accent-soft text-accent-text hover:border-accent-border hover:bg-accent-soft hover:text-accent-text';

// Engine-tile dot colors (mockup): ChatGPT at 60% accent, Gemini full accent,
// Claude the accent hover tone; unknown engines fall back to full accent.
const ENGINE_DOT_CLASS: Record<string, string> = {
  chatgpt: 'bg-accent/60',
  gemini: 'bg-accent',
  claude: 'bg-accent-hover',
};

/**
 * LLM Analytics screen (F8 + F9) — the `/analytics` AEO Insights dashboard
 * over the persisted `AnalyticsSnapshot` projection (invariant 7 — no
 * read-time recomputation anywhere):
 *   - toolbar (Range dropdown-chip + Day|Week|Month segmented granularity),
 *   - AI-referral volume (count scale via `domainMax`) + referral share
 *     (fixed 0–100%) trend cards,
 *   - per-`ai_source` breakdown donut + the visibility↔referral correlation
 *     card (`insufficient_data` → neutral badge + `—`, never fabricated),
 *   - cross-engine visibility tiles,
 *   - the theme rollup table,
 *   - the referrals drill-down (`referrals-table.tsx`).
 * A project with no analytics evidence at all renders only the empty state
 * (no toolbar), matching `analytics-dashboards-llm-empty`.
 */
export function AnalyticsScreen() {
  const { activeProject, isLoading: isProjectLoading } = useProjectContext();
  const projectId = activeProject?.id ?? null;

  const [range, setRange] = useState<AnalyticsRange>('90d');
  const [granularity, setGranularity] = useState<AnalyticsGranularity>('week');

  // Resolve the range preset to a `from` bound once per range change —
  // computing it inline would call `new Date()` on every render and churn the
  // query key (visibility precedent).
  const fromParam = useMemo(() => rangeToFrom(range), [range]);

  const dashboardQuery = useQuery({
    queryKey: queryKeys.analytics.dashboard(projectId ?? '', {
      from: fromParam ?? null,
      granularity,
    }),
    queryFn: ({ signal }) =>
      analyticsApi.getAnalytics(projectId!, { from: fromParam, granularity }, { signal }),
    enabled: Boolean(projectId),
  });

  const data = dashboardQuery.data ?? null;
  const empty = data ? isAnalyticsEmpty(data) : false;

  const themesQuery = useQuery({
    queryKey: queryKeys.analytics.themes(projectId ?? '', { from: fromParam ?? null }),
    queryFn: ({ signal }) => analyticsApi.getThemes(projectId!, { from: fromParam }, { signal }),
    enabled: Boolean(projectId) && !empty,
  });

  if (isProjectLoading || (Boolean(projectId) && dashboardQuery.isLoading)) {
    return <AnalyticsSkeleton />;
  }

  if (!projectId) {
    return <Alert tone="info">Select or create a project to see its LLM analytics.</Alert>;
  }

  if (dashboardQuery.isError) {
    return (
      <Alert tone="danger">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>Could not load LLM analytics. Check your connection and try again.</span>
          <Button variant="secondary" size="sm" onClick={() => dashboardQuery.refetch()}>
            Retry
          </Button>
        </div>
      </Alert>
    );
  }

  if (!data || empty) {
    return <AnalyticsEmptyState />;
  }

  return (
    <div className="grid gap-6">
      <AnalyticsToolbar
        range={range}
        onChangeRange={setRange}
        granularity={granularity}
        onChangeGranularity={setGranularity}
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <ReferralVolumeCard data={data} />
        <ReferralShareCard data={data} />
      </div>
      <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
        <SourceBreakdownCard data={data} range={range} />
        <CorrelationCard data={data} granularity={granularity} />
      </div>
      <EngineVisibilityCard data={data} granularity={granularity} />
      <ThemesCard query={themesQuery} />
      {/* Remount on window change so the keyset walk restarts (a cursor
          replayed against a different window is a backend 400). */}
      <ReferralsTable key={fromParam ?? 'all'} projectId={projectId} from={fromParam} />
    </div>
  );
}

/** Loading placeholder shared by the page Suspense fallback and the screen. */
export function AnalyticsSkeleton() {
  return (
    <div className="grid gap-6" aria-hidden>
      <div className="flex flex-wrap gap-2.5">
        <Skeleton className="h-[30px] w-36 rounded-full" />
        <Skeleton className="h-[38px] w-56 rounded-full" />
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        {[0, 1].map((index) => (
          <Card key={index}>
            <CardContent className="grid gap-4">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-[180px] w-full" />
            </CardContent>
          </Card>
        ))}
      </div>
      <Card>
        <CardContent className="grid gap-4">
          <Skeleton className="h-4 w-48" />
          <Skeleton className="h-24 w-full" />
        </CardContent>
      </Card>
    </div>
  );
}

function AnalyticsToolbar({
  range,
  onChangeRange,
  granularity,
  onChangeGranularity,
}: Readonly<{
  range: AnalyticsRange;
  onChangeRange: (range: AnalyticsRange) => void;
  granularity: AnalyticsGranularity;
  onChangeGranularity: (granularity: AnalyticsGranularity) => void;
}>) {
  return (
    <div className="flex flex-wrap items-center gap-2.5" data-testid="analytics-toolbar">
      <Dropdown>
        <DropdownTrigger asChild>
          <Button
            variant="secondary"
            size="sm"
            aria-label="Select date range"
            className={cn(range !== '90d' && CHIP_ACTIVE_CLASS)}
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
        ariaLabel="Granularity"
      />
    </div>
  );
}

function TrendCard({
  title,
  description,
  badge,
  points,
  yLabels,
  domainMax,
}: Readonly<{
  title: string;
  description: string;
  badge?: string;
  points: TrendPoint[];
  yLabels: string[];
  domainMax?: number;
}>) {
  const firstLabel = points[0]?.label ?? '';
  const lastLabel = points[points.length - 1]?.label ?? '';
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-2">
        <div className="grid gap-1">
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
        {badge ? <Badge variant="neutral">{badge}</Badge> : null}
      </CardHeader>
      <CardContent>
        <div className="flex gap-3">
          <div
            className="text-2xs text-muted flex flex-col justify-between py-1 font-mono"
            aria-hidden
          >
            {yLabels.map((label) => (
              <span key={label}>{label}</span>
            ))}
          </div>
          <div className="min-w-0 flex-1">
            <TrendChart
              label={title}
              data={points}
              width={680}
              height={180}
              domainMax={domainMax}
              className="h-[180px] w-full"
            />
            {points.length > 1 ? (
              <div
                className="text-2xs text-muted mt-1 flex justify-between font-mono"
                aria-hidden
              >
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

function ReferralVolumeCard({ data }: Readonly<{ data: LlmAnalytics }>) {
  const values = data.referral_volume.flatMap((point) =>
    point.value === null ? [] : [point.value],
  );
  const domainMax = countDomainMax(values);
  return (
    <TrendCard
      title="AI-referral volume"
      description="Sessions referred by AI assistants and answer engines"
      badge={bucketCountLabel(data.granularity, data.referral_volume.length)}
      points={toCountChartPoints(data.referral_volume)}
      yLabels={countYLabels(domainMax)}
      domainMax={domainMax}
    />
  );
}

function ReferralShareCard({ data }: Readonly<{ data: LlmAnalytics }>) {
  return (
    <TrendCard
      title="Referral share"
      description="AI-referral sessions as a share of all tracked sessions · 0–100% scale"
      points={toPercentChartPoints(data.referral_share)}
      yLabels={['100%', '75%', '50%', '25%', '0%']}
    />
  );
}

function SourceBreakdownCard({
  data,
  range,
}: Readonly<{ data: LlmAnalytics; range: AnalyticsRange }>) {
  const segments = sourceSegments(data.sources);
  return (
    <Card>
      <CardHeader>
        <CardTitle>AI referrals by source</CardTitle>
        <CardDescription>{`Classified sessions per AI source · ${rangeLabel(range).toLowerCase()}`}</CardDescription>
      </CardHeader>
      <CardContent>
        {segments.length === 0 ? (
          <p className="text-secondary text-sm">No classified referral sessions in this window.</p>
        ) : (
          <Donut
            segments={segments}
            size={148}
            label="AI referrals by source"
            centerLabel={formatInt(totalSourceSessions(data.sources))}
          />
        )}
      </CardContent>
    </Card>
  );
}

function CorrelationCard({
  data,
  granularity,
}: Readonly<{ data: LlmAnalytics; granularity: AnalyticsGranularity }>) {
  const display = correlationDisplay(data.correlation, granularity);
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-2">
        <div className="grid gap-1">
          <CardEyebrow>Correlation</CardEyebrow>
          <CardTitle>Visibility ↔ AI-referral correlation</CardTitle>
        </div>
        {display.insufficient ? <Badge variant="neutral">{display.badge}</Badge> : null}
      </CardHeader>
      <CardContent className="grid gap-3">
        <span
          className={cn(
            'mono text-2xl font-semibold tracking-tight',
            display.insufficient ? 'text-subtle' : 'text-foreground',
          )}
        >
          {display.value}
        </span>
        {display.insufficient ? null : (
          <div>
            <Badge variant="neutral">{display.badge}</Badge>
          </div>
        )}
        <CardDescription>{display.description}</CardDescription>
      </CardContent>
    </Card>
  );
}

function EngineVisibilityCard({
  data,
  granularity,
}: Readonly<{ data: LlmAnalytics; granularity: AnalyticsGranularity }>) {
  const engines = sortEngineVisibility(data.engine_visibility);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Cross-engine visibility</CardTitle>
        <CardDescription>{`${bucketAdjectiveTitle(granularity)} visibility score per audited engine · 0–100 scale`}</CardDescription>
      </CardHeader>
      <CardContent>
        {engines.length === 0 ? (
          <p className="text-secondary text-sm">
            No audited-engine visibility in this window yet.
          </p>
        ) : (
          <div className="grid gap-4 md:grid-cols-3">
            {engines.map((engine) => (
              <EngineTile key={engine.logical_engine} engine={engine} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function EngineTile({
  engine,
}: Readonly<{ engine: LlmAnalytics['engine_visibility'][number] }>) {
  const latest = latestValue(engine.series);
  const points = toCountChartPoints(engine.series);
  const firstLabel = points[0]?.label ?? '';
  const lastLabel = points[points.length - 1]?.label ?? '';
  return (
    <div className="border-border-subtle bg-background-alt grid gap-2.5 rounded-lg border p-4">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            'size-1.5 rounded-full',
            ENGINE_DOT_CLASS[engine.logical_engine] ?? 'bg-accent',
          )}
          aria-hidden
        />
        <span className="text-foreground text-sm font-semibold">
          {engineLabel(engine.logical_engine)}
        </span>
        <span
          className={cn(
            'mono ml-auto text-lg font-semibold',
            latest === null ? 'text-subtle' : 'text-foreground',
          )}
        >
          {latest === null ? '—' : Math.round(latest)}
        </span>
      </div>
      <div>
        <TrendChart
          label={`${engineLabel(engine.logical_engine)} visibility`}
          data={points}
          width={300}
          height={96}
          className="h-24 w-full"
        />
        {points.length > 1 ? (
          <div className="text-2xs text-muted mt-1 flex justify-between font-mono" aria-hidden>
            <span>{firstLabel}</span>
            <span>{lastLabel}</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ThemesCard({
  query,
}: Readonly<{ query: UseQueryResult<LlmAnalyticsThemeRow[], unknown> }>) {
  const rows = query.data ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Themes</CardTitle>
        <CardDescription>
          Visibility metrics rolled up by prompt theme and intent
        </CardDescription>
      </CardHeader>
      {query.isLoading ? (
        <CardContent className="grid gap-2" aria-hidden>
          {[0, 1].map((index) => (
            <Skeleton key={index} className="h-10 w-full" />
          ))}
        </CardContent>
      ) : query.isError ? (
        <CardContent>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-secondary text-sm">
              Could not load the theme rollup. Check your connection and try again.
            </p>
            <Button variant="secondary" size="sm" onClick={() => query.refetch()}>
              Retry
            </Button>
          </div>
        </CardContent>
      ) : rows.length === 0 ? (
        <CardContent>
          <p className="text-secondary text-sm">No theme rollups in this window yet.</p>
        </CardContent>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Theme</TableHead>
              <TableHead numeric>Prompts</TableHead>
              <TableHead numeric>Mentions</TableHead>
              <TableHead numeric>Visibility</TableHead>
              <TableHead numeric>SOV</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={`${row.theme}-${row.intent}`}>
                <TableCell>
                  <span className="flex items-center gap-2">
                    {row.theme}
                    <Badge variant="neutral">{row.intent}</Badge>
                  </span>
                </TableCell>
                <TableCell numeric>
                  <span className="mono">{row.total_completed}</span>
                </TableCell>
                <TableCell numeric>
                  <RateCell rate={row.brand_mention_rate} />
                </TableCell>
                <TableCell numeric>
                  <VisibilityCell score={row.visibility_score} />
                </TableCell>
                <TableCell numeric>
                  <RateCell rate={row.share_of_voice} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Card>
  );
}

function RateCell({ rate }: Readonly<{ rate: number | null }>) {
  if (rate === null) return <span className="text-subtle mono">—</span>;
  return <span className="mono">{formatPercent(rate)}</span>;
}

function VisibilityCell({ score }: Readonly<{ score: number | null }>) {
  if (score === null) return <span className="text-subtle mono">—</span>;
  return (
    <span className={cn('mono', scoreBandText[scoreBand(score)])}>{`${Math.round(score)}%`}</span>
  );
}
