'use client';

import { useQuery } from '@tanstack/react-query';
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react';
import { useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { CursorPager } from '@/components/ui/cursor-pager';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { queryKeys } from '@/lib/api/query-keys';
import { trafficApi, type TrafficQueryRow } from '@/lib/api/traffic';
import { useCursorStack } from '@/lib/site-health/use-cursor-stack';
import {
  describeSort,
  formatCount,
  formatCtr,
  formatPosition,
  NULL_PLACEHOLDER,
  sortDirection,
  sortKey,
  toggleSort,
} from '@/lib/traffic/traffic';
import { cn } from '@/lib/utils';

/** The sortable metric columns (exactly the backend queries sort whitelist). */
const SORTABLE_COLUMNS = [
  { key: 'impressions', label: 'Impressions' },
  { key: 'clicks', label: 'Clicks' },
  { key: 'ctr', label: 'CTR' },
  { key: 'position', label: 'Position' },
] as const;

/** The default "top rows" view (mockup: sorted by clicks, descending). */
const DEFAULT_SORT = '-clicks';

function SortableColumnHead({
  columnKey,
  label,
  sort,
  onSort,
}: Readonly<{ columnKey: string; label: string; sort: string; onSort: (key: string) => void }>) {
  const active = sortKey(sort) === columnKey;
  const descending = sortDirection(sort) === 'descending';
  return (
    <TableHead
      numeric
      aria-sort={active ? (descending ? 'descending' : 'ascending') : undefined}
    >
      <button
        type="button"
        onClick={() => onSort(columnKey)}
        className={cn(
          'inline-flex items-center gap-1',
          active ? 'text-accent-text' : 'hover:text-foreground',
        )}
      >
        {label}
        {active ? (
          descending ? (
            <ArrowDown className="size-3" aria-hidden />
          ) : (
            <ArrowUp className="size-3" aria-hidden />
          )
        ) : (
          <ArrowUpDown className="text-subtle size-3" aria-hidden />
        )}
      </button>
    </TableHead>
  );
}

function NumericCell({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <TableCell numeric>
      <span className="font-mono">{children}</span>
    </TableCell>
  );
}

function NullCell() {
  return (
    <TableCell numeric>
      <span className="text-subtle">{NULL_PLACEHOLDER}</span>
    </TableCell>
  );
}

function QueryRow({ row }: Readonly<{ row: TrafficQueryRow }>) {
  return (
    <TableRow>
      <TableCell>{row.normalized_query}</TableCell>
      <NumericCell>{formatCount(row.impressions)}</NumericCell>
      <NumericCell>{formatCount(row.clicks)}</NumericCell>
      {row.ctr === null ? <NullCell /> : <NumericCell>{formatCtr(row.ctr)}</NumericCell>}
      {row.position === null ? (
        <NullCell />
      ) : (
        <NumericCell>{formatPosition(row.position)}</NumericCell>
      )}
    </TableRow>
  );
}

/**
 * Top-queries table (F7; mockup `queries-table`): the persisted per-query
 * stat rows for the served window — normalized search queries with GSC
 * impressions / clicks / CTR / position — as a dense table with keyset
 * paging (`CursorPager` + `useCursorStack`, the shared site-health idiom) and
 * backend-whitelisted `?sort=` columns. Numeric cells are mono tabular; null
 * metrics render the em-dash placeholder, never a zero. The cursor stack
 * resets whenever the sort changes (a keyset cursor is bound to its sort
 * fingerprint).
 */
export function QueriesTable({
  projectId,
  from,
  to,
}: Readonly<{ projectId: string; from?: string; to?: string }>) {
  const pager = useCursorStack();
  const [sort, setSort] = useState(DEFAULT_SORT);

  const queriesQuery = useQuery({
    queryKey: queryKeys.traffic.queries(projectId, { from, to, sort, cursor: pager.cursor }),
    queryFn: ({ signal }) =>
      trafficApi.getQueries(projectId, { from, to, sort, cursor: pager.cursor }, { signal }),
  });

  const onSort = (key: string) => {
    setSort((current) => toggleSort(current, key));
    pager.reset();
  };

  const rows = queriesQuery.data?.items ?? [];
  const nextCursor = queriesQuery.data?.next_cursor ?? null;

  return (
    <Card data-testid="queries-table">
      <CardHeader>
        <CardTitle>Top queries</CardTitle>
        <CardDescription>
          Normalized search queries by organic clicks · Google Search Console
        </CardDescription>
      </CardHeader>

      {queriesQuery.isError ? (
        <div className="p-[var(--card-padding)]">
          <Alert tone="danger">
            Could not load query stats. Check your connection and try again.
          </Alert>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Query</TableHead>
              {SORTABLE_COLUMNS.map((column) => (
                <SortableColumnHead
                  key={column.key}
                  columnKey={column.key}
                  label={column.label}
                  sort={sort}
                  onSort={onSort}
                />
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {queriesQuery.isLoading
              ? Array.from({ length: 5 }, (_, i) => (
                  <TableRow key={`skeleton-${i}`}>
                    <TableCell>
                      <Skeleton className="h-4 w-48 max-w-full" />
                    </TableCell>
                    {SORTABLE_COLUMNS.map((column) => (
                      <TableCell key={column.key} numeric>
                        <Skeleton className="mx-auto h-4 w-12" />
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              : null}
            {!queriesQuery.isLoading && rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={1 + SORTABLE_COLUMNS.length}>
                  <span className="text-muted">No queries measured for this window.</span>
                </TableCell>
              </TableRow>
            ) : null}
            {rows.map((row) => (
              <QueryRow key={row.normalized_query} row={row} />
            ))}
          </TableBody>
        </Table>
      )}

      <div className="border-border-subtle flex items-center justify-between gap-3 border-t px-3 py-2">
        <span className="text-2xs text-muted font-mono">{describeSort(sort)}</span>
        <div className="flex items-center gap-2">
          <CursorPager
            canPrev={pager.canPrev}
            canNext={Boolean(nextCursor)}
            onPrev={pager.pop}
            onNext={() => pager.push(nextCursor)}
          />
        </div>
      </div>
    </Card>
  );
}
