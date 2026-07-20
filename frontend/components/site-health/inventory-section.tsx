'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Card, CardContent } from '@/components/ui/card';
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
import { Label } from '@/components/ui/typography';
import { InventorySelection } from '@/components/site-health/inventory-selection';
import { PagesTable } from '@/components/site-health/pages-table';
import { siteHealthQueries, type PagesParams } from '@/lib/api/site-health';
import type { PageSummary, SiteCrawl, SiteHealthEntitlement } from '@/lib/api/types';
import { cn } from '@/lib/utils';
import {
  PAGE_LIMIT,
  POLL_INTERVAL_MS,
  statusLabel,
  type InventoryMode,
} from '@/lib/site-health/status';

/**
 * Always-mounted inventory section of the canonical Site Health screen.
 *
 * ONE persistent region that renders the crawl's pages through the whole
 * lifecycle — the rows enrich in place instead of the screen swapping:
 *   - 'discovering': read-only rows streaming in as discovery finds them;
 *   - 'selectable':  Starter monitored-set staging (checkboxes + commit +
 *                    Start analysis) via `InventorySelection`;
 *   - 'analyzing':   the monitored window with live per-page audit statuses;
 *   - 'scored':      the tabbed (monitored / all / errors) scored browser;
 *   - 'none':        an in-section empty state (nothing discovered yet).
 *
 * The section wrapper (and its `data-testid`) never unmounts across modes —
 * that stability is asserted by the screen regression tests.
 */
export function InventorySection({
  mode,
  crawl,
  entitlement,
  projectId,
  active,
  pages,
  pagesError,
  pagesLoading,
  onStartAnalysis,
  startPending,
}: Readonly<{
  mode: InventoryMode;
  crawl: SiteCrawl | null;
  entitlement: SiteHealthEntitlement;
  projectId: string;
  /** True while the crawl is non-terminal (keeps inventory/pages polling). */
  active: boolean;
  /** Bounded monitored-page window for the analyzing per-page table. */
  pages: PageSummary[];
  /** True when the per-page window fetch failed (an empty table would mislead). */
  pagesError: boolean;
  /** True while the per-page window loads for the first time (no rows yet). */
  pagesLoading: boolean;
  /** Starts a fresh crawl that seeds the committed monitored set. */
  onStartAnalysis: () => void;
  startPending: boolean;
}>) {
  return (
    <div className="grid gap-2" data-testid="inventory-section">
      {mode === 'none' || !crawl ? (
        <Card>
          <CardContent className="py-6 text-center">
            <p className="text-secondary text-sm">
              Pages appear here as discovery finds them.
            </p>
          </CardContent>
        </Card>
      ) : mode === 'discovering' ? (
        <DiscoveringInventory crawl={crawl} active={active} />
      ) : mode === 'selectable' ? (
        <InventorySelection
          crawl={crawl}
          entitlement={entitlement}
          projectId={projectId}
          // A cancelled crawl keeps its discovered inventory but can no longer
          // run analysis itself — selections persist, and "Start analysis"
          // launches a fresh crawl that seeds them as analyze tasks.
          crawlInactive={!active}
          onStartAnalysis={onStartAnalysis}
          startPending={startPending}
        />
      ) : mode === 'analyzing' ? (
        <AnalyzingInventory
          crawl={crawl}
          pages={pages}
          pagesError={pagesError}
          pagesLoading={pagesLoading}
        />
      ) : (
        <ScoredInventory crawl={crawl} active={active} />
      )}
    </div>
  );
}

/**
 * Read-only admitted-URL inventory while discovery runs (bounded to this
 * crawl). The first page keeps polling so new URLs stream in; deeper pages
 * stay stable under keyset (normalized_url, id) ordering.
 */
function DiscoveringInventory({
  crawl,
  active,
}: Readonly<{ crawl: SiteCrawl; active: boolean }>) {
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const cursor = cursorStack.at(-1) ?? undefined;
  const inventoryQuery = useQuery({
    ...siteHealthQueries.inventory(crawl.id, { cursor, limit: PAGE_LIMIT }),
    // Only the FIRST page polls — deeper cursor pages stay static so the rows
    // under review don't shift as new URLs are discovered.
    refetchInterval: active && cursor === undefined ? POLL_INTERVAL_MS : false,
  });
  const rows = inventoryQuery.data?.items ?? [];
  const nextCursor = inventoryQuery.data?.next_cursor ?? null;
  const canPrev = cursorStack.length > 0;

  return (
    <Card>
      <CardContent className="grid gap-2">
        <Label>Pages discovered so far</Label>
        {inventoryQuery.isError ? (
          <Alert tone="danger">Could not load the page inventory. Please refresh.</Alert>
        ) : rows.length === 0 ? (
          <p className="text-secondary text-sm">
            Discovering pages — sitemaps and internal links are being scanned.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Page URL</TableHead>
                <TableHead>Source</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.site_url_id}>
                  <TableCell>
                    <span className="mono text-foreground text-xs">{row.display_url}</span>
                  </TableCell>
                  <TableCell className="text-secondary text-xs">
                    {row.source ? statusLabel(row.source) : '—'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <div className="flex flex-wrap items-center justify-between gap-3">
          {crawl.status === 'running' || crawl.status === 'queued' ? (
            <p className="text-muted text-xs">More URLs appear as discovery continues.</p>
          ) : (
            <span />
          )}
          {canPrev || nextCursor ? (
            <div className="flex items-center gap-2">
              <CursorPager
                canPrev={canPrev}
                canNext={Boolean(nextCursor)}
                onPrev={() => setCursorStack((prev) => prev.slice(0, -1))}
                onNext={() =>
                  nextCursor &&
                  setCursorStack((prev) =>
                    // Idempotent under rapid clicks: the captured nextCursor
                    // may already be on the stack before the rerender lands.
                    prev.at(-1) === nextCursor ? prev : [...prev, nextCursor],
                  )
                }
              />
            </div>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * Per-page audit table while analysis runs. `pages` is a bounded window of the
 * monitored subset (rows move queued → running → completed/error/blocked via
 * the parent's polling + SSE invalidation); progress COUNTS live in the status
 * strip, never here.
 */
function AnalyzingInventory({
  crawl,
  pages,
  pagesError,
  pagesLoading,
}: Readonly<{
  crawl: SiteCrawl;
  pages: PageSummary[];
  pagesError: boolean;
  pagesLoading: boolean;
}>) {
  return (
    <Card>
      <CardContent className="p-0">
        {pagesError ? (
          <div className="p-4">
            <Alert tone="warning">
              Could not load the per-page audit table
              {pages.length > 0 ? ' — showing the last loaded results.' : '.'} It will refresh
              automatically.
            </Alert>
          </div>
        ) : null}
        {pages.length === 0 && !pagesError && pagesLoading ? (
          <p className="text-muted p-4 text-sm" aria-live="polite">
            Loading audited pages…
          </p>
        ) : null}
        {/* Render the table only when there are rows to show, or the query
            resolved successfully with a genuine empty result. A failed or
            still-loading fetch with no cached rows must not render bare
            table headers that read as a valid "no pages" outcome. */}
        {pages.length > 0 || (!pagesError && !pagesLoading) ? (
          <PagesTable pages={pages} crawlId={crawl.id} />
        ) : null}
      </CardContent>
    </Card>
  );
}

/** The three server-backed page tabs (design mockup 713). */
type TabKey = 'monitored' | 'all' | 'errors';

const TABS: ReadonlyArray<{ key: TabKey; label: string; params: PagesParams }> = [
  { key: 'monitored', label: 'Monitored', params: { monitored: true } },
  { key: 'all', label: 'All Discovered', params: {} },
  { key: 'errors', label: 'Errors & Blocked', params: { status: 'error_or_blocked' } },
];

/**
 * Scored page browser (design mockup 713): three server-backed tabs, each its
 * OWN query with its own cursor stack — filtering is server-side, never a
 * filter over the current client page.
 */
function ScoredInventory({ crawl, active }: Readonly<{ crawl: SiteCrawl; active: boolean }>) {
  const [tab, setTab] = useState<TabKey>('monitored');
  // Per-tab cursor stack so Prev/Next walk keyset pages without offsets.
  const [cursorStack, setCursorStack] = useState<Record<TabKey, string[]>>({
    monitored: [],
    all: [],
    errors: [],
  });

  const activeTab = TABS.find((t) => t.key === tab)!;
  const cursor = cursorStack[tab].at(-1) ?? undefined;

  const pagesQuery = useQuery({
    ...siteHealthQueries.pages(crawl.id, { ...activeTab.params, cursor, limit: PAGE_LIMIT }),
    // While analysis is active only the FIRST page of a tab polls (polling
    // baseline) — deeper cursor pages stay static so rows under review don't
    // shift as more pages finish scoring.
    refetchInterval: active && cursor === undefined ? POLL_INTERVAL_MS : false,
  });

  const rows = pagesQuery.data?.items ?? [];
  const nextCursor = pagesQuery.data?.next_cursor ?? null;
  const canPrev = cursorStack[tab].length > 0;

  const goNext = () => {
    if (!nextCursor) return;
    setCursorStack((prev) =>
      // Idempotent under rapid clicks: the captured nextCursor may already be
      // on the stack before the rerender lands.
      prev[tab].at(-1) === nextCursor ? prev : { ...prev, [tab]: [...prev[tab], nextCursor] },
    );
  };
  const goPrev = () => setCursorStack((prev) => ({ ...prev, [tab]: prev[tab].slice(0, -1) }));

  return (
    <Card>
      <CardContent className="grid gap-4 p-0">
        <div className="border-border-subtle flex flex-wrap items-center gap-1 border-b px-[var(--card-padding)] pt-[var(--card-padding)]">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              aria-current={t.key === tab ? 'true' : undefined}
              className={cn(
                'rounded-t-md border-b-2 px-3 py-2 text-sm font-medium transition-colors',
                t.key === tab
                  ? 'border-accent text-foreground'
                  : 'text-secondary hover:text-foreground border-transparent',
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        {pagesQuery.isError ? (
          <div className="p-[var(--card-padding)]">
            <Alert tone="danger">Could not load pages for this view. Try again.</Alert>
          </div>
        ) : pagesQuery.isLoading ? (
          <div className="grid gap-2 p-[var(--card-padding)]">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : rows.length === 0 ? (
          <p className="text-secondary p-[var(--card-padding)] text-sm">No pages in this view.</p>
        ) : (
          <PagesTable pages={rows} crawlId={crawl.id} />
        )}

        {canPrev || nextCursor ? (
          <div className="border-border-subtle flex items-center justify-end gap-2 border-t px-[var(--card-padding)] py-3">
            <CursorPager
              canPrev={canPrev}
              canNext={Boolean(nextCursor)}
              onPrev={goPrev}
              onNext={goNext}
            />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
