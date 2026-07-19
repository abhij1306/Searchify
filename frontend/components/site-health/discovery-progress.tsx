'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { CursorPager } from '@/components/ui/cursor-pager';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Label, Metric } from '@/components/ui/typography';
import { siteHealthQueries } from '@/lib/api/site-health';
import type { SiteCrawl, SiteHealthEntitlement } from '@/lib/api/types';
import {
  canShowDiscoveredTotal,
  crawlBadgeValue,
  discoveryProgressLabel,
  isDiscoveryProvisional,
  statusLabel,
} from '@/lib/site-health/status';

const PAGE_LIMIT = 25;
const POLL_INTERVAL_MS = 4_000;

/**
 * Discovery-in-progress view (Slice 7, mockup 708).
 *
 * Shows live discovery progress for the active crawl: a status badge, the
 * "N pages discovered so far" / "N sample pages" copy (Free NEVER implies a
 * hidden total — no "so far", no placeholder total), a discovered-total card
 * only when the entitlement + crawl allow it, and a cursor-paginated list of
 * the URLs admitted to THIS crawl so far (the same keyset inventory the
 * selection screen pages — so a 5000+-URL discovery is fully browsable while
 * it runs, not just a fixed preview). Free stays read-only + sample-scoped
 * with an upgrade notice; a Cancel control is offered while the crawl is
 * still active.
 */
export function DiscoveryProgress({
  crawl,
  entitlement,
  active,
  onCancel,
  cancelPending,
}: Readonly<{
  crawl: SiteCrawl;
  entitlement: SiteHealthEntitlement;
  /** True while the crawl is non-terminal (keeps the inventory list polling). */
  active: boolean;
  onCancel: () => void;
  cancelPending: boolean;
}>) {
  const provisional = isDiscoveryProvisional(crawl);
  const showTotal = canShowDiscoveredTotal(entitlement, crawl);
  const isFree = entitlement.plan_key === 'free';

  // Cursor-paginated admitted-URL inventory (bounded to this crawl). The first
  // page keeps polling while discovery runs so new URLs stream in; deeper
  // pages stay stable under keyset (normalized_url, id) ordering.
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const cursor = cursorStack.at(-1) ?? undefined;
  const inventoryQuery = useQuery({
    ...siteHealthQueries.inventory(crawl.id, { cursor, limit: PAGE_LIMIT }),
    refetchInterval: active ? POLL_INTERVAL_MS : false,
  });
  const rows = inventoryQuery.data?.items ?? [];
  const nextCursor = inventoryQuery.data?.next_cursor ?? null;
  const canPrev = cursorStack.length > 0;

  return (
    <Card>
      <CardContent className="grid gap-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Badge variant="run-status" value={crawlBadgeValue(crawl.status)}>
              {statusLabel(crawl.status)}
            </Badge>
            <span className="text-sm text-secondary" aria-live="polite">
              {discoveryProgressLabel(crawl)}
              {provisional ? ' — scanning continues in the background' : ''}
            </span>
          </div>
          <Button variant="destructive" size="sm" onClick={onCancel} disabled={cancelPending}>
            {cancelPending ? 'Cancelling…' : 'Cancel'}
          </Button>
        </div>

        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <div className="grid gap-1">
            <Label>{isFree ? 'Sample URLs' : 'URLs found'}</Label>
            <Metric className="text-2xl">{crawl.visible_url_count}</Metric>
          </div>
          {showTotal && crawl.total_url_count !== null ? (
            <div className="grid gap-1">
              <Label>Total discovered</Label>
              <Metric className="text-2xl">{crawl.total_url_count}</Metric>
            </div>
          ) : null}
          <div className="grid gap-1">
            <Label>Discovery</Label>
            <span className="text-sm text-secondary">{statusLabel(crawl.discovery_status)}</span>
          </div>
        </dl>

        {isFree ? (
          <div className="rounded-md border border-warning-border bg-warning-bg p-3 text-sm text-warning-text">
            <p className="font-medium">
              Free plan — we&apos;ll automatically analyze a {entitlement.sample_url_limit}-page
              sample of your site.
            </p>
            <p className="mt-0.5 text-warning-text/90">
              Upgrade to Starter to choose which pages to monitor.
            </p>
          </div>
        ) : null}

        <div className="grid gap-2">
          <Label>Pages discovered so far</Label>
          {inventoryQuery.isError ? (
            <Alert tone="danger">Could not load the page inventory. Please refresh.</Alert>
          ) : rows.length === 0 ? (
            <p className="text-sm text-secondary">
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
                      <span className="mono text-xs text-foreground">{row.display_url}</span>
                    </TableCell>
                    <TableCell className="text-xs text-secondary">
                      {row.source ? statusLabel(row.source) : '—'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            {crawl.status === 'running' || crawl.status === 'queued' ? (
              <p className="text-xs text-muted">More URLs appear as discovery continues.</p>
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
        </div>
      </CardContent>
    </Card>
  );
}
