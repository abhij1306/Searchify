'use client';

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { ApiError } from '@/lib/api/errors';
import { siteHealthApi, siteHealthMutations, siteHealthQueries } from '@/lib/api/site-health';
import { queryKeys } from '@/lib/api/query-keys';
import type { SiteCrawl, SiteHealthEntitlement } from '@/lib/api/types';
import {
  committedFromResponse,
  initStagedSelection,
  quotaStatus,
  rebaseOnServer,
  selectionDelta,
  toReplacePayload,
  type StagedSelection,
} from '@/lib/site-health/selection';

export type BulkSelectMode = 'first_n' | 'all' | 'none';

/**
 * Staged monitored-selection state + commit/bulk mutations (Slice 7).
 *
 * Owns the staging session: staged ids persist across pages (never rebuilt
 * from the visible rows), the quota comes from the server entitlement, and
 * commit sends the FULL versioned set. A stale `selection_version` conflict is
 * recovered by refetching the server set, rebasing the user's edits onto it
 * (per-row edits) or adopting the fresh baseline (bulk actions), and asking
 * them to resubmit (no silent overwrite).
 */
export function useMonitoredSelection({
  crawl,
  entitlement,
  projectId,
  homepageId,
  inventoryReady,
  searchQuery,
}: Readonly<{
  crawl: SiteCrawl;
  entitlement: SiteHealthEntitlement;
  projectId: string;
  /** The inventory row id of the root URL, once visible (default staging). */
  homepageId: string | undefined;
  /**
   * True once the inventory query has settled. The staging session must not
   * initialize before this: the monitored and inventory queries race, and if
   * the committed set arrives first, `homepageId` would still be undefined
   * when the initial selection is promoted into state — silently dropping the
   * first-use homepage default.
   */
  inventoryReady: boolean;
  /** Active inventory search filter — bulk selection is scoped to it. */
  searchQuery: string;
}>) {
  const queryClient = useQueryClient();
  const [selection, setSelection] = useState<StagedSelection | null>(null);
  const [staleNotice, setStaleNotice] = useState(false);

  const monitoredQuery = useQuery(siteHealthQueries.monitored(projectId));

  // Initialize the staging session once the committed set is known AND the
  // inventory has settled (so the homepageId lookup is final). The homepage is
  // staged by default ONLY when there is no committed set yet. Once computed,
  // the result is persisted into `selection` state (not just memoized) so that
  // navigating away from the page containing the root URL never drops the
  // default homepage selection — the staged set thereafter only changes via
  // explicit user edits, never an implicit re-derivation from the visible page.
  const committed = monitoredQuery.data ? committedFromResponse(monitoredQuery.data) : null;
  const effectiveSelection = useMemo(() => {
    if (selection) return selection;
    if (committed && inventoryReady) return initStagedSelection(committed, homepageId);
    return null;
  }, [selection, committed, inventoryReady, homepageId]);

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

  const adoptServerSet = (response: Parameters<typeof committedFromResponse>[0]) => {
    queryClient.setQueryData(queryKeys.siteHealth.monitored(projectId), response);
    setSelection(initStagedSelection(committedFromResponse(response)));
    setStaleNotice(false);
  };

  const replaceMutation = useMutation({
    ...siteHealthMutations.replaceMonitoredUrls(),
    onSuccess: adoptServerSet,
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
    onSuccess: adoptServerSet,
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

  const bulkSelect = (mode: BulkSelectMode, count?: number) => {
    if (!effectiveSelection) return;
    bulkSelectMutation.mutate({
      projectId,
      input: {
        mode,
        crawl_id: crawl.id,
        count,
        // Bulk selection respects the active inventory search filter so
        // "select first N" matches exactly what the filtered list shows.
        query: searchQuery.trim() || undefined,
        expected_selection_version: effectiveSelection.committed.version,
      },
    });
  };

  // Surface the server's quota message ("N of LIMIT") on a 403; generic text
  // otherwise. The body is the raw JSON error payload from the transport.
  const bulkSelectError = useMemo(() => {
    const error = bulkSelectMutation.error;
    if (!(error instanceof ApiError)) return null;
    try {
      const detail = (
        JSON.parse(error.body) as {
          detail?: { code?: string; limit?: number; currently_used?: number };
        }
      ).detail;
      if (detail?.code === 'site_health_quota_exceeded') {
        return `That selection exceeds your monitored-URL limit (${detail.limit}). Try "Select first ${detail.limit}" instead.`;
      }
    } catch {
      // Non-JSON body — fall through to the generic message.
    }
    return null;
  }, [bulkSelectMutation.error]);

  const commit = () => {
    if (!effectiveSelection) return;
    replaceMutation.mutate({
      projectId,
      input: toReplacePayload(effectiveSelection),
    });
  };

  const delta = effectiveSelection ? selectionDelta(effectiveSelection) : null;
  const quota = effectiveSelection ? quotaStatus(effectiveSelection, entitlement) : null;

  return {
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
  };
}
