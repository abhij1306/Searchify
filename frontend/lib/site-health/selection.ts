/**
 * Staged monitored-selection logic (Site Health, Task 2) — PURE.
 *
 * The monitored set is a **project-level persistent full set**; the server is
 * the sole authority (atomic full-set replacement keyed by
 * `expected_selection_version`). This module models only the client-side
 * *staging* the user does before committing, so the selection screen (Task 7)
 * stays presentational and every rule here is unit-testable:
 *
 *   - the staged set is the complete monitored set to submit, persisted across
 *     cursor pages (it is NOT rebuilt from the currently-visible rows);
 *   - the homepage (crawl root) is staged initially ONLY when no committed set
 *     exists yet — never when a committed set is present;
 *   - the delta vs the committed set (additions / removals) drives the CTA;
 *   - the quota limit comes from the entitlement's `monitored_url_limit`
 *     (never a hard-coded 50) so over-limit feedback is immediate;
 *   - a stale `selection_version` is recovered by rebasing onto the server's
 *     newly-observed committed set;
 *   - the client claims NO authority: it only produces the payload
 *     (`site_url_ids` + `expected_selection_version`) for the server to apply.
 */
import type { MonitoredUrl, MonitoredUrlsResponse, SiteHealthEntitlement } from '@/lib/api/types';

/** The committed server state a staging session is based on. */
export type CommittedSelection = {
  /** Active monitored site-url ids (the persistent set). */
  readonly siteUrlIds: ReadonlySet<string>;
  /** The `selection_version` the staged set is optimistically based on. */
  readonly version: number;
};

/** A staging session: the committed baseline plus the user's staged full set. */
export type StagedSelection = {
  readonly committed: CommittedSelection;
  /** The complete set the user intends to submit (persists across pages). */
  readonly staged: ReadonlySet<string>;
};

/** Additions/removals of the staged set relative to the committed baseline. */
export type SelectionDelta = {
  readonly added: readonly string[];
  readonly removed: readonly string[];
  /** True when the staged set differs from the committed set. */
  readonly dirty: boolean;
};

/** Immediate quota feedback derived from the entitlement limit. */
export type QuotaStatus = {
  /** Distinct URLs the staged set would make active workspace-wide-locally. */
  readonly staged: number;
  /** Entitlement `monitored_url_limit` — the ONLY limit authority. */
  readonly limit: number;
  readonly remaining: number;
  readonly overLimit: boolean;
};

/** Build a `CommittedSelection` from the monitored-urls response. */
export function committedFromResponse(response: MonitoredUrlsResponse): CommittedSelection {
  const siteUrlIds = new Set(
    response.monitored_urls.filter((row) => row.active).map((row) => row.site_url_id),
  );
  return { siteUrlIds, version: response.selection_version };
}

/**
 * Initialize a staging session. The staged set starts as a copy of the
 * committed set; when this is the FIRST commit ever (`version === 0` — the
 * server-side `selection_version` starts at 0 and is bumped on every replace,
 * even one that commits an empty set) and a `homepageId` is given, the
 * homepage is staged by default (first-use convenience). Once the user has
 * committed at least once — including an intentional empty set — the
 * `version` has advanced past 0, so an empty committed set on reload is never
 * mistaken for "never used" and re-staged.
 */
export function initStagedSelection(
  committed: CommittedSelection,
  homepageId?: string | null,
): StagedSelection {
  const staged = new Set(committed.siteUrlIds);
  if (committed.version === 0 && homepageId) {
    staged.add(homepageId);
  }
  return { committed, staged };
}

/** Toggle a single row in the staged set (persists across cursor pages). */
export function toggleStaged(selection: StagedSelection, siteUrlId: string): StagedSelection {
  const staged = new Set(selection.staged);
  if (staged.has(siteUrlId)) staged.delete(siteUrlId);
  else staged.add(siteUrlId);
  return { ...selection, staged };
}

/** Explicitly set a row's staged state (for bulk / row checkbox controls). */
export function setStaged(
  selection: StagedSelection,
  siteUrlId: string,
  monitored: boolean,
): StagedSelection {
  const staged = new Set(selection.staged);
  if (monitored) staged.add(siteUrlId);
  else staged.delete(siteUrlId);
  return { ...selection, staged };
}

/** Stage/unstage a batch of ids at once (bulk select on the visible page). */
export function setManyStaged(
  selection: StagedSelection,
  siteUrlIds: readonly string[],
  monitored: boolean,
): StagedSelection {
  const staged = new Set(selection.staged);
  for (const id of siteUrlIds) {
    if (monitored) staged.add(id);
    else staged.delete(id);
  }
  return { ...selection, staged };
}

/** Discard staged edits, returning to the committed baseline. */
export function resetStaged(selection: StagedSelection): StagedSelection {
  return { ...selection, staged: new Set(selection.committed.siteUrlIds) };
}

/** Is a given row currently staged (checked)? */
export function isStaged(selection: StagedSelection, siteUrlId: string): boolean {
  return selection.staged.has(siteUrlId);
}

/** Additions/removals vs the committed set (sorted for stable display). */
export function selectionDelta(selection: StagedSelection): SelectionDelta {
  const added: string[] = [];
  const removed: string[] = [];
  for (const id of selection.staged) {
    if (!selection.committed.siteUrlIds.has(id)) added.push(id);
  }
  for (const id of selection.committed.siteUrlIds) {
    if (!selection.staged.has(id)) removed.push(id);
  }
  added.sort();
  removed.sort();
  return { added, removed, dirty: added.length > 0 || removed.length > 0 };
}

/**
 * Quota feedback using the entitlement limit (never a hard-coded 50). `used`
 * from other projects is added so the workspace-wide count is reflected; the
 * server is still the authority, this is only immediate local feedback.
 */
export function quotaStatus(
  selection: StagedSelection,
  entitlement: Pick<SiteHealthEntitlement, 'monitored_url_limit'>,
  usedElsewhere = 0,
): QuotaStatus {
  const stagedCount = selection.staged.size + usedElsewhere;
  const limit = entitlement.monitored_url_limit;
  return {
    staged: stagedCount,
    limit,
    remaining: limit - stagedCount,
    overLimit: stagedCount > limit,
  };
}

/**
 * The payload the server applies. `expected_selection_version` is the committed
 * version this staging session is based on — the server rejects it with
 * `stale_selection_version` if it has advanced. The client asserts nothing.
 */
export function toReplacePayload(selection: StagedSelection): {
  site_url_ids: string[];
  expected_selection_version: number;
} {
  return {
    site_url_ids: Array.from(selection.staged).sort(),
    expected_selection_version: selection.committed.version,
  };
}

/**
 * Recover from a stale-version conflict: rebase the user's *intent* onto the
 * server's freshly-observed committed set. The staged additions/removals the
 * user made are re-applied on top of the new baseline so their in-progress
 * edits survive the refetch, and the version is updated so a resubmit is valid.
 */
export function rebaseOnServer(
  selection: StagedSelection,
  server: CommittedSelection,
): StagedSelection {
  const delta = selectionDelta(selection);
  const staged = new Set(server.siteUrlIds);
  for (const id of delta.added) staged.add(id);
  for (const id of delta.removed) staged.delete(id);
  return { committed: server, staged };
}

/** Are all of `siteUrlIds` staged (for a page "select all" tri-state)? */
export function allStaged(selection: StagedSelection, siteUrlIds: readonly string[]): boolean {
  return siteUrlIds.length > 0 && siteUrlIds.every((id) => selection.staged.has(id));
}

/** Human-readable "Analyze N of LIMIT pages" CTA label. */
export function commitCtaLabel(selection: StagedSelection, limit: number): string {
  return `Analyze ${selection.staged.size} of ${limit} pages`;
}

/** Committed monitored ids that no longer appear in the inventory (missing). */
export function missingMonitored(
  response: MonitoredUrlsResponse,
  inventoryIds: ReadonlySet<string>,
): MonitoredUrl[] {
  return response.monitored_urls.filter(
    (row) => row.active && !inventoryIds.has(row.site_url_id),
  );
}
