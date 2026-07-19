'use client';

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { CursorPager } from '@/components/ui/cursor-pager';
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
  const [bulkCount, setBulkCount] = useState('');
  // "Clear all" is destructive (wipes the committed selection immediately),
  // so it requires a second explicit click to confirm.
  const [confirmClear, setConfirmClear] = useState(false);

  const cursor = cursorStack.at(-1) ?? undefined;

  const monitoredQuery = useQuery(siteHealthQueries.monitored(projectId));

  const inventoryQuery = useQuery(
    siteHealthQueries.inventory(crawl.id, {
      ...toInventoryParams(filters, cursor, PAGE_LIMIT),
    }),
  );

  // Initialize the staging session once the committed set is known. The homepage
  // is staged by default ONLY when there is no committed set yet. Once computed,
  // the result is persisted into `selection` state (not just memoized) so that
  // navigating away from the page containing the root URL never drops the
  // default homepage selection — the staged set thereafter only changes via
  // explicit user edits, never an implicit re-derivation from the visible page.
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

  useEffect(() => {
    if (!selection && effectiveSelection) {
      // One-time promotion of the derived initial selection into state (see
      // comment above) — intentionally not a render-time derivation.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSelection(effectiveSelection);
    }
    // Only run when the derived initial selection first becomes available;
    // `selection` itself is excluded so this effect does not re-fire once set.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveSelection]);

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

  // Server-resolved bulk selection (first N / all / clear). Unlike the staged
  // flow, this COMMITS immediately: the server resolves the candidate ids in
  // the inventory's deterministic order and replaces the monitored set in one
  // atomic call — no shipping thousands of ids through the client.
  const bulkSelectMutation = useMutation({
    ...siteHealthMutations.bulkSelectMonitoredUrls(),
    onSuccess: (response) => {
      queryClient.setQueryData(queryKeys.siteHealth.monitored(projectId), response);
      setSelection(initStagedSelection(committedFromResponse(response)));
      setStaleNotice(false);
    },
    onError: async (error) => {
      // Stale version → adopt the fresh server baseline; the user just retries
      // the bulk action (their intent is the mode/count, not per-row edits).
      if (error instanceof ApiError && error.status === 409) {
        const fresh = await siteHealthApi.getMonitoredUrls(projectId);
        queryClient.setQueryData(queryKeys.siteHealth.monitored(projectId), fresh);
        setSelection(initStagedSelection(committedFromResponse(fresh)));
        setStaleNotice(true);
      }
    },
  });

  const bulkSelect = (mode: 'first_n' | 'all' | 'none', count?: number) => {
    if (!effectiveSelection) return;
    bulkSelectMutation.mutate({
      projectId,
      input: {
        mode,
        crawl_id: crawl.id,
        count,
        // Bulk selection respects the active inventory search filter so
        // "select first N" matches exactly what the filtered list shows.
        query: filters.query.trim() || undefined,
        expected_selection_version: effectiveSelection.committed.version,
      },
    });
  };

  const parsedBulkCount = (() => {
    const n = Number.parseInt(bulkCount, 10);
    return Number.isFinite(n) && n > 0 ? n : null;
  })();

  // Surface the server's quota message ("N of LIMIT") on a 403; generic text
  // otherwise. The body is the raw JSON error payload from the transport.
  const bulkSelectError = (() => {
    const error = bulkSelectMutation.error;
    if (!(error instanceof ApiError)) return null;
    try {
      const detail = (JSON.parse(error.body) as { detail?: { code?: string; limit?: number; currently_used?: number } }).detail;
      if (detail?.code === 'site_health_quota_exceeded') {
        return `That selection exceeds your monitored-URL limit (${detail.limit}). Try "Select first ${detail.limit}" instead.`;
      }
    } catch {
      // Non-JSON body — fall through to the generic message.
    }
    return null;
  })();

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

        {/* Quick-select: server-resolved bulk selection. The ids are resolved
            on the server in the inventory's deterministic order, so "first N"
            always matches the first N rows shown here (under the same search
            filter). Applies immediately — no separate commit step. */}
        {effectiveSelection && entitlement.access_mode === 'selection' ? (
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-border-subtle bg-background-alt px-3 py-2">
            <span className="text-xs font-medium text-secondary">Quick select</span>
            <Input
              type="number"
              min={1}
              max={entitlement.monitored_url_limit}
              value={bulkCount}
              onChange={(event) => setBulkCount(event.target.value)}
              className="w-24"
              aria-label="Number of pages to select"
            />
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={bulkSelectMutation.isPending || !parsedBulkCount}
              onClick={() => parsedBulkCount && bulkSelect('first_n', parsedBulkCount)}
            >
              Select first {parsedBulkCount ?? 'N'}
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={bulkSelectMutation.isPending}
              onClick={() => bulkSelect('all')}
            >
              Select all
            </Button>
            {confirmClear ? (
              <>
                <span className="text-xs text-secondary">Deselect every page?</span>
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  disabled={bulkSelectMutation.isPending}
                  onClick={() => {
                    setConfirmClear(false);
                    bulkSelect('none');
                  }}
                >
                  Confirm clear
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setConfirmClear(false)}
                >
                  Cancel
                </Button>
              </>
            ) : (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={bulkSelectMutation.isPending}
                onClick={() => setConfirmClear(true)}
              >
                Clear all
              </Button>
            )}
            {bulkSelectMutation.isPending ? (
              <span className="text-xs text-muted">Applying…</span>
            ) : null}
          </div>
        ) : null}
        {bulkSelectMutation.isError && !staleNotice ? (
          <Alert tone="danger">
            {bulkSelectError ?? 'Could not apply the bulk selection. Please try again.'}
          </Alert>
        ) : null}

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
