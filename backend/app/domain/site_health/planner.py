# Site Health crawl planner (Task 3 — invariant 9 determinism, invariant 3 freeze).
#
# Owns crawl CREATION for the Site Health subsystem, mirroring the audit
# ``planner.create_audit`` freeze/flush/seed/transition/commit shape:
#
#   1. Load + authorize the workspace-scoped project and derive the crawl root
#      from ``Project.website_url`` (canonicalized through the URL policy).
#   2. Derive + FREEZE the primary registrable domain (offline PSL), root
#      URL/host, and the validated include/exclude narrowing globs onto the
#      project's ``SiteHealthProfile`` (created on first crawl).
#   3. Resolve the workspace entitlement; a Free capability freezes
#      ``sample_mode=True`` and locks the entitlement row so the workspace-wide
#      Free allowance is serialized at creation time.
#   4. Freeze the operational settings + entitlement + rule/scoring versions
#      into ``SiteCrawl.configuration`` so a live env change never alters an
#      in-flight run (invariant 9), store the normalized 64-bit ``random_seed``.
#   5. Seed the in-scope root ``discover`` task (generation 0), plus re-seed the
#      persistent monitored set's ``analyze`` tasks on a recrawl.
#   6. Drive the crawl DRAFT -> VALIDATING -> QUEUED (overall) and
#      PENDING -> RUNNING (discovery) through ``state_events`` guards, record
#      the lifecycle events, and commit with the root task ``queued`` so the
#      worker can claim it.
#
# A second active crawl for the same project is rejected (409
# ``crawl_already_active`` / ``CODE_CRAWL_ALREADY_ACTIVE``).
from __future__ import annotations

import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.connectors.web_evidence.url_policy import (
    UrlPolicyError,
    canonicalize,
    registrable_domain,
    split_host_port,
)
from app.core.config.site_health import (
    ANALYZER_VERSION,
    CODE_CRAWL_ALREADY_ACTIVE,
    CRAWL_ACTIVE_STATUSES,
    CRAWL_STATUS_DRAFT,
    CRAWL_STATUS_QUEUED,
    CRAWL_STATUS_VALIDATING,
    DISCOVERY_MODE_SAMPLE,
    DISCOVERY_STATUS_COMPLETED,
    DISCOVERY_STATUS_RUNNING,
    EVENT_CRAWL_CREATED,
    EVENT_CRAWL_QUEUED,
    EXTRACTOR_VERSION,
    OBSERVATION_SOURCE_ROOT,
    RULE_CATALOG_VERSION,
    SCORING_VERSION,
    TASK_KIND_ANALYZE,
    TASK_KIND_DISCOVER,
    capability_profile,
    site_health_settings,
)
from app.core.config.task_queue import TASK_STATUS_QUEUED
from app.domain.site_health.entitlements import (
    lock_entitlement,
    resolve_entitlement,
)
from app.domain.site_health.normalization import canonical_identity
from app.domain.site_health.selection import seed_monitored_targets
from app.domain.site_health.state_events import (
    apply_crawl_status,
    apply_discovery_status,
    record_crawl_event,
)
from app.models.project import Project
from app.models.site_health import (
    SiteCrawl,
    SiteCrawlTask,
    SiteHealthProfile,
    SiteUrl,
    SiteUrlObservation,
)

# Bound the number of include/exclude narrowing globs accepted at creation so a
# request can never freeze an unbounded pattern list into the crawl config.
MAX_NARROWING_GLOBS = 100
MAX_GLOB_LENGTH = 512


class CrawlPlanError(ValueError):
    """Raised when a crawl cannot be created (bad root/globs, missing project).

    Carries a stable ``code`` so the API layer can map it to the right HTTP
    status (422 for validation, 409 for an already-active crawl).
    """

    code: str = "invalid_crawl_request"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class CrawlAlreadyActiveError(CrawlPlanError):
    """A project already has an active crawl (409)."""

    code = CODE_CRAWL_ALREADY_ACTIVE


def _normalize_seed(value: str | None) -> str:
    """Return a decimal string for a 64-bit unsigned seed (invariant 9).

    Accepts an explicit seed (any 64-bit-representable int) or generates a
    fresh 64-bit one when omitted, so the deterministic frontier order is
    stored and exactly replayable.
    """
    if value is None or not str(value).strip():
        return str(secrets.randbits(64))
    try:
        seed_int = int(str(value).strip())
    except ValueError as exc:
        raise CrawlPlanError("random_seed must be an integer") from exc
    return str(seed_int & ((1 << 64) - 1))


def _normalize_globs(globs: list[str] | None, *, label: str) -> list[str]:
    """Validate + normalize a bounded include/exclude glob list.

    Rejects a list longer than ``MAX_NARROWING_GLOBS`` or a single pattern
    longer than ``MAX_GLOB_LENGTH`` (422). Blank patterns are dropped. The
    result is stored verbatim on the profile and matched against canonical URLs
    by ``url_policy.narrow`` (exclusions win; globs only ever narrow scope).
    """
    if not globs:
        return []
    cleaned: list[str] = []
    for raw in globs:
        pattern = str(raw or "").strip()
        if not pattern:
            continue
        if len(pattern) > MAX_GLOB_LENGTH:
            raise CrawlPlanError(f"{label} glob exceeds max length {MAX_GLOB_LENGTH}")
        cleaned.append(pattern)
    if len(cleaned) > MAX_NARROWING_GLOBS:
        raise CrawlPlanError(f"too many {label} globs (max {MAX_NARROWING_GLOBS})")
    return cleaned


async def _load_project(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    lock: bool = False,
) -> Project:
    stmt = select(Project).where(
        Project.id == project_id,
        Project.workspace_id == workspace_id,
    )
    if lock:
        stmt = stmt.with_for_update()
    project = await session.scalar(stmt)
    if project is None:
        raise CrawlPlanError("Project not found", code="project_not_found")
    return project


async def _has_active_crawl(session: AsyncSession, *, project_id: uuid.UUID) -> bool:
    existing = await session.scalar(
        select(func.count())
        .select_from(SiteCrawl)
        .where(SiteCrawl.project_id == project_id)
        .where(SiteCrawl.status.in_(list(CRAWL_ACTIVE_STATUSES)))
    )
    return bool(existing and existing > 0)


async def _upsert_profile(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    root_url: str,
    root_host: str,
    root_registrable_domain: str,
    include_globs: list[str],
    exclude_globs: list[str],
) -> SiteHealthProfile:
    """Create or refresh the project's ``SiteHealthProfile`` (frozen scope).

    The profile is a project-owned mutable projection (unique ``project_id``):
    it holds the canonical root, the derived registrable domain, and the
    validated narrowing globs the worker enforces. Re-running a crawl refreshes
    them so a changed website URL / narrowing takes effect on the NEXT crawl
    without disturbing the persistent monitored set or ``selection_version``.
    """
    profile = await session.scalar(
        select(SiteHealthProfile).where(SiteHealthProfile.project_id == project_id)
    )
    if profile is None:
        profile = SiteHealthProfile(
            workspace_id=workspace_id,
            project_id=project_id,
        )
        session.add(profile)
    profile.root_url = root_url
    profile.root_host = root_host
    profile.registrable_domain = root_registrable_domain
    profile.include_globs = include_globs or None
    profile.exclude_globs = exclude_globs or None
    await session.flush()
    return profile


def _is_sample_mode(profile) -> bool:
    """Single source of truth for whether a capability crawls in sample mode.

    Free crawls a deterministic sample (no user selection); Starter runs the
    full progressive inventory. Both ``_frozen_configuration`` and
    ``create_crawl`` derive ``sample_mode`` from here so they can never diverge.
    """
    return (
        not profile.allows_user_selection
        and profile.discovery_mode == DISCOVERY_MODE_SAMPLE
    )


def _frozen_configuration(
    *,
    capability: str,
    root_registrable_domain: str,
    include_globs: list[str],
    exclude_globs: list[str],
    entitlement,
) -> dict:
    """Freeze the operational settings + entitlement snapshot (invariant 9).

    Everything the worker needs to run this crawl deterministically regardless
    of a later live env change: the narrowing scope, the frozen capability
    limits, the crawler bounds, and the rule/scoring versions.
    """
    s = site_health_settings
    profile = capability_profile(capability)
    return {
        "capability": profile.capability,
        "discovery_mode": profile.discovery_mode,
        "sample_mode": _is_sample_mode(profile),
        "count_disclosure": profile.count_disclosure,
        "sample_url_limit": int(getattr(entitlement, "sample_url_limit", 0)),
        "monitored_url_limit": int(getattr(entitlement, "monitored_url_limit", 0)),
        "discovery_url_cap": profile.discovery_url_cap,
        "root_registrable_domain": root_registrable_domain,
        "include_globs": include_globs,
        "exclude_globs": exclude_globs,
        "max_frontier_urls": s.max_frontier_urls,
        "max_crawl_depth": s.max_crawl_depth,
        "admission_batch_size": s.admission_batch_size,
        "global_concurrency": s.global_concurrency,
        "per_host_concurrency": s.per_host_concurrency,
        "per_host_delay_seconds": s.per_host_delay_seconds,
        "request_timeout_seconds": s.request_timeout_seconds,
        "max_redirects": s.max_redirects,
        "max_response_wire_bytes": s.max_response_wire_bytes,
        "max_response_decoded_bytes": s.max_response_decoded_bytes,
        "max_attempts": s.max_attempts,
        "extractor_version": EXTRACTOR_VERSION,
        "analyzer_version": ANALYZER_VERSION,
        "rule_catalog_version": RULE_CATALOG_VERSION,
        "scoring_version": SCORING_VERSION,
    }


async def create_crawl(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    random_seed: str | None = None,
) -> SiteCrawl:
    """Create + queue a Site Health crawl (freeze scope, seed the root task).

    Derives the crawl root from ``Project.website_url``, freezes the primary
    registrable domain + narrowing onto the profile and the operational
    settings + entitlement into ``SiteCrawl.configuration``, seeds the in-scope
    root ``discover`` task (and re-seeds the persistent monitored set's
    ``analyze`` tasks), then drives the lifecycle to ``queued`` and commits so
    the worker can claim the root. Rejects a second active crawl for the same
    project (409). Caller owns nothing else — this commits.
    """
    # Lock the project row before checking active state so two concurrent
    # requests for the same project serialize instead of racing past
    # ``_has_active_crawl()`` and both creating a crawl.
    project = await _load_project(
        session, workspace_id=workspace_id, project_id=project_id, lock=True
    )

    if await _has_active_crawl(session, project_id=project_id):
        raise CrawlAlreadyActiveError("Project already has an active crawl")

    raw_root = str(project.website_url or "").strip()
    if not raw_root:
        raise CrawlPlanError("Project has no website_url to crawl", code="invalid_root")
    try:
        root_url = canonicalize(raw_root)
    except UrlPolicyError as exc:
        raise CrawlPlanError(f"invalid crawl root: {exc}", code="invalid_root") from exc

    root_host, _port = split_host_port(root_url)
    root_registrable_domain = registrable_domain(root_url)
    if not root_registrable_domain:
        raise CrawlPlanError(
            "could not derive a registrable domain from the root URL",
            code="invalid_root",
        )

    includes = _normalize_globs(include_globs, label="include")
    excludes = _normalize_globs(exclude_globs, label="exclude")

    # Resolve (seed if missing) the entitlement BEFORE mutating the profile.
    # ``replace_monitored_set()`` locks entitlement before profile, so this
    # path must match that lock order to avoid a deadlock cycle. For a Free
    # capability, LOCK the entitlement row so the workspace-wide sample
    # allowance is serialized at creation time against any concurrent crawl
    # in another project.
    entitlement = await resolve_entitlement(session, workspace_id)
    capability = entitlement.plan_key
    capability_prof = capability_profile(capability)
    sample_mode = _is_sample_mode(capability_prof)
    if sample_mode:
        entitlement = await lock_entitlement(session, workspace_id)
        capability = entitlement.plan_key
        # Re-derive sample_mode from the LOCKED row: the entitlement may have
        # changed between the unlocked read above and acquiring the lock.
        capability_prof = capability_profile(capability)
        sample_mode = _is_sample_mode(capability_prof)

    profile = await _upsert_profile(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        root_url=root_url,
        root_host=root_host,
        root_registrable_domain=root_registrable_domain,
        include_globs=includes,
        exclude_globs=excludes,
    )

    seed = _normalize_seed(random_seed)
    configuration = _frozen_configuration(
        capability=capability,
        root_registrable_domain=root_registrable_domain,
        include_globs=includes,
        exclude_globs=excludes,
        entitlement=entitlement,
    )

    crawl = SiteCrawl(
        workspace_id=workspace_id,
        project_id=project_id,
        profile_id=profile.id,
        status=CRAWL_STATUS_DRAFT,
        root_url=root_url,
        random_seed=seed,
        configuration=configuration,
        sample_mode=sample_mode,
        extractor_version=EXTRACTOR_VERSION,
        analyzer_version=ANALYZER_VERSION,
        rule_catalog_version=RULE_CATALOG_VERSION,
        scoring_version=SCORING_VERSION,
    )
    session.add(crawl)
    await session.flush()  # assign crawl.id

    # Seed the in-scope root discover task (generation 0). The worker claims it,
    # fetches the root, and progressively admits the frontier from there.
    _root_canonical, root_hash = canonical_identity(root_url)
    root_task = SiteCrawlTask(
        crawl_id=crawl.id,
        workspace_id=workspace_id,
        task_kind=TASK_KIND_DISCOVER,
        requested_url=root_url,
        url_hash=root_hash,
        depth=0,
        generation=0,
        idempotency_key=f"{crawl.id}:{TASK_KIND_DISCOVER}:{root_hash}:0",
        status=TASK_STATUS_QUEUED,
        randomized_position=0,
    )
    session.add(root_task)

    # Re-seed the persistent monitored set: on a recrawl the active monitored
    # URLs get fresh analyze tasks so their facts/scores refresh. On a first
    # crawl there is no monitored set yet, so this is a no-op.
    await seed_monitored_targets(session, crawl=crawl)

    # Drive the lifecycle through the guarded state machine (invariant 9).
    apply_crawl_status(crawl, CRAWL_STATUS_VALIDATING)
    apply_crawl_status(crawl, CRAWL_STATUS_QUEUED)
    apply_discovery_status(crawl, DISCOVERY_STATUS_RUNNING)

    count_disclosure = bool(configuration.get("count_disclosure", False))
    record_crawl_event(
        session,
        crawl_id=crawl.id,
        event_type=EVENT_CRAWL_CREATED,
        message="crawl created",
        payload={
            "root_url": root_url,
            "sample_mode": sample_mode,
            "source_kind": OBSERVATION_SOURCE_ROOT,
        },
        count_disclosure=count_disclosure,
    )
    record_crawl_event(
        session,
        crawl_id=crawl.id,
        event_type=EVENT_CRAWL_QUEUED,
        message="crawl queued",
        count_disclosure=count_disclosure,
    )

    await session.commit()
    return await get_crawl(session, workspace_id=workspace_id, crawl_id=crawl.id)


async def create_page_rerun_crawl(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    profile: SiteHealthProfile,
    site_url: SiteUrl,
    entitlement,
    random_seed: str | None = None,
) -> SiteCrawl:
    """Create a fresh, single-page rerun crawl for one already-known URL.

    A "Re-audit this page" action must work even when the project's most
    recent crawl is TERMINAL (completed/partial/failed): enqueuing an analyze
    task into a terminal crawl is cooperatively cancelled by the worker, so it
    would never run. Instead this mints a NEW active crawl whose ONLY work is a
    single ``analyze`` task for ``site_url`` — no ``discover`` root task, so it
    never re-crawls the whole site.

    The fresh crawl:

    - reuses the project's frozen ``SiteHealthProfile`` (root/scope/versions)
      and the current capability snapshot, so scope/version provenance matches
      a normal crawl;
    - records the target's ``SiteUrlObservation`` up front (source_kind
      ``root``) so the page-detail projection — which scopes URLs to a crawl's
      admitted/observed set — resolves the URL on the new crawl immediately;
    - starts with ``discovery_status=completed`` and ``discovered_url_count=1``
      so the shared worker reconciliation terminalizes on the single analyze
      task alone (it is never mis-classified as a fully-failed discovery);
    - is left QUEUED with one QUEUED ``analyze`` task (generation 0 — a fresh
      crawl owns a fresh slot namespace) for the worker to claim.

    The caller owns the transaction boundary (flush, no commit), mirroring
    ``rerun_page``. Returns the new ``SiteCrawl`` (unrefreshed).
    """
    capability = entitlement.plan_key
    capability_prof = capability_profile(capability)
    sample_mode = _is_sample_mode(capability_prof)
    configuration = _frozen_configuration(
        capability=capability,
        root_registrable_domain=profile.registrable_domain or "",
        include_globs=list(profile.include_globs or []),
        exclude_globs=list(profile.exclude_globs or []),
        entitlement=entitlement,
    )
    seed = _normalize_seed(random_seed)

    crawl = SiteCrawl(
        workspace_id=workspace_id,
        project_id=project_id,
        profile_id=profile.id,
        status=CRAWL_STATUS_DRAFT,
        root_url=profile.root_url or site_url.normalized_url,
        random_seed=seed,
        configuration=configuration,
        sample_mode=sample_mode,
        extractor_version=EXTRACTOR_VERSION,
        analyzer_version=ANALYZER_VERSION,
        rule_catalog_version=RULE_CATALOG_VERSION,
        scoring_version=SCORING_VERSION,
        # A single-page rerun performs no discovery: pin the discovery
        # sub-state complete with one observed URL so reconciliation never
        # treats the empty discover plan as a fully-failed crawl.
        discovery_status=DISCOVERY_STATUS_COMPLETED,
        discovered_url_count=1,
        admitted_url_count=1,
        inventory_complete=True,
    )
    session.add(crawl)
    await session.flush()  # assign crawl.id

    # Record the target URL's observation so the page-detail projection (which
    # scopes URLs to a crawl's observed set) resolves it on this fresh crawl.
    from sqlalchemy.dialects.postgresql import insert as _pg_insert

    await session.execute(
        _pg_insert(SiteUrlObservation)
        .values(
            workspace_id=workspace_id,
            project_id=project_id,
            crawl_id=crawl.id,
            site_url_id=site_url.id,
            source_kind=OBSERVATION_SOURCE_ROOT,
            parent_site_url_id=None,
            source_artifact_id=None,
            depth=0,
            observed_url=site_url.normalized_url,
            final_url=site_url.normalized_url,
            status_code=None,
            content_type="",
            title=site_url.latest_title or "",
        )
        .on_conflict_do_nothing(index_elements=["crawl_id", "site_url_id"])
    )

    # Seed exactly ONE analyze task (generation 0) for the target URL.
    analyze_task = SiteCrawlTask(
        crawl_id=crawl.id,
        workspace_id=workspace_id,
        site_url_id=site_url.id,
        task_kind=TASK_KIND_ANALYZE,
        requested_url=site_url.normalized_url,
        url_hash=site_url.url_hash,
        depth=0,
        generation=0,
        idempotency_key=(f"{crawl.id}:{TASK_KIND_ANALYZE}:{site_url.url_hash}:0"),
        status=TASK_STATUS_QUEUED,
        randomized_position=0,
    )
    session.add(analyze_task)

    # Drive the lifecycle through the guarded state machine to QUEUED so the
    # worker can claim the analyze task (invariant 9). Analysis stays PENDING
    # until the worker moves it RUNNING on first claim.
    apply_crawl_status(crawl, CRAWL_STATUS_VALIDATING)
    apply_crawl_status(crawl, CRAWL_STATUS_QUEUED)

    count_disclosure = bool(configuration.get("count_disclosure", False))
    record_crawl_event(
        session,
        crawl_id=crawl.id,
        event_type=EVENT_CRAWL_CREATED,
        message="page rerun crawl created",
        payload={
            "root_url": crawl.root_url,
            "sample_mode": sample_mode,
            "source_kind": OBSERVATION_SOURCE_ROOT,
            "rerun_site_url_id": str(site_url.id),
        },
        count_disclosure=count_disclosure,
    )
    record_crawl_event(
        session,
        crawl_id=crawl.id,
        event_type=EVENT_CRAWL_QUEUED,
        message="crawl queued",
        count_disclosure=count_disclosure,
    )
    await session.flush()
    return crawl


async def get_crawl(
    session: AsyncSession, *, workspace_id: uuid.UUID, crawl_id: uuid.UUID
) -> SiteCrawl:
    """Load a workspace-scoped crawl (eager events) or raise ``CrawlPlanError``."""
    crawl = await session.scalar(
        select(SiteCrawl)
        .options(selectinload(SiteCrawl.events))
        .where(
            SiteCrawl.id == crawl_id,
            SiteCrawl.workspace_id == workspace_id,
        )
    )
    if crawl is None:
        raise CrawlPlanError("Crawl not found", code="crawl_not_found")
    return crawl
