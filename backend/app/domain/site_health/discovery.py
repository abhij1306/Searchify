# Progressive URL discovery: parsing, conflict-safe admission, Free stop-at-10.
#
# The heart of Task 3's inventory pipeline. Three concerns, all deterministic
# and bounded:
#
#   1. ``extract_discovery_links`` — an incremental ``lxml`` parse of a fetched
#      HTML body in DISCOVERY mode only: the page ``<title>`` plus canonical,
#      in-scope, narrowed, de-duplicated anchor links in document order. Bounded
#      by ``max_links_per_page``. (Full page-fact extraction is Task 5.)
#
#   2. ``admit_candidates`` — conflict-safe frontier admission. New ``SiteUrl``
#      identities are inserted with PostgreSQL ``INSERT ... ON CONFLICT DO
#      NOTHING`` on the unique ``(project_id, url_hash)`` so two concurrent
#      workers can never create duplicate inventory rows. Starter admits every
#      in-scope URL up to the frontier ceiling and enqueues child discover
#      tasks. Rows are emitted progressively (committed per batch) so the
#      inventory is queryable while discovery runs.
#
#   3. Free workspace-wide stop-at-10 — for a sample crawl, admission locks the
#      workspace entitlement row ``FOR UPDATE`` and counts active
#      ``free_sample`` monitored rows ACROSS THE WHOLE WORKSPACE. Once the
#      10-URL allowance is filled, admission and all further discovery stop
#      transactionally; each admitted sample URL is added to the system-managed
#      monitored set (``selection_source=free_sample``) and gets an ``analyze``
#      task queued automatically. No total/frontier/overflow count is computed
#      or persisted — the pipeline simply terminalizes at the cap.
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from lxml import etree
from lxml import html as lxml_html
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.web_evidence.url_policy import (
    UrlPolicyError,
    is_admissible,
    split_host_port,
)
from app.core.config.site_health import (
    DISCOVERY_STATUS_RUNNING,
    OBSERVATION_SOURCE_LINK,
    SELECTION_SOURCE_FREE_SAMPLE,
    TASK_KIND_ANALYZE,
    TASK_KIND_DISCOVER,
    site_health_settings,
)
from app.core.config.task_queue import TASK_STATUS_QUEUED
from app.domain.site_health.normalization import canonical_identity, url_hash
from app.domain.site_health.schemas import (
    AdmissionResult,
    DiscoveredLink,
    DiscoveryOutput,
    FrontierCandidate,
)
from app.models.site_health import (
    MonitoredSiteUrl,
    SiteCrawl,
    SiteCrawlTask,
    SiteUrl,
    WorkspaceSiteHealthEntitlement,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def extract_discovery_links(
    body: bytes,
    *,
    base_url: str,
    root_registrable_domain: str,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    max_links: int | None = None,
) -> tuple[str, list[DiscoveredLink]]:
    """Parse HTML into (title, in-scope canonical links) — discovery mode only.

    Deterministic + bounded: anchors are resolved against ``base_url``,
    canonicalized, scope+narrowing checked, de-duplicated by hash, and returned
    in document order up to ``max_links``. Malformed HTML never raises — lxml's
    recovering parser tolerates it and we skip un-canonicalizable hrefs.
    """
    limit = max_links or site_health_settings.max_links_per_page
    title = ""
    links: list[DiscoveredLink] = []
    if not body:
        return title, links

    parser = lxml_html.HTMLParser(
        recover=True, encoding="utf-8", no_network=True
    )
    try:
        root = lxml_html.document_fromstring(body, parser=parser)
    except (etree.ParserError, ValueError):
        return title, links
    if root is None:
        return title, links

    title_nodes = root.xpath("//title")
    if title_nodes:
        title = (title_nodes[0].text_content() or "").strip()[:1024]

    seen: set[str] = set()
    ordinal = 0
    for anchor in root.iter("a"):
        href = anchor.get("href")
        if not href:
            continue
        href = href.strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        try:
            canonical, h = canonical_identity(href, base_url=base_url)
        except UrlPolicyError:
            continue
        if h in seen:
            continue
        if not is_admissible(
            canonical,
            root_registrable_domain=root_registrable_domain,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
        ):
            continue
        seen.add(h)
        links.append(DiscoveredLink(url=canonical, url_hash=h, ordinal=ordinal))
        ordinal += 1
        if len(links) >= limit:
            break
    return title, links


def build_frontier_candidates(
    output: DiscoveryOutput,
    *,
    parent_position: int,
    depth: int,
) -> list[FrontierCandidate]:
    """Turn a discover task's links into deterministically-ordered candidates.

    The order key ``(parent_position, link_ordinal, url_hash)`` makes the
    frontier admission order reproducible under the crawl seed (invariant 9).
    """
    return [
        FrontierCandidate(
            url=link.url,
            url_hash=link.url_hash,
            depth=depth + 1,
            source_kind=OBSERVATION_SOURCE_LINK,
            parent_position=parent_position,
            link_ordinal=link.ordinal,
        )
        for link in output.links
    ]


async def _active_free_sample_count(
    session: AsyncSession, workspace_id: uuid.UUID
) -> int:
    """Count active ``free_sample`` monitored rows across the workspace."""
    result = await session.scalar(
        select(func.count())
        .select_from(MonitoredSiteUrl)
        .where(MonitoredSiteUrl.workspace_id == workspace_id)
        .where(MonitoredSiteUrl.active.is_(True))
        .where(
            MonitoredSiteUrl.selection_source == SELECTION_SOURCE_FREE_SAMPLE
        )
    )
    return int(result or 0)


async def _upsert_site_url(
    session: AsyncSession,
    *,
    crawl: SiteCrawl,
    candidate: FrontierCandidate,
) -> tuple[uuid.UUID, bool]:
    """Insert a ``SiteUrl`` conflict-safely; return ``(id, created)``.

    Uses ``INSERT ... ON CONFLICT (project_id, url_hash) DO NOTHING`` so two
    workers admitting the same URL never create duplicate identities; on
    conflict we read the existing row's id. ``created`` distinguishes a NEW
    identity (counts toward admitted/allowance) from a re-observation.
    """
    now = _utcnow()
    try:
        host, _port = split_host_port(candidate.url)
    except Exception:
        host = ""
    stmt = (
        pg_insert(SiteUrl)
        .values(
            workspace_id=crawl.workspace_id,
            project_id=crawl.project_id,
            normalized_url=candidate.url,
            url_hash=candidate.url_hash,
            display_url=candidate.url,
            host=host[:255],
            depth=candidate.depth,
            discovery_status=DISCOVERY_STATUS_RUNNING,
            latest_source_kind=candidate.source_kind,
            first_seen_crawl_id=crawl.id,
            last_seen_crawl_id=crawl.id,
            first_seen_at=now,
            last_seen_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["project_id", "url_hash"]
        )
        .returning(SiteUrl.id)
    )
    inserted_id = await session.scalar(stmt)
    if inserted_id is not None:
        return inserted_id, True
    existing = await session.scalar(
        select(SiteUrl.id).where(
            SiteUrl.project_id == crawl.project_id,
            SiteUrl.url_hash == candidate.url_hash,
        )
    )
    return existing, False


def _task_idempotency_key(
    crawl_id: uuid.UUID, task_kind: str, url_hash_value: str, generation: int
) -> str:
    return f"{crawl_id}:{task_kind}:{url_hash_value}:{generation}"


async def _enqueue_task(
    session: AsyncSession,
    *,
    crawl: SiteCrawl,
    site_url_id: uuid.UUID | None,
    url: str,
    url_hash_value: str,
    task_kind: str,
    depth: int,
    generation: int = 0,
    randomized_position: int = 0,
    parent_site_url_id: uuid.UUID | None = None,
    priority: int = 0,
) -> uuid.UUID | None:
    """Enqueue one queue row conflict-safely (returns id, or None if it existed).

    The unique ``(crawl_id, task_kind, url_hash, generation)`` slot plus the
    unique ``idempotency_key`` mean a re-admitted URL never double-enqueues in
    the same generation; the insert is ``ON CONFLICT DO NOTHING``.
    """
    stmt = (
        pg_insert(SiteCrawlTask)
        .values(
            crawl_id=crawl.id,
            workspace_id=crawl.workspace_id,
            site_url_id=site_url_id,
            task_kind=task_kind,
            requested_url=url,
            url_hash=url_hash_value,
            depth=depth,
            generation=generation,
            idempotency_key=_task_idempotency_key(
                crawl.id, task_kind, url_hash_value, generation
            ),
            status=TASK_STATUS_QUEUED,
            priority=priority,
            randomized_position=randomized_position,
            parent_site_url_id=parent_site_url_id,
            max_attempts=site_health_settings.max_attempts,
        )
        .on_conflict_do_nothing(
            index_elements=[
                "crawl_id",
                "task_kind",
                "url_hash",
                "generation",
            ]
        )
        .returning(SiteCrawlTask.id)
    )
    return await session.scalar(stmt)


async def _add_free_sample(
    session: AsyncSession,
    *,
    crawl: SiteCrawl,
    site_url_id: uuid.UUID,
    url: str,
    url_hash_value: str,
    depth: int,
) -> None:
    """Add a system-managed sample monitored row + auto-enqueue its analysis.

    Conflict-safe on ``(project_id, site_url_id)`` so re-admission never
    duplicates the membership. The analyze task is what deep-analyzes the Free
    sample automatically, subject to the locked workspace allowance.
    """
    now = _utcnow()
    await session.execute(
        pg_insert(MonitoredSiteUrl)
        .values(
            workspace_id=crawl.workspace_id,
            project_id=crawl.project_id,
            profile_id=crawl.profile_id,
            site_url_id=site_url_id,
            active=True,
            selection_source=SELECTION_SOURCE_FREE_SAMPLE,
            selected_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["project_id", "site_url_id"]
        )
    )
    await _enqueue_task(
        session,
        crawl=crawl,
        site_url_id=site_url_id,
        url=url,
        url_hash_value=url_hash_value,
        task_kind=TASK_KIND_ANALYZE,
        depth=depth,
        priority=1,
    )


async def admit_candidates(
    session: AsyncSession,
    *,
    crawl: SiteCrawl,
    candidates: list[FrontierCandidate],
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    enqueue_children: bool = True,
) -> AdmissionResult:
    """Admit a deterministically-ordered batch of candidates.

    Starter: insert every new ``SiteUrl`` (conflict-safe), bump the crawl's
    admitted counter, and (when ``enqueue_children``) queue a child discover
    task per NEW URL under the depth/frontier ceilings.

    Free (``sample_mode``): lock the workspace entitlement row ``FOR UPDATE``,
    compute the remaining workspace-wide allowance out of the frozen sample
    limit, admit only up to that allowance, add each admitted URL to the
    ``free_sample`` monitored set with an auto-queued analyze task, and stop
    (``sample_capped=True``) the moment the allowance is exhausted — never
    computing a hidden total.

    Caller owns the commit (progressive batches commit per admission call).
    """
    # Deterministic order (invariant 9).
    ordered = sorted(candidates, key=lambda c: c.order_key)
    result = AdmissionResult(admitted=0, sample_capped=False)
    admitted = 0
    settings = site_health_settings

    remaining: int | None = None
    if crawl.sample_mode:
        # Lock the entitlement row so the workspace-wide sample allowance is
        # serialized across concurrent crawls in different projects.
        entitlement = await session.scalar(
            select(WorkspaceSiteHealthEntitlement)
            .where(
                WorkspaceSiteHealthEntitlement.workspace_id
                == crawl.workspace_id
            )
            .with_for_update()
        )
        sample_limit = (
            entitlement.sample_url_limit if entitlement is not None else 0
        )
        used = await _active_free_sample_count(session, crawl.workspace_id)
        remaining = max(0, int(sample_limit) - used)
        if remaining <= 0:
            result = AdmissionResult(admitted=0, sample_capped=True)
            return result

    site_url_ids: dict[str, str] = {}
    for position, candidate in enumerate(ordered):
        if candidate.depth > settings.max_crawl_depth:
            continue
        if crawl.sample_mode and remaining is not None and remaining <= 0:
            result = AdmissionResult(
                admitted=admitted,
                sample_capped=True,
                site_url_ids=site_url_ids,
            )
            return result
        # Starter frontier ceiling.
        if (
            not crawl.sample_mode
            and crawl.admitted_url_count + admitted
            >= settings.max_frontier_urls
        ):
            break

        site_url_id, created = await _upsert_site_url(
            session, crawl=crawl, candidate=candidate
        )
        if site_url_id is None:
            continue
        site_url_ids[candidate.url_hash] = str(site_url_id)
        if not created:
            continue
        admitted += 1

        if crawl.sample_mode:
            await _add_free_sample(
                session,
                crawl=crawl,
                site_url_id=site_url_id,
                url=candidate.url,
                url_hash_value=candidate.url_hash,
                depth=candidate.depth,
            )
            if remaining is not None:
                remaining -= 1
        elif enqueue_children:
            await _enqueue_task(
                session,
                crawl=crawl,
                site_url_id=site_url_id,
                url=candidate.url,
                url_hash_value=candidate.url_hash,
                task_kind=TASK_KIND_DISCOVER,
                depth=candidate.depth,
                randomized_position=position,
                parent_site_url_id=None,
            )

    crawl.admitted_url_count += admitted
    sample_capped = bool(
        crawl.sample_mode and remaining is not None and remaining <= 0
    )
    return AdmissionResult(
        admitted=admitted,
        sample_capped=sample_capped,
        site_url_ids=site_url_ids,
    )


async def monitored_hashes_for_project(
    session: AsyncSession, *, project_id: uuid.UUID
) -> set[str]:
    """Return the url_hashes of active monitored URLs in a project.

    Used by the discover loop to auto-enqueue an ``analyze`` task the moment a
    discovered URL is already monitored (analysis can start during discovery).
    """
    rows = await session.execute(
        select(SiteUrl.url_hash)
        .join(MonitoredSiteUrl, MonitoredSiteUrl.site_url_id == SiteUrl.id)
        .where(MonitoredSiteUrl.project_id == project_id)
        .where(MonitoredSiteUrl.active.is_(True))
    )
    return {row[0] for row in rows.all()}


def compute_url_hash(url: str) -> str:
    """Convenience re-export of the canonical url hash for callers/tests."""
    return url_hash(url)
