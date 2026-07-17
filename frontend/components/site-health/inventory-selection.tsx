'use client';

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Label } from '@/components/ui/typography';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ApiError } from '@/lib/api/errors';
import { siteHealthApi, siteHealthMutations, siteHealthQueries } from '@/lib/api/site-health';
import { queryKeys } from '@/lib/api/query-keys';
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
  committedFromResponse,
  initStagedSelection,
  quotaStatus,
  rebaseOnServer,
  selectionDelta,
  setManyStaged,
  toggleStaged,
  toReplacePayload,
  type StagedSelection,
} from '@/lib/site-health/selection';

const PAGE_LIMIT = 25;

/**
 * Starter monitored-selection (Slice 7, mockup 709).
 *
 * A cursor-paginated inventory with search/status filters where the user stages
 * the persistent monitored set: staged ids persist across pages (never rebuilt
 * from the visible rows), the quota comes from the server entitlement, and
 * commit sends the FULL versioned set. A stale `selection_version` conflict is
 * recovered by refetching the server set, rebasing the user's edits onto it, and
 * asking them to resubmit (no silent overwrite).
 */
export function InventorySelection({
  crawl,
  entitlement,
  projectId,
}: Readonly<{
  crawl: SiteCrawl;
  entitlement: SiteHealthEntitlement;
  projectId: string;
}>) {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState<InventoryFilters>(emptyInventoryFilters);
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const [searchInput, setSearchInput] = useState('');
  const [selection, setSelection] = useState<StagedSelection | null>(null);
  const [staleNotice, setStaleNotice] = useState(false);

  const cursor = cursorStack.at(-1) ?? undefined;

  const monitoredQuery = useQuery(siteHealthQueries.monitored(projectId));

  const inventoryQuery = useQuery(
    siteHealthQueries.inventory(crawl.id, {
      ...toInventoryParams(filters, cursor, PAGE_LIMIT),
    }),
  );

  // Initialize the staging session once the committed set is known. The homepage
  // is staged by default ONLY when there is no committed set yet.
  const committed = monitoredQuery.data ? committedFromResponse(monitoredQuery.data) : null;
  const effectiveSelection = useMemo(() => {
    if (selection) return selection;
    if (committed) {
      const homepageId = inventoryQuery.data?.items.find(
        (row) => row.normalized_url === crawl.root_url,
      )?.site_url_id;
      return initStagedSelection(committed, homepageId);
    }
    return null;
  }, [selection, committed, inventoryQuery.data, crawl.root_url]);

  const rows = inventoryQuery.data?.items ?? [];
  const nextCursor = inventoryQuery.data?.next_cursor ?? null;
  const canPrev = cursorStack.length > 0;

  const delta = effectiveSelection ? selectionDelta(effectiveSelection) : null;
  const quota = effectiveSelection ? quotaStatus(effectiveSelection, entitlement) : null;
  const visibleIds = rows.map((row) => row.site_url_id);
  const allVisibleStaged = effectiveSelection ? allStaged(effectiveSelection, visibleIds) : false;

  const replaceMutation = useMutation({
    ...siteHealthMutations.replaceMonitoredUrls(),
    onSuccess: (response) => {
      queryClient.setQueryData(queryKeys.siteHealth.monitored(projectId), response);
      setSelection(initStagedSelection(committedFromResponse(response)));
      setStaleNotice(false);
    },
    onError: async (error) => {
      // Stale version → refetch the server set, rebase the user's edits, prompt
      // an explicit resubmit rather than silently discarding their changes.
      if (error instanceof ApiError && error.status === 409 && effectiveSelection) {
        const fresh = await siteHealthApi.getMonitoredUrls(projectId);
        queryClient.setQueryData(queryKeys.siteHealth.monitored(projectId), fresh);
        setSelection(rebaseOnServer(effectiveSelection, committedFromResponse(fresh)));
        setStaleNotice(true);
      }
    },
  });

  const applyFilters = (next: Partial<InventoryFilters>) => {
    const changed = changeInventoryFilters(filters, next);
    setFilters(changed.filters);
    setCursorStack([]);
  };

  const commit = () => {
    if (!effectiveSelection) return;
    replaceMutation.mutate({
      projectId,
      input: toReplacePayload(effectiveSelection),
    });
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

        {quota?.overLimit ? (
          <Alert tone="warning">
            You&apos;ve selected {quota.staged} pages — your plan allows {quota.limit}. Remove{' '}
            {quota.staged - quota.limit} to continue.
          </Alert>
        ) : null}
        {staleNotice ? (
          <Alert tone="info">
            The monitored set changed since you started. We merged your edits onto the latest
            version — review and resubmit.
          </Alert>
        ) : null}
        {replaceMutation.isError && !staleNotice ? (
          <Alert tone="danger">Could not save your selection. Please try again.</Alert>
        ) : null}

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10" />
              <TableHead>Page URL</TableHead>
              <TableHead>Type</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => {
              const checked = effectiveSelection?.staged.has(row.site_url_id) ?? false;
              return (
                <TableRow key={row.site_url_id}>
                  <TableCell>
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!effectiveSelection}
                      aria-label={`Monitor ${row.display_url}`}
                      onChange={() =>
                        effectiveSelection &&
                        setSelection(toggleStaged(effectiveSelection, row.site_url_id))
                      }
                    />
                  </TableCell>
                  <TableCell>
                    <span className="flex flex-col">
                      <span className="font-medium text-foreground">
                        {row.title ?? row.display_url}
                      </span>
                      <span className="mono text-2xs text-muted">{row.display_url}</span>
                    </span>
                  </TableCell>
                  <TableCell className="text-xs text-secondary">
                    {row.content_type ?? '—'}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle pt-4">
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setCursorStack((prev) => prev.slice(0, -1))}
              disabled={!canPrev}
            >
              Previous
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => nextCursor && setCursorStack((prev) => [...prev, nextCursor])}
              disabled={!nextCursor}
            >
              Next
            </Button>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted">
              Selections are saved and persist across re-crawls.
            </span>
            <Button
              size="sm"
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
                  ? commitCtaLabel(effectiveSelection, entitlement.monitored_url_limit)
                  : 'Analyze pages'}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
