'use client';

import Link from 'next/link';
import { ChevronDown, Download, Inbox, Package, RefreshCw } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardEyebrow, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dropdown,
  DropdownContent,
  DropdownItem,
  DropdownLabel,
  DropdownTrigger,
} from '@/components/ui/dropdown';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { displayHeadingLgClasses } from '@/components/ui/typography';
import { ApiError } from '@/lib/api/errors';
import { productsApi } from '@/lib/api/products';
import type {
  CompetitorProductVisibilityEntry,
  ProductVisibilityEntry,
} from '@/lib/api/types';
import {
  RANK_BUCKET_LABELS,
  RANK_BUCKET_ORDER,
  formatAvgRank,
  formatPercent,
  summarizeProductVisibility,
} from '@/lib/products/catalog';
import type { useProductVisibilityQueries } from '@/lib/products/use-products-screen';
import { engineLabel } from '@/lib/providers/catalog';
import { ENGINE_ORDER } from '@/lib/providers/catalog';
import { cn } from '@/lib/utils';

type VisibilityQueries = ReturnType<typeof useProductVisibilityQueries>;

const RANK_SEGMENT_CLASS: Record<(typeof RANK_BUCKET_ORDER)[number], string> = {
  top_1: 'bg-success',
  top_2_3: 'bg-info',
  top_4_5: 'bg-warning',
  rank_6_plus: 'bg-danger',
  unranked: 'bg-muted',
};

/**
 * Visibility tab (agentic commerce): the selected run's product-vs-competitor
 * projection. A filter bar (Run selector defaulting to latest, engine slice,
 * CSV export) sits above a catalog-wide summary strip (Product SOV, product
 * mentions, avg rank, price-mention accuracy) and the ranked tables — own
 * products first, competitor products second. All values are persisted
 * backend aggregates; states mirror the visibility evidence-states gallery
 * (skeleton / retryable error / no-audit empty / no-catalog CTA).
 */
export function ProductVisibilityPanel({
  projectId,
  queries,
  onGoToCatalog,
}: Readonly<{
  projectId: string;
  queries: VisibilityQueries;
  onGoToCatalog: () => void;
}>) {
  const { runOptions, activeRunId, selectRun, engine, setEngine, visibilityQuery } = queries;

  if (visibilityQuery.isLoading) {
    return <VisibilitySkeleton />;
  }

  if (visibilityQuery.isError) {
    const error = visibilityQuery.error;
    // 404 = no completed run with product metrics yet (no-audit) OR the run
    // predates / lacks a catalog (no-catalog CTA).
    if (error instanceof ApiError && error.status === 404) {
      return <NoAuditEmpty onGoToCatalog={onGoToCatalog} />;
    }
    return (
      <Card>
        <CardContent>
          <div className="grid justify-items-center gap-3 py-10 text-center">
            <CardEyebrow>Product visibility</CardEyebrow>
            <h3 className={displayHeadingLgClasses}>Couldn&apos;t load product visibility</h3>
            <p className="text-secondary max-w-xs text-sm">
              The request failed or timed out. Your filters are unchanged.
            </p>
            <Button variant="primary" size="sm" onClick={() => visibilityQuery.refetch()}>
              <RefreshCw className="size-4" aria-hidden />
              Retry
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  const visibility = visibilityQuery.data;
  if (!visibility) return <VisibilitySkeleton />;

  const summary = summarizeProductVisibility(visibility);
  const activeRun = runOptions.find((run) => run.id === activeRunId) ?? null;

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2.5" data-testid="product-visibility-toolbar">
        <Dropdown>
          <DropdownTrigger asChild>
            <Button variant="secondary" size="sm" aria-label="Select run">
              <span className="text-muted">Run:</span>
              <span className="font-medium">{activeRun?.label ?? 'Latest'}</span>
              <ChevronDown className="text-muted size-3" aria-hidden />
            </Button>
          </DropdownTrigger>
          <DropdownContent>
            <DropdownLabel>Runs</DropdownLabel>
            <DropdownItem data-active={activeRunId === null} onSelect={() => selectRun(null)}>
              Latest
            </DropdownItem>
            {runOptions.map((run) => (
              <DropdownItem
                key={run.id}
                data-active={run.id === activeRunId}
                onSelect={() => selectRun(run.id)}
              >
                {run.label}
              </DropdownItem>
            ))}
          </DropdownContent>
        </Dropdown>

        <Dropdown>
          <DropdownTrigger asChild>
            <Button variant="secondary" size="sm" aria-label="Filter by engine">
              <span className="text-muted">Engine:</span>
              <span className="font-medium">
                {engine === 'all' ? 'All engines' : engineLabel(engine)}
              </span>
              <ChevronDown className="text-muted size-3" aria-hidden />
            </Button>
          </DropdownTrigger>
          <DropdownContent>
            <DropdownLabel>Engine</DropdownLabel>
            <DropdownItem data-active={engine === 'all'} onSelect={() => setEngine('all')}>
              All engines
            </DropdownItem>
            {ENGINE_ORDER.map((option) => (
              <DropdownItem
                key={option}
                data-active={engine === option}
                onSelect={() => setEngine(option)}
              >
                {engineLabel(option)}
              </DropdownItem>
            ))}
          </DropdownContent>
        </Dropdown>

        <div className="ml-auto">
          <Button asChild variant="ghost" size="sm">
            <a href={productsApi.exportCsvUrl(projectId, activeRunId ?? undefined)} download>
              <Download className="size-4" aria-hidden />
              Export CSV
            </a>
          </Button>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          label="Product SOV"
          value={formatPercent(summary.sov)}
          caption="Your share of all product mentions in this run"
        />
        <SummaryCard
          label="Product mentions"
          value={String(summary.ownMentions)}
          caption={`of ${summary.totalMentions} product mentions`}
        />
        <SummaryCard
          label="Avg rank in product lists"
          value={formatAvgRank(summary.avgRank)}
          caption="Average position when your products are listed"
        />
        <SummaryCard
          label="Price-mention accuracy"
          value={formatPercent(summary.priceAccuracy)}
          caption="Extracted prices matching the catalog"
        />
      </div>

      <RankingsCard
        title="Product rankings"
        description="Your products — mentions, rank distribution, and price accuracy for the selected run."
        rows={visibility.products}
        kind="own"
      />
      <RankingsCard
        title="Competitor products"
        description="Competitor products measured in the same run."
        rows={visibility.competitor_products}
        kind="competitor"
      />
    </div>
  );
}

function SummaryCard({
  label,
  value,
  caption,
}: Readonly<{ label: string; value: string; caption: string }>) {
  return (
    <Card>
      <CardContent className="grid gap-1">
        <CardEyebrow>{label}</CardEyebrow>
        <p className="font-mono text-2xl tabular-nums">{value}</p>
        <p className="text-muted text-xs">{caption}</p>
      </CardContent>
    </Card>
  );
}

type RankingRow =
  | { kind: 'own'; entry: ProductVisibilityEntry }
  | { kind: 'competitor'; entry: CompetitorProductVisibilityEntry };

function RankingsCard({
  title,
  description,
  rows,
  kind,
}: Readonly<{
  title: string;
  description: string;
  rows: ProductVisibilityEntry[] | CompetitorProductVisibilityEntry[];
  kind: 'own' | 'competitor';
}>) {
  const normalized: RankingRow[] = rows.map((entry) => ({ kind, entry }) as RankingRow);
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="grid gap-1">
          <CardTitle>{title}</CardTitle>
          <p className="text-secondary text-sm">{description}</p>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {normalized.length === 0 ? (
          <p className="text-secondary p-[var(--card-padding)] text-sm">
            Nothing measured here in the selected run.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">#</TableHead>
                <TableHead>Product</TableHead>
                <TableHead>Mentions</TableHead>
                <TableHead>SOV</TableHead>
                <TableHead className="min-w-[140px]">Rank distribution</TableHead>
                <TableHead>Avg rank</TableHead>
                <TableHead>Price accuracy</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {normalized.map((row, index) => (
                <RankingTableRow key={rowKey(row) ?? index} row={row} position={index + 1} />
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function rowKey(row: RankingRow): string | null {
  return row.kind === 'own' ? row.entry.product_id : row.entry.competitor_product_id;
}

function RankingTableRow({ row, position }: Readonly<{ row: RankingRow; position: number }>) {
  const { entry } = row;
  const id = rowKey(row);
  const subtitle = row.kind === 'own' ? row.entry.sku : row.entry.competitor_name;
  return (
    <TableRow>
      <TableCell numeric className="text-muted">
        {position}
      </TableCell>
      <TableCell className="max-w-[280px] min-w-[180px]">
        <div className="grid gap-0.5">
          <span className="flex items-center gap-2">
            {row.kind === 'own' && id ? (
              <Link
                href={`/products/${id}`}
                className="text-foreground hover:text-accent-text truncate font-medium transition-colors"
              >
                {entry.name}
              </Link>
            ) : (
              <span className="text-foreground truncate font-medium">{entry.name}</span>
            )}
            {row.kind === 'own' ? (
              <Badge variant="status" value="info">
                You
              </Badge>
            ) : null}
          </span>
          {subtitle ? <span className="text-muted truncate text-xs">{subtitle}</span> : null}
        </div>
      </TableCell>
      <TableCell numeric className="text-secondary">
        {entry.mention_count}
      </TableCell>
      <TableCell numeric className="text-secondary">
        {formatPercent(entry.sov_share)}
      </TableCell>
      <TableCell>
        <RankDistributionBar distribution={entry.rank_distribution} />
      </TableCell>
      <TableCell numeric className="text-secondary">
        {formatAvgRank(entry.avg_rank)}
      </TableCell>
      <TableCell numeric className="text-secondary">
        {formatPercent(entry.price_accuracy_rate)}
      </TableCell>
    </TableRow>
  );
}

/**
 * Compact stacked bar of the persisted rank buckets (Top 1 → 6+ → unranked).
 * Each segment carries a `title` with its bucket label + count so the value
 * is never color-only.
 */
function RankDistributionBar({
  distribution,
}: Readonly<{ distribution: Record<string, number> }>) {
  const total = RANK_BUCKET_ORDER.reduce((sum, key) => sum + (distribution[key] ?? 0), 0);
  if (total === 0) return <span className="text-subtle">—</span>;
  return (
    <div
      className="bg-neutral-bg flex h-2 w-full overflow-hidden rounded-full"
      role="img"
      aria-label={RANK_BUCKET_ORDER.map(
        (key) => `${RANK_BUCKET_LABELS[key]}: ${distribution[key] ?? 0}`,
      ).join(', ')}
    >
      {RANK_BUCKET_ORDER.map((key) => {
        const count = distribution[key] ?? 0;
        if (count === 0) return null;
        return (
          <span
            key={key}
            title={`${RANK_BUCKET_LABELS[key]}: ${count}`}
            className={cn('h-full', RANK_SEGMENT_CLASS[key])}
            style={{ width: `${(count / total) * 100}%` }}
          />
        );
      })}
    </div>
  );
}

function VisibilitySkeleton() {
  return (
    <div className="grid gap-4" aria-hidden>
      <Skeleton className="h-9 w-96" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24 w-full" />
        ))}
      </div>
      <Skeleton className="h-56 w-full" />
    </div>
  );
}

/** No completed run has product metrics yet — guide to runs and the catalog. */
function NoAuditEmpty({ onGoToCatalog }: Readonly<{ onGoToCatalog: () => void }>) {
  return (
    <Card>
      <CardContent className="grid justify-items-center gap-4 py-12 text-center">
        <CardEyebrow>Product visibility</CardEyebrow>
        <span className="bg-neutral-bg text-muted flex size-10 items-center justify-center rounded-full">
          <Inbox className="size-5" aria-hidden />
        </span>
        <div className="grid gap-1">
          <h2 className={displayHeadingLgClasses}>No product visibility yet</h2>
          <p className="text-secondary max-w-md text-sm">
            Once a run completes with products in your catalog, each product&apos;s share of
            voice, rank distribution, and price accuracy appear here. An empty catalog measures
            nothing — add products first, then launch a run.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="md" onClick={onGoToCatalog}>
            <Package className="size-4" aria-hidden />
            Go to Catalog
          </Button>
          <Button asChild variant="primary" size="md">
            <Link href="/runs">View runs</Link>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
