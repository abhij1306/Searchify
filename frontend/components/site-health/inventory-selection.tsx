'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { CursorPager } from '@/components/ui/cursor-pager';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Label } from '@/components/ui/typography';
import { InventoryTable } from '@/components/site-health/inventory-table';
import { QuickSelectBar } from '@/components/site-health/quick-select-bar';
import { SelectionNotices } from '@/components/site-health/selection-notices';
import { siteHealthQueries } from '@/lib/api/site-health';
import type { SiteCrawl, SiteHealthEntitlement } from '@/lib/api/types';
import {
  changeInventoryFilters,
  emptyInventoryFilters,
  toInventoryParams,
  type InventoryFilters,
} from '@/lib/site-health/filters';
import {
  allStaged,
  commitCtaLabel,
  setManyStaged,
  toggleStaged,
} from '@/lib/site-health/selection';
import { useMonitoredSelection } from '@/lib/site-health/use-monitored-selection';

const PAGE_LIMIT = 25;

/**
 * Starter monitored-selection (Slice 7, mockup 709).
 *
 * A cursor-paginated inventory with search/status filters where the user stages
 * the persistent monitored set. Staging/commit/bulk semantics (including stale
 * `selection_version` recovery) live in `useMonitoredSelection`; this component
 * owns only the inventory pagination/filters and layout.
 *
 * Also serves a CANCELLED crawl (`crawlInactive`): the discovered inventory
 * survives a cancel, so the user can still browse it and stage/commit a
 * monitored set — but analysis tasks only enqueue into an active crawl, so a
 * "Start analysis" action (a fresh crawl that seeds the persisted selection)
 * is offered instead of the implicit in-crawl analysis.
 */
export function InventorySelection({
  crawl,
  entitlement,
  projectId,
  crawlInactive = false,
  onStartAnalysis,
  startPending = false,
}: Readonly<{
  crawl: SiteCrawl;
  entitlement: SiteHealthEntitlement;
  projectId: string;
  /** True when the crawl is terminal (cancelled) — analysis needs a new crawl. */
  crawlInactive?: boolean;
  /** Starts a fresh crawl that seeds the committed monitored set (re-analysis). */
  onStartAnalysis?: () => void;
  startPending?: boolean;
}>) {
  const [filters, setFilters] = useState<InventoryFilters>(emptyInventoryFilters);
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const [searchInput, setSearchInput] = useState('');

  const cursor = cursorStack.at(-1) ?? undefined;

  const inventoryQuery = useQuery(
    siteHealthQueries.inventory(crawl.id, {
      ...toInventoryParams(filters, cursor, PAGE_LIMIT),
    }),
  );

  const rows = inventoryQuery.data?.items ?? [];
  const nextCursor = inventoryQuery.data?.next_cursor ?? null;
  const canPrev = cursorStack.length > 0;

  const homepageId = inventoryQuery.data?.items.find(
    (row) => row.normalized_url === crawl.root_url,
  )?.site_url_id;

  const {
    monitoredQuery,
    effectiveSelection,
    setSelection,
    delta,
    quota,
    staleNotice,
    replaceMutation,
    bulkSelectMutation,
    bulkSelect,
    bulkSelectError,
    commit,
  } = useMonitoredSelection({
    crawl,
    entitlement,
    projectId,
    homepageId,
    // Don't initialize the staging session until the inventory has settled —
    // the homepage lookup above is only meaningful once rows are loaded.
    inventoryReady: inventoryQuery.isSuccess,
    searchQuery: filters.query,
  });

  const visibleIds = rows.map((row) => row.site_url_id);
  const allVisibleStaged = effectiveSelection ? allStaged(effectiveSelection, visibleIds) : false;

  const applyFilters = (next: Partial<InventoryFilters>) => {
    const changed = changeInventoryFilters(filters, next);
    setFilters(changed.filters);
    setCursorStack([]);
  };

  if (monitoredQuery.isLoading || inventoryQuery.isLoading) {
    return (
      <Card>
        <CardContent className="grid gap-3">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-40 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (monitoredQuery.isError || inventoryQuery.isError) {
    return <Alert tone="danger">Could not load the page inventory. Please refresh.</Alert>;
  }

  return (
    <Card>
      <CardContent className="grid gap-4">
        {crawlInactive ? (
          <Alert tone="info">
            Discovery was cancelled — the pages found so far are kept below. Select the pages to
            monitor, save your selection, then start the analysis.
          </Alert>
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="grid gap-0.5">
            <Label>Page Inventory</Label>
            <span className="text-sm text-secondary">
              Select pages to include in your health analysis — selections persist across
              re-crawls.
            </span>
          </div>
          {quota ? (
            <span
              className={`text-sm font-medium ${quota.overLimit ? 'text-danger-text' : 'text-secondary'}`}
            >
              {quota.staged} of {quota.limit} selected
            </span>
          ) : null}
        </div>

        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            applyFilters({ query: searchInput });
          }}
        >
          <Input
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="Search pages…"
            className="max-w-xs"
            aria-label="Search pages"
          />
          <Button type="submit" variant="secondary" size="sm">
            Search
          </Button>
          {effectiveSelection ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() =>
                setSelection(
                  setManyStaged(effectiveSelection, visibleIds, !allVisibleStaged),
                )
              }
            >
              {allVisibleStaged ? 'Clear visible' : 'Select visible'}
            </Button>
          ) : null}
        </form>

        {effectiveSelection && entitlement.access_mode === 'selection' ? (
          <QuickSelectBar
            maxCount={entitlement.monitored_url_limit}
            pending={bulkSelectMutation.isPending}
            onBulkSelect={bulkSelect}
          />
        ) : null}

        <SelectionNotices
          bulkError={bulkSelectMutation.isError}
          bulkErrorMessage={bulkSelectError}
          quota={quota}
          staleNotice={staleNotice}
          replaceError={replaceMutation.isError}
        />

        <InventoryTable
          rows={rows}
          isStaged={(id) => effectiveSelection?.staged.has(id) ?? false}
          disabled={!effectiveSelection}
          onToggle={(id) =>
            effectiveSelection && setSelection(toggleStaged(effectiveSelection, id))
          }
        />

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle pt-4">
          <div className="flex items-center gap-2">
            <CursorPager
              canPrev={canPrev}
              canNext={Boolean(nextCursor)}
              onPrev={() => setCursorStack((prev) => prev.slice(0, -1))}
              onNext={() =>
                nextCursor &&
                setCursorStack((prev) =>
                  // Idempotent under rapid clicks: the captured nextCursor may
                  // already be on the stack before the rerender lands.
                  prev.at(-1) === nextCursor ? prev : [...prev, nextCursor],
                )
              }
            />
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted">
              Selections are saved and persist across re-crawls.
            </span>
            <Button
              size="sm"
              variant={crawlInactive && !delta?.dirty ? 'secondary' : 'primary'}
              onClick={commit}
              disabled={
                !effectiveSelection ||
                !delta?.dirty ||
                quota?.overLimit ||
                replaceMutation.isPending
              }
            >
              {replaceMutation.isPending
                ? 'Saving…'
                : effectiveSelection
                  ? crawlInactive
                    ? `Save selection (${effectiveSelection.staged.size} of ${entitlement.monitored_url_limit})`
                    : commitCtaLabel(effectiveSelection, entitlement.monitored_url_limit)
                  : 'Analyze pages'}
            </Button>
            {crawlInactive && onStartAnalysis ? (
              // A cancelled crawl cannot enqueue analyze tasks, so analysis is
              // a fresh crawl: it re-discovers and seeds the COMMITTED
              // monitored set. Enabled once a committed (saved) set exists and
              // no unsaved edits are pending.
              <Button
                size="sm"
                onClick={onStartAnalysis}
                disabled={
                  startPending ||
                  !effectiveSelection ||
                  delta?.dirty ||
                  effectiveSelection.committed.siteUrlIds.size === 0
                }
              >
                {startPending ? 'Starting…' : 'Start analysis'}
              </Button>
            ) : null}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
