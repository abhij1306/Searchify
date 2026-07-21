'use client';

import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
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
import type { SiteCrawl, SiteHealthEntitlement } from '@/lib/api/types';
import { cn } from '@/lib/utils';
import { useCursorStack } from '@/lib/site-health/use-cursor-stack';
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
 *   - 'scored':      the tabbed (monitored / all / errors) page browser used
 *                    BOTH while analysis runs and after it finishes — statuses
 *                    move queued → running → completed and scores fill in on
 *                    the same rows (no separate "analyzing" table);
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
  onCancel,
  cancelPending,
  onStartAnalysis,
  startPending,
}: Readonly<{
  mode: InventoryMode;
  crawl: SiteCrawl | null;
  entitlement: SiteHealthEntitlement;
  projectId: string;
  /** True while the crawl is non-terminal (keeps inventory/pages polling). */
  active: boolean;
  /** Cancels the active discovery/analysis crawl from the inventory controls. */
  onCancel: () => void;
  cancelPending: boolean;
  /** Starts a fresh crawl that seeds the committed monitored set. */
  onStartAnalysis: () => void;
  startPending: boolean;
}>) {
  let content: ReactNode;
  if (mode === 'none' || !crawl) {
    content = (
      <Card>
        <CardContent className="py-6 text-center">
          <p className="text-secondary text-sm">Pages appear here as discovery finds them.</p>
        </CardContent>
      </Card>
    );
  } else if (mode === 'discovering') {
    content = (
      <DiscoveringInventory
        crawl={crawl}
        active={active}
        onCancel={onCancel}
        cancelPending={cancelPending}
      />
    );
  } else if (mode === 'selectable') {
    content = (
      <InventorySelection
        crawl={crawl}
        entitlement={entitlement}
        projectId={projectId}
        // A cancelled crawl keeps its discovered inventory but can no longer
        // run analysis itself — selections persist, and "Start analysis"
        // launches a fresh crawl that seeds them as analyze tasks.
        crawlInactive={!active}
        onCancel={onCancel}
        cancelPending={cancelPending}
        onStartAnalysis={onStartAnalysis}
        startPending={startPending}
      />
    );
  } else {
    content = (
      <ScoredInventory
        crawl={crawl}
        active={active}
        onCancel={onCancel}
        cancelPending={cancelPending}
      />
    );
  }

  return (
    <div className="grid gap-2" data-testid="inventory-section">
      {content}
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
  onCancel,
  cancelPending,
}: Readonly<{
  crawl: SiteCrawl;
  active: boolean;
  onCancel: () => void;
  cancelPending: boolean;
}>) {
  const pager = useCursorStack();
  const inventoryQuery = useQuery({
    ...siteHealthQueries.inventory(crawl.id, { cursor: pager.cursor, limit: PAGE_LIMIT }),
    // Only the FIRST page polls — deeper cursor pages stay static so the rows
    // under review don't shift as new URLs are discovered.
    refetchInterval: active && pager.cursor === undefined ? POLL_INTERVAL_MS : false,
  });
  const rows = inventoryQuery.data?.items ?? [];
  const nextCursor = inventoryQuery.data?.next_cursor ?? null;

  let body: ReactNode;
  if (inventoryQuery.isError) {
    body = <Alert tone="danger">Could not load the page inventory. Please refresh.</Alert>;
  } else if (rows.length === 0) {
    body = (
      <p className="text-secondary text-sm">
        Discovering pages — sitemaps and internal links are being scanned.
      </p>
    );
  } else {
    body = (
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
    );
  }

  return (
    <Card>
      <CardContent className="grid gap-2">
        <div className="flex items-center justify-between gap-3">
          <Label>Pages discovered so far</Label>
          {active ? (
            <Button variant="destructive" size="sm" onClick={onCancel} disabled={cancelPending}>
              {cancelPending ? 'Cancelling…' : 'Cancel'}
            </Button>
          ) : null}
        </div>
        {body}
        <div className="flex flex-wrap items-center justify-between gap-3">
          {crawl.status === 'running' || crawl.status === 'queued' ? (
            <p className="text-muted text-xs">More URLs appear as discovery continues.</p>
          ) : (
            <span />
          )}
          {pager.canPrev || nextCursor ? (
            <div className="flex items-center gap-2">
              <CursorPager
                canPrev={pager.canPrev}
                canNext={Boolean(nextCursor)}
                onPrev={pager.pop}
                onNext={() => pager.push(nextCursor)}
              />
            </div>
          ) : null}
        </div>
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
 * The page browser (design mockup 713): three server-backed tabs, each its
 * OWN query with its own cursor stack — filtering is server-side, never a
 * filter over the current client page.
 *
 * Used for the WHOLE audit lifecycle: while analysis runs the first page of
 * the active tab polls, so each row's status badge advances queued → running
 * → completed and its scores fill in — the SAME rows, in place, until the run
 * settles. Finishing changes nothing structurally.
 */
function ScoredInventory({
  crawl,
  active,
  onCancel,
  cancelPending,
}: Readonly<{
  crawl: SiteCrawl;
  active: boolean;
  onCancel: () => void;
  cancelPending: boolean;
}>) {
  const [tab, setTab] = useState<TabKey>('monitored');
  // Per-tab cursor stack so Prev/Next walk keyset pages without offsets.
  const pagers = {
    monitored: useCursorStack(),
    all: useCursorStack(),
    errors: useCursorStack(),
  };
  const pager = pagers[tab];

  const activeTab = TABS.find((t) => t.key === tab)!;

  const pagesQuery = useQuery({
    ...siteHealthQueries.pages(crawl.id, {
      ...activeTab.params,
      cursor: pager.cursor,
      limit: PAGE_LIMIT,
    }),
    // While analysis is active only the FIRST page of a tab polls (polling
    // baseline) — deeper cursor pages stay static so rows under review don't
    // shift as more pages finish scoring.
    refetchInterval: active && pager.cursor === undefined ? POLL_INTERVAL_MS : false,
  });

  const rows = pagesQuery.data?.items ?? [];
  const nextCursor = pagesQuery.data?.next_cursor ?? null;

  let body: ReactNode;
  if (pagesQuery.isError) {
    body = (
      <div className="p-[var(--card-padding)]">
        <Alert tone="danger">Could not load pages for this view. Try again.</Alert>
      </div>
    );
  } else if (pagesQuery.isLoading) {
    body = (
      <div className="grid gap-2 p-[var(--card-padding)]">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
      </div>
    );
  } else if (rows.length === 0) {
    body = (
      <p className="text-secondary p-[var(--card-padding)] text-sm">
        {active ? 'Pages appear here as the audit reaches them.' : 'No pages in this view.'}
      </p>
    );
  } else {
    body = <PagesTable pages={rows} crawlId={crawl.id} />;
  }

  return (
    <Card>
      <CardContent className="grid gap-4 p-0">
        <div className="border-border-subtle flex flex-wrap items-center gap-2 border-b px-[var(--card-padding)] pt-[var(--card-padding)]">
          <div className="flex flex-wrap items-center gap-1">
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
          {active ? (
            <Button
              variant="destructive"
              size="sm"
              className="mb-1 ml-auto"
              onClick={onCancel}
              disabled={cancelPending}
            >
              {cancelPending ? 'Cancelling…' : 'Cancel'}
            </Button>
          ) : null}
        </div>

        {body}

        {pager.canPrev || nextCursor ? (
          <div className="border-border-subtle flex items-center justify-end gap-2 border-t px-[var(--card-padding)] py-3">
            <CursorPager
              canPrev={pager.canPrev}
              canNext={Boolean(nextCursor)}
              onPrev={pager.pop}
              onNext={() => pager.push(nextCursor)}
            />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
