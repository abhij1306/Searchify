# Site Health monitored-set lifecycle domain logic (Task 4).
#
# Owns the atomic, versioned full-set replacement of a project's monitored
# selection, the recrawl seeding of active monitored URLs, and the PURE
# worker-side guard functions Task 5 wires into the Site Health worker.
#
# The monitored set is a persistent, project-level projection
# (``MonitoredSiteUrl``) whose active rows are counted WORKSPACE-WIDE against
# the entitlement's ``monitored_url_limit`` (Starter = 50). Every active row is
# counted regardless of ``selection_source`` (``user`` | ``free_sample``).
#
# Concurrency contract (subplan Acceptance criteria 2): two simultaneous
# selection updates — even across different projects in the same workspace —
# cannot push the workspace above the limit. This is serialized by locking the
# single ``WorkspaceSiteHealthEntitlement`` row ``FOR UPDATE`` before counting,
# so the two updaters are ordered and each sees the other's committed rows.
#
# Nothing here is ever deleted on downgrade: rows are DEACTIVATED (``active``
# flipped, ``deselected_at`` stamped) so evidence/history survives capability
# changes (plan §4). Re-adding a removed URL in the same crawl allocates the
# NEXT ``generation`` so it never collides with the cancelled task's slot.
from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeGuard

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.site_health import (
    CODE_QUOTA_EXCEEDED,
    CODE_STALE_SELECTION_VERSION,
    CODE_STARTER_REQUIRED,
    CRAWL_ACTIVE_STATUSES,
    INITIAL_TASK_GENERATION,
    SELECTION_SOURCE_USER,
    TASK_KIND_ANALYZE,
)
from app.core.config.task_queue import (
    TASK_CLAIMABLE_STATUSES,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_LEASED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
)
from app.domain.site_health.entitlements import (
    entitlement_allows_monitored_analysis,
    lock_entitlement,
    resolve_entitlement,
)
from app.models.project import Project
from app.models.site_health import (
    MonitoredSiteUrl,
    SiteCrawl,
    SiteCrawlTask,
    SiteHealthProfile,
    SiteUrl,
    SiteUrlObservation,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# =========================================================================
# Coded errors (match the Task 2 frontend contract)
# =========================================================================
class SelectionError(Exception):
    """Base class for a monitored-selection failure carrying a stable code."""

    code: str = ""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class StarterRequiredError(SelectionError):
    """Free workspace attempted a user-managed selection mutation (403)."""

    code = CODE_STARTER_REQUIRED


class StaleSelectionVersionError(SelectionError):
    """``expected_selection_version`` did not match the current version (409)."""

    code = CODE_STALE_SELECTION_VERSION

    def __init__(self, message: str, *, current_version: int) -> None:
        super().__init__(message)
        self.current_version = current_version


class QuotaExceededError(SelectionError):
    """A valid Starter selection would exceed the workspace limit (403).

    Carries the workspace ``limit`` and the currently-used active count so the
    API/UI can render "N of 50" feedback. Never exposes other projects' URLs.
    """

    code = CODE_QUOTA_EXCEEDED

    def __init__(self, message: str, *, limit: int, currently_used: int) -> None:
        super().__init__(message)
        self.limit = limit
        self.currently_used = currently_used


class SelectionValidationError(SelectionError):
    """A requested id is foreign / not a discovered project URL (422)."""

    code = "invalid_selection"


class RerunNotAllowedError(SelectionError):
    """A rerun was requested for a URL not (still) monitored / no active crawl."""

    code = "rerun_not_allowed"


@dataclass(frozen=True)
class SelectionResult:
    """The outcome of a monitored-set replacement (projection-only)."""

    selection_version: int
    active_ids: tuple[uuid.UUID, ...]
    added_ids: tuple[uuid.UUID, ...]
    removed_ids: tuple[uuid.UUID, ...]
    workspace_used: int
    enqueued_task_ids: tuple[uuid.UUID, ...]
    cancelled_task_ids: tuple[uuid.UUID, ...]


# =========================================================================
# Loaders / helpers
# =========================================================================
async def _lock_project(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> Project | None:
    """Load + lock the project row ``FOR UPDATE``.

    This is the SAME lock ``create_crawl`` takes first (before the entitlement
    and profile). Taking it here serializes a terminal-page rerun against a
    concurrent full-crawl creation for the same project, so the active-crawl
    check below cannot race past ``create_crawl``'s and mint a second active
    crawl. The global lock order is ``project -> entitlement -> profile``.
    """
    result = await session.execute(
        select(Project)
        .where(
            Project.id == project_id,
            Project.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def _lock_profile(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> SiteHealthProfile | None:
    """Load + lock the project's Site Health profile ``FOR UPDATE``."""
    result = await session.execute(
        select(SiteHealthProfile)
        .where(
            SiteHealthProfile.workspace_id == workspace_id,
            SiteHealthProfile.project_id == project_id,
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def _load_project_site_urls(
    session: AsyncSession, *, project_id: uuid.UUID, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, SiteUrl]:
    """Load the ``SiteUrl`` rows for ``ids`` that belong to the project."""
    id_list = list(dict.fromkeys(ids))
    if not id_list:
        return {}
    result = await session.execute(
        select(SiteUrl).where(
            SiteUrl.project_id == project_id,
            SiteUrl.id.in_(id_list),
        )
    )
    return {row.id: row for row in result.scalars().all()}


async def _active_count_other_projects(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> int:
    """Count ACTIVE monitored rows in the workspace OUTSIDE this project.

    Counts every active row regardless of ``selection_source`` (plan §4: quota
    usage counts every active monitored row). Called while holding the
    entitlement lock so the value reflects other updaters' committed state.
    """
    result = await session.execute(
        select(func.count())
        .select_from(MonitoredSiteUrl)
        .where(
            MonitoredSiteUrl.workspace_id == workspace_id,
            MonitoredSiteUrl.project_id != project_id,
            MonitoredSiteUrl.active.is_(True),
        )
    )
    return int(result.scalar_one())


async def _active_count_in_project(
    session: AsyncSession, *, project_id: uuid.UUID
) -> int:
    """Count ACTIVE monitored rows currently in this project."""
    result = await session.execute(
        select(func.count())
        .select_from(MonitoredSiteUrl)
        .where(
            MonitoredSiteUrl.project_id == project_id,
            MonitoredSiteUrl.active.is_(True),
        )
    )
    return int(result.scalar_one())


async def _load_project_memberships(
    session: AsyncSession, *, project_id: uuid.UUID
) -> list[MonitoredSiteUrl]:
    """Load + lock every monitored membership row for the project."""
    result = await session.execute(
        select(MonitoredSiteUrl)
        .where(MonitoredSiteUrl.project_id == project_id)
        .with_for_update()
    )
    return list(result.scalars().all())


async def _active_crawl(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> SiteCrawl | None:
    """Return the project's current active crawl, if any (most recent)."""
    result = await session.execute(
        select(SiteCrawl)
        .where(
            SiteCrawl.workspace_id == workspace_id,
            SiteCrawl.project_id == project_id,
            SiteCrawl.status.in_(list(CRAWL_ACTIVE_STATUSES)),
        )
        .order_by(SiteCrawl.created_at.desc())
    )
    return result.scalars().first()


async def _next_generations(
    session: AsyncSession,
    *,
    crawl_id: uuid.UUID,
    task_kind: str,
    url_hashes: Sequence[str],
) -> dict[str, int]:
    """Next ``generation`` per url_hash for a task kind within a crawl.

    A URL analyzed once (or removed+cancelled) already owns generation(s) in
    the crawl; re-adding it must allocate ``max(existing) + 1`` so the unique
    ``(crawl_id, task_kind, url_hash, generation)`` slot never collides with a
    cancelled task. A URL never seen in this crawl starts at generation 0.
    """
    wanted = set(url_hashes)
    if not wanted:
        return {}
    result = await session.execute(
        select(
            SiteCrawlTask.url_hash,
            func.max(SiteCrawlTask.generation),
        )
        .where(
            SiteCrawlTask.crawl_id == crawl_id,
            SiteCrawlTask.task_kind == task_kind,
            SiteCrawlTask.url_hash.in_(list(wanted)),
        )
        .group_by(SiteCrawlTask.url_hash)
    )
    max_by_hash = {row[0]: int(row[1]) for row in result.all()}
    return {
        url_hash: (max_by_hash[url_hash] + 1)
        if url_hash in max_by_hash
        else INITIAL_TASK_GENERATION
        for url_hash in wanted
    }


def _analyze_idempotency_key(
    *, crawl_id: uuid.UUID, url_hash: str, generation: int
) -> str:
    return f"{crawl_id}:{TASK_KIND_ANALYZE}:{url_hash}:{generation}"


async def _enqueue_analyze_task(
    session: AsyncSession,
    *,
    crawl: SiteCrawl,
    site_url: SiteUrl,
    generation: int,
    position: int,
) -> SiteCrawlTask:
    """Create one queued ``analyze`` task for a newly monitored URL."""
    task = SiteCrawlTask(
        crawl_id=crawl.id,
        workspace_id=crawl.workspace_id,
        site_url_id=site_url.id,
        task_kind=TASK_KIND_ANALYZE,
        requested_url=site_url.normalized_url,
        url_hash=site_url.url_hash,
        generation=generation,
        randomized_position=position,
        idempotency_key=_analyze_idempotency_key(
            crawl_id=crawl.id,
            url_hash=site_url.url_hash,
            generation=generation,
        ),
        status=TASK_STATUS_QUEUED,
        available_at=_utcnow(),
    )
    session.add(task)
    return task


async def _cancel_pending_analyze_tasks(
    session: AsyncSession,
    *,
    crawl_id: uuid.UUID,
    url_hashes: Sequence[str],
) -> list[uuid.UUID]:
    """Cancel ONLY queued/retry ``analyze`` tasks for removed URLs.

    A running/leased task is NOT cancelled here — the worker's own guard
    (``evaluate_task_guard``) discards its result cooperatively before I/O and
    before persistence. Succeeded/failed tasks keep their immutable evidence.
    """
    hashes = list(dict.fromkeys(url_hashes))
    if not hashes:
        return []
    now = _utcnow()
    result = await session.execute(
        update(SiteCrawlTask)
        .where(
            SiteCrawlTask.crawl_id == crawl_id,
            SiteCrawlTask.task_kind == TASK_KIND_ANALYZE,
            SiteCrawlTask.url_hash.in_(hashes),
            SiteCrawlTask.status.in_(list(TASK_CLAIMABLE_STATUSES)),
        )
        .values(
            status=TASK_STATUS_CANCELLED,
            lease_owner=None,
            lease_expires_at=None,
            completed_at=now,
            error_code="cancelled",
        )
        .returning(SiteCrawlTask.id)
    )
    return [row[0] for row in result.all()]


# =========================================================================
# Atomic full-set replacement
# =========================================================================
async def replace_monitored_set(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    site_url_ids: Sequence[uuid.UUID],
    expected_selection_version: int,
) -> SelectionResult:
    """Atomically replace the project's user-managed monitored set.

    In one locked transaction (subplan Users & flows step 3):

    1. Lock the workspace entitlement ``FOR UPDATE`` (serializes the
       workspace-wide quota across concurrent updates in ANY project).
    2. Reject the mutation for a capability that disallows user selection
       (Free) with ``starter_required``.
    3. Lock the project profile and reject a stale
       ``expected_selection_version`` with ``stale_selection_version``.
    4. Validate every requested id is a discovered URL in this project.
    5. Enforce the workspace-wide active limit counting every active row
       regardless of source; over-limit raises ``site_health_quota_exceeded``.
    6. Apply the full-set delta: activate/convert requested rows to
       user-managed (this is the Free->Starter sample conversion), deactivate
       omitted active rows (never delete — evidence survives), bump the
       version.
    7. Enqueue ``analyze`` tasks for additions into the active crawl (next
       generation) and cancel only queued/retry analyze tasks for removals.

    The caller owns the surrounding transaction boundary; this function flushes
    but does not commit, so the API layer can wrap it.
    """
    entitlement = await lock_entitlement(session, workspace_id)
    profile = await _lock_profile(
        session, workspace_id=workspace_id, project_id=project_id
    )
    if profile is None:
        raise SelectionValidationError("Site Health profile not found")

    # Capability gate: Free may not mutate a user-managed selection.
    if not entitlement_allows_monitored_analysis(
        entitlement, selection_source=SELECTION_SOURCE_USER
    ):
        raise StarterRequiredError(
            "Starter capability is required to select monitored URLs"
        )

    # Optimistic concurrency on the persistent selection version.
    if expected_selection_version != profile.selection_version:
        raise StaleSelectionVersionError(
            "The monitored selection changed since it was loaded",
            current_version=profile.selection_version,
        )

    requested = list(dict.fromkeys(site_url_ids))
    site_urls = await _load_project_site_urls(
        session, project_id=project_id, ids=requested
    )
    unknown = [rid for rid in requested if rid not in site_urls]
    if unknown:
        raise SelectionValidationError(
            "Selection contains ids that are not discovered project URLs"
        )

    # Workspace-wide quota: every active row outside this project + the full
    # requested set for this project (a full-set replacement). Counted under
    # the entitlement lock so concurrent updaters are serialized.
    other_active = await _active_count_other_projects(
        session, workspace_id=workspace_id, project_id=project_id
    )
    limit = int(entitlement.monitored_url_limit)
    requested_set = set(requested)
    new_workspace_total = other_active + len(requested_set)
    if new_workspace_total > limit:
        # The quota-check's ``currently_used`` reports the true workspace-
        # wide count of active rows (including this project's pre-existing
        # active memberships), not just the "other projects" count used for
        # the limit arithmetic above.
        current_project_active = await _active_count_in_project(
            session, project_id=project_id
        )
        raise QuotaExceededError(
            "The selection would exceed the workspace monitored-URL limit",
            limit=limit,
            currently_used=other_active + current_project_active,
        )

    # Apply the full-set delta against the project's memberships (locked).
    memberships = await _load_project_memberships(session, project_id=project_id)
    by_url_id = {m.site_url_id: m for m in memberships}
    now = _utcnow()
    new_version = profile.selection_version + 1

    added_ids: list[uuid.UUID] = []
    removed_ids: list[uuid.UUID] = []

    # Deactivate previously-active rows omitted from the submitted set. This is
    # both a user removal AND the Free->Starter deactivation of omitted sample
    # rows — the row is preserved (never deleted) so evidence survives.
    for membership in memberships:
        if membership.active and membership.site_url_id not in requested_set:
            membership.active = False
            membership.deselected_at = now
            removed_ids.append(membership.site_url_id)

    # Activate / (re)activate / convert every requested row to user-managed.
    # Converting a ``free_sample`` row to ``user`` is the first-Starter
    # reconciliation done in this same locked transaction.
    for rid in requested:
        existing = by_url_id.get(rid)
        if existing is None:
            membership = MonitoredSiteUrl(
                workspace_id=workspace_id,
                project_id=project_id,
                profile_id=profile.id,
                site_url_id=rid,
                active=True,
                selection_source=SELECTION_SOURCE_USER,
                selecting_membership_id=new_version,
                selected_at=now,
            )
            session.add(membership)
            by_url_id[rid] = membership
            added_ids.append(rid)
        else:
            was_active = existing.active
            existing.active = True
            existing.selection_source = SELECTION_SOURCE_USER
            existing.deselected_at = None
            if not was_active:
                existing.selected_at = now
                existing.selecting_membership_id = new_version
                added_ids.append(rid)

    profile.selection_version = new_version
    await session.flush()

    # Active-crawl side effects: enqueue additions (next generation), cancel
    # only pending removals. If there is no active crawl, the selection still
    # persists — later crawls seed it via ``seed_monitored_targets``.
    enqueued_task_ids: list[uuid.UUID] = []
    cancelled_task_ids: list[uuid.UUID] = []
    crawl = await _active_crawl(
        session, workspace_id=workspace_id, project_id=project_id
    )
    if crawl is not None:
        if removed_ids:
            removed_hashes = [
                site_urls[rid].url_hash for rid in removed_ids if rid in site_urls
            ]
            # A removed row may not be in ``site_urls`` (it was not requested),
            # so resolve its hash from its membership's SiteUrl if needed.
            missing = [rid for rid in removed_ids if rid not in site_urls]
            if missing:
                extra = await _load_project_site_urls(
                    session, project_id=project_id, ids=missing
                )
                removed_hashes.extend(row.url_hash for row in extra.values())
            cancelled_task_ids = await _cancel_pending_analyze_tasks(
                session, crawl_id=crawl.id, url_hashes=removed_hashes
            )
        if added_ids:
            add_hashes = [site_urls[rid].url_hash for rid in added_ids]
            generations = await _next_generations(
                session,
                crawl_id=crawl.id,
                task_kind=TASK_KIND_ANALYZE,
                url_hashes=add_hashes,
            )
            for position, rid in enumerate(added_ids):
                site_url = site_urls[rid]
                task = await _enqueue_analyze_task(
                    session,
                    crawl=crawl,
                    site_url=site_url,
                    generation=generations[site_url.url_hash],
                    position=position,
                )
                enqueued_task_ids.append(task.id)
        await session.flush()

    active_ids = tuple(m.site_url_id for m in by_url_id.values() if m.active)
    return SelectionResult(
        selection_version=new_version,
        active_ids=active_ids,
        added_ids=tuple(added_ids),
        removed_ids=tuple(removed_ids),
        workspace_used=new_workspace_total,
        enqueued_task_ids=tuple(enqueued_task_ids),
        cancelled_task_ids=tuple(cancelled_task_ids),
    )


# =========================================================================
# Server-resolved bulk selection
# =========================================================================
BULK_SELECT_MODE_FIRST_N = "first_n"
BULK_SELECT_MODE_ALL = "all"
BULK_SELECT_MODE_NONE = "none"
BULK_SELECT_MODES = (
    BULK_SELECT_MODE_FIRST_N,
    BULK_SELECT_MODE_ALL,
    BULK_SELECT_MODE_NONE,
)


async def bulk_select_monitored_set(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    crawl_id: uuid.UUID,
    mode: str,
    count: int | None = None,
    query: str | None = None,
    expected_selection_version: int,
) -> SelectionResult:
    """Resolve a bulk selection server-side, then replace the monitored set.

    Avoids shipping tens of thousands of ids through the client for "select
    the first N / all discovered URLs": the candidate ``site_url_id``s are
    resolved HERE, in the same deterministic ``(normalized_url, id)`` order
    the inventory endpoint pages in, so "first N" always matches the first N
    rows the user sees in the inventory (under the same ``query`` filter).

    Modes:

    - ``first_n`` — the first ``count`` admitted URLs (``count`` required).
    - ``all``     — every admitted URL (quota still enforced downstream).
    - ``none``    — clear the selection (empty set).

    Candidates are scoped to the crawl's ADMITTED URLs (``SiteUrlObservation``
    rows for this crawl), exactly like the inventory listing — a bulk select
    can never sweep in URLs from an earlier crawl's fuller catalog.

    The heavy lifting (capability gate, version check, workspace quota under
    the entitlement lock, delta application, task enqueue/cancel) is delegated
    to ``replace_monitored_set`` — same locks, same coded errors. An ``all``
    selection larger than the workspace limit raises the SAME
    ``site_health_quota_exceeded`` a manual over-selection would — but it is
    raised HERE, before any lock is taken: candidate resolution is capped at
    ``limit + 1`` ids, so an unfiltered ``all`` over a huge inventory can
    never materialize tens of thousands of ids nor drag them through the
    entitlement-locked replacement path. The under-lock quota check in
    ``replace_monitored_set`` remains the race-safe authority; this pre-check
    only bounds the work.
    """
    if mode not in BULK_SELECT_MODES:
        raise SelectionValidationError(f"Unknown bulk selection mode: {mode!r}")

    crawl = await session.scalar(
        select(SiteCrawl).where(
            SiteCrawl.id == crawl_id,
            SiteCrawl.workspace_id == workspace_id,
            SiteCrawl.project_id == project_id,
        )
    )
    if crawl is None:
        raise SelectionValidationError("Crawl not found in this project")

    site_url_ids: list[uuid.UUID] = []
    if mode != BULK_SELECT_MODE_NONE:
        if mode == BULK_SELECT_MODE_FIRST_N and (count is None or count < 1):
            raise SelectionValidationError(
                "A positive count is required for first_n bulk selection"
            )
        # Read (not lock) the entitlement to bound candidate resolution: any
        # set larger than the workspace limit is doomed to the same quota
        # error downstream, so cap the query at limit + 1 and fail fast
        # before the locked replacement path ever sees an oversized set.
        entitlement = await resolve_entitlement(session, workspace_id)
        limit = int(entitlement.monitored_url_limit)
        fetch_cap = limit + 1 if count is None else min(count, limit + 1)
        admitted = (
            select(SiteUrlObservation.site_url_id)
            .where(SiteUrlObservation.crawl_id == crawl_id)
            .scalar_subquery()
        )
        stmt = select(SiteUrl.id).where(
            SiteUrl.project_id == project_id,
            SiteUrl.id.in_(admitted),
        )
        if query:
            pattern = f"%{query.strip().lower()}%"
            stmt = stmt.where(
                func.lower(SiteUrl.normalized_url).like(pattern)
                | func.lower(SiteUrl.display_url).like(pattern)
            )
        stmt = stmt.order_by(SiteUrl.normalized_url.asc(), SiteUrl.id.asc()).limit(
            fetch_cap
        )
        site_url_ids = list((await session.scalars(stmt)).all())
        # Only pre-raise quota for entitlements that may select at all — a
        # Free workspace must still get its usual StarterRequiredError from
        # the locked path, not a misleading quota error.
        if entitlement_allows_monitored_analysis(entitlement) and (
            len(site_url_ids) > limit
        ):
            currently_used = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(MonitoredSiteUrl)
                        .where(
                            MonitoredSiteUrl.workspace_id == workspace_id,
                            MonitoredSiteUrl.active.is_(True),
                        )
                    )
                )
                or 0
            )
            raise QuotaExceededError(
                "The selection would exceed the workspace monitored-URL limit",
                limit=limit,
                currently_used=currently_used,
            )

    return await replace_monitored_set(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        site_url_ids=site_url_ids,
        expected_selection_version=expected_selection_version,
    )


# =========================================================================
# Explicit per-page rerun
# =========================================================================
@dataclass(frozen=True)
class RerunResult:
    """Identity/status of a freshly-enqueued page rerun (frontend polls this).

    - ``crawl_id`` — the crawl the rerun's analyze task lives in. This is a
      NEW crawl when the project's latest crawl was terminal, or the current
      active crawl when one exists. The frontend must poll the page detail on
      THIS crawl (not the terminal source crawl).
    - ``site_url_id`` — the target URL (unchanged from the request).
    - ``task_id`` — the enqueued ``analyze`` task id (for provenance/debugging).
    - ``created_new_crawl`` — ``True`` iff a fresh crawl was minted, so the
      caller can decide whether to redirect the client to a new crawl route.
    - ``analysis_status`` — the crawl's analysis sub-state at enqueue time
      (always ``pending`` for a brand-new crawl; the current value otherwise),
      so the frontend starts polling from a known non-terminal baseline.
    """

    crawl_id: uuid.UUID
    site_url_id: uuid.UUID
    task_id: uuid.UUID
    created_new_crawl: bool
    analysis_status: str


async def rerun_page(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    site_url_id: uuid.UUID,
) -> RerunResult:
    """Enqueue a fresh ``analyze`` task for one monitored URL under lock.

    Requires an active monitored membership for the URL (``rerun_not_allowed``
    otherwise — never silently no-ops). Locks the project row, then the
    entitlement, then the project profile — the SAME order ``create_crawl``
    uses — so a concurrent selection change, another rerun of the same URL, or
    a concurrent full-crawl creation is serialized. The active-crawl check
    below runs while holding the project lock, so a terminal-page rerun and a
    full crawl can never both observe "no active crawl" and each mint one.

    Crawl identity is chosen so the rerun's task can actually run:

    - If the project has an ACTIVE crawl, the analyze task is enqueued into it
      at the NEXT ``generation`` (so it never collides with a prior run's slot
      or a cancelled task).
    - Otherwise (the latest crawl is terminal — the common "Re-audit from a
      completed crawl" case), a fresh single-page rerun crawl is minted via
      the planner and the task is seeded there. Enqueuing into a terminal
      crawl would be cooperatively cancelled by the worker and never run, so
      this path is required for the visible action to work.

    Returns a ``RerunResult`` carrying the (possibly new) crawl id, the task
    id, the ``created_new_crawl`` flag, and the crawl's analysis sub-state, so
    the API can hand the frontend enough identity to poll the fresh rerun.
    """
    # Lock the project row FIRST — the same lock ``create_crawl`` takes before
    # its active-crawl check — so a concurrent full crawl and this terminal-page
    # rerun serialize and cannot both mint an active crawl. Global lock order:
    # project -> entitlement -> profile.
    project = await _lock_project(
        session, workspace_id=workspace_id, project_id=project_id
    )
    if project is None:
        raise SelectionValidationError("Project not found")

    entitlement = await lock_entitlement(session, workspace_id)
    profile = await _lock_profile(
        session, workspace_id=workspace_id, project_id=project_id
    )
    if profile is None:
        raise SelectionValidationError("Site Health profile not found")

    membership = await session.scalar(
        select(MonitoredSiteUrl).where(
            MonitoredSiteUrl.project_id == project_id,
            MonitoredSiteUrl.site_url_id == site_url_id,
            MonitoredSiteUrl.active.is_(True),
        )
    )
    if membership is None:
        raise RerunNotAllowedError(
            "The URL is not part of the active monitored selection"
        )
    if not entitlement_allows_monitored_analysis(
        entitlement, selection_source=membership.selection_source
    ):
        raise StarterRequiredError(
            "The current capability does not allow analysis of this URL"
        )

    site_url = await session.get(SiteUrl, site_url_id)
    if site_url is None or site_url.project_id != project_id:
        raise SelectionValidationError("Site URL not found in this project")

    crawl = await _active_crawl(
        session, workspace_id=workspace_id, project_id=project_id
    )
    if crawl is not None:
        generations = await _next_generations(
            session,
            crawl_id=crawl.id,
            task_kind=TASK_KIND_ANALYZE,
            url_hashes=[site_url.url_hash],
        )
        task = await _enqueue_analyze_task(
            session,
            crawl=crawl,
            site_url=site_url,
            generation=generations[site_url.url_hash],
            position=0,
        )
        await session.flush()
        return RerunResult(
            crawl_id=crawl.id,
            site_url_id=site_url_id,
            task_id=task.id,
            created_new_crawl=False,
            analysis_status=crawl.analysis_status,
        )

    # No active crawl: mint a fresh single-page rerun crawl so the analyze
    # task can actually run. Imported lazily because ``planner`` imports this
    # module (``seed_monitored_targets``) at module load — a top-level import
    # here would create a cycle.
    from app.domain.site_health.planner import create_page_rerun_crawl

    new_crawl = await create_page_rerun_crawl(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        profile=profile,
        site_url=site_url,
        entitlement=entitlement,
    )
    existing_task = await session.scalar(
        select(SiteCrawlTask).where(
            SiteCrawlTask.crawl_id == new_crawl.id,
            SiteCrawlTask.task_kind == TASK_KIND_ANALYZE,
            SiteCrawlTask.site_url_id == site_url_id,
        )
    )
    await session.flush()
    return RerunResult(
        crawl_id=new_crawl.id,
        site_url_id=site_url_id,
        task_id=existing_task.id if existing_task is not None else new_crawl.id,
        created_new_crawl=True,
        analysis_status=new_crawl.analysis_status,
    )


# =========================================================================
# Recrawl seeding
# =========================================================================
async def seed_monitored_targets(
    session: AsyncSession,
    *,
    crawl: SiteCrawl,
) -> list[uuid.UUID]:
    """Seed ``analyze`` tasks for every active monitored URL of a new crawl.

    Called on every manual recrawl (plan §4): the persistent monitored set is
    re-analyzed so last-audited facts/scores refresh. Newly discovered URLs are
    left unselected (they get no analyze task here). Missing/redirected
    monitored records are preserved — they remain monitored until the user
    removes them, so they are seeded like any other active row.

    Seeds at ``INITIAL_TASK_GENERATION`` because a fresh crawl owns a fresh
    slot namespace. Idempotent: an already-seeded slot is skipped so a retry
    never violates the unique ``(crawl_id, task_kind, url_hash, generation)``.
    """
    result = await session.execute(
        select(MonitoredSiteUrl, SiteUrl)
        .join(SiteUrl, SiteUrl.id == MonitoredSiteUrl.site_url_id)
        .where(
            MonitoredSiteUrl.project_id == crawl.project_id,
            MonitoredSiteUrl.active.is_(True),
        )
        .order_by(SiteUrl.normalized_url.asc())
    )
    rows = result.all()
    if not rows:
        return []

    url_hashes = [site_url.url_hash for _monitored, site_url in rows]
    existing = await session.execute(
        select(SiteCrawlTask.url_hash).where(
            SiteCrawlTask.crawl_id == crawl.id,
            SiteCrawlTask.task_kind == TASK_KIND_ANALYZE,
            SiteCrawlTask.generation == INITIAL_TASK_GENERATION,
            SiteCrawlTask.url_hash.in_(url_hashes),
        )
    )
    already_seeded = {row[0] for row in existing.all()}

    seeded: list[uuid.UUID] = []
    position = 0
    for _monitored, site_url in rows:
        if site_url.url_hash in already_seeded:
            continue
        task = await _enqueue_analyze_task(
            session,
            crawl=crawl,
            site_url=site_url,
            generation=INITIAL_TASK_GENERATION,
            position=position,
        )
        position += 1
        already_seeded.add(site_url.url_hash)
        await session.flush()
        seeded.append(task.id)
    return seeded


# =========================================================================
# Pure worker-side guard functions (Task 5 wires these into the worker)
# =========================================================================
@dataclass(frozen=True)
class GuardDecision:
    """The outcome of a worker guard check (pure, side-effect free)."""

    ok: bool
    reason: str = ""


def crawl_is_active(crawl: SiteCrawl | None) -> TypeGuard[SiteCrawl]:
    """True only when the crawl still exists and is in an active status.

    A cancelled/terminal crawl means the worker must abandon the task without
    persisting evidence (invariant 3 — no artifact for a cancelled task).
    """
    return crawl is not None and crawl.status in CRAWL_ACTIVE_STATUSES


def lease_is_owned(
    task: SiteCrawlTask | None, *, owner: str
) -> TypeGuard[SiteCrawlTask]:
    """True only when THIS worker still holds the task's lease and is working.

    Guards the double-claim / lost-lease case: between the network call and the
    write the sweeper could have reclaimed the lease and another worker could
    have re-claimed it. Only a leased/running row owned by ``owner`` may write.
    """
    return (
        task is not None
        and task.lease_owner == owner
        and task.status in (TASK_STATUS_LEASED, TASK_STATUS_RUNNING)
    )


def monitored_is_active(
    monitored: MonitoredSiteUrl | None,
) -> TypeGuard[MonitoredSiteUrl]:
    """True only when the URL is still an ACTIVE monitored membership.

    A URL removed mid-fetch (its membership deactivated) must not have its
    analysis persisted — the worker re-checks this immediately before I/O and
    again under row lock before evidence persistence.
    """
    return monitored is not None and monitored.active


def evaluate_task_guard(
    *,
    crawl: SiteCrawl | None,
    task: SiteCrawlTask | None,
    monitored: MonitoredSiteUrl | None,
    entitlement,  # WorkspaceSiteHealthEntitlement | None
    owner: str,
) -> GuardDecision:
    """Combined pure guard the worker calls before I/O and before persistence.

    Re-checks, in order: lease ownership, crawl status, active monitoring, and
    the live entitlement (a downgrade blocks new work on user-source rows while
    preserving evidence). Returns the first failing reason, or ``ok`` when all
    pass. Pure: it never touches the DB — the worker loads the rows (under lock
    before persistence) and passes them in.
    """
    if not lease_is_owned(task, owner=owner):
        return GuardDecision(ok=False, reason="lease_not_owned")
    if not crawl_is_active(crawl):
        return GuardDecision(ok=False, reason="crawl_not_active")
    if not monitored_is_active(monitored):
        return GuardDecision(ok=False, reason="not_actively_monitored")
    source = getattr(monitored, "selection_source", SELECTION_SOURCE_USER)
    if not entitlement_allows_monitored_analysis(entitlement, selection_source=source):
        return GuardDecision(ok=False, reason="entitlement_revoked")
    return GuardDecision(ok=True)
