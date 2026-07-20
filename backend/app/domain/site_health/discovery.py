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

import codecs
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
from app.domain.site_health.normalization import canonical_identity
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
    SiteUrlObservation,
    WorkspaceSiteHealthEntitlement,
)


def _safe_parser_encoding(charset: str) -> str | None:
    """Return a codec-valid encoding name, or ``None`` to auto-detect.

    A response's declared charset is arbitrary attacker-influenced input. Handed
    straight to ``lxml``'s ``HTMLParser(encoding=...)`` an unknown value raises
    ``LookupError`` at parser-construction time — outside the ``try`` guarding
    the actual parse — which would crash discovery instead of degrading. Validate
    with ``codecs.lookup``; on an empty/unknown value return ``None`` so lxml
    auto-detects rather than raising.
    """
    normalized = str(charset or "").strip()
    if not normalized:
        return None
    try:
        codecs.lookup(normalized)
    except LookupError:
        return None
    return normalized.lower()


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
    charset: str = "",
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

    # Honor the response's declared charset when present; otherwise let lxml
    # auto-detect rather than hard-coding UTF-8 (a mismatched hard-coded
    # charset can mangle a non-UTF-8 page's anchors/title). A bogus/unknown
    # charset is validated away to None (auto-detect) so parser construction
    # never raises LookupError.
    declared_charset = _safe_parser_encoding(charset)
    parser = lxml_html.HTMLParser(
        recover=True, encoding=declared_charset, no_network=True
    )
    try:
        root = lxml_html.document_fromstring(body, parser=parser)
    except (etree.ParserError, ValueError):
        return title, links
    if root is None:
        return title, links

    title_node = next(root.iter("title"), None)
    if title_node is not None:
        title_text = "".join(
            t if isinstance(t, str) else t.decode("utf-8", "replace")
            for t in title_node.itertext()
        )
        title = title_text.strip()[:1024]

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
        .where(MonitoredSiteUrl.selection_source == SELECTION_SOURCE_FREE_SAMPLE)
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
    # urlsplit port parsing raises ValueError on a malformed-but-admitted URL;
    # host is display metadata only, so degrade to "" (same catch as
    # is_in_scope). Anything else is a systemic bug and must propagate.
    except ValueError:
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
        .on_conflict_do_nothing(index_elements=["project_id", "url_hash"])
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
    if existing is None:
        # Unreachable barring a concurrent hard-delete between the conflicting
        # insert and this read — surface loudly rather than return a bogus id.
        raise RuntimeError(f"SiteUrl row vanished for url_hash={candidate.url_hash!r}")
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
    source_kind: str = OBSERVATION_SOURCE_LINK,
) -> bool:
    """Add/reactivate a system-managed sample monitored row + auto-enqueue.

    Conflict-safe on ``(project_id, site_url_id)`` so re-admission never
    duplicates the membership. Three cases on conflict:

    - No existing row: a fresh ``INSERT`` creates a new active membership.
    - An existing row that is currently INACTIVE (e.g. deactivated by a
      selection replacement, or deselected then rediscovered): it is
      reactivated in place (``active=True``, ``selected_at`` refreshed,
      ``deselected_at`` cleared) rather than silently doing nothing, so a
      recrawl can genuinely bring a previously-sampled URL back into the
      monitored set.
    - An existing row that is ALREADY active: the conflict update's ``WHERE``
      guard means nothing changes and the statement is a no-op (equivalent to
      ``DO NOTHING``), so re-observing an already-sampled URL never appears
      to "activate" it again.

    The analyze task is what deep-analyzes the Free sample automatically,
    subject to the locked workspace allowance.

    Returns ``True`` only when this call newly activated the membership
    (inserted a brand-new row or reactivated an inactive one) — i.e. exactly
    when the caller should decrement the remaining workspace-wide sample
    allowance. Returns ``False`` when the membership was already active
    (re-observing an existing, already-counted sample must not consume a
    second unit of the allowance).
    """
    now = _utcnow()
    activated_id = await session.scalar(
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
        .on_conflict_do_update(
            index_elements=["project_id", "site_url_id"],
            set_={
                "active": True,
                "selection_source": SELECTION_SOURCE_FREE_SAMPLE,
                "selected_at": now,
                "deselected_at": None,
            },
            where=(MonitoredSiteUrl.active.is_(False)),
        )
        .returning(MonitoredSiteUrl.id)
    )
    newly_activated = activated_id is not None
    # Record per-crawl admission provenance for the sampled URL. The pages /
    # inventory read paths scope strictly through ``SiteUrlObservation``
    # (see ``_admitted_site_url_subquery``), and a Free crawl fetches most of
    # its sample via analyze-only tasks (no per-URL discover task ever runs),
    # so without this row 9 of 10 sampled URLs would be invisible in the UI.
    # Conflict-safe on the unique ``(crawl_id, site_url_id)`` pair; the richer
    # discover-path observation (status/title/artifact) wins if it ran first,
    # and this sparse admission row is enriched later by the analyze result.
    await session.execute(
        pg_insert(SiteUrlObservation)
        .values(
            workspace_id=crawl.workspace_id,
            project_id=crawl.project_id,
            crawl_id=crawl.id,
            site_url_id=site_url_id,
            source_kind=source_kind,
            depth=depth,
            observed_url=url,
            final_url=url,
        )
        .on_conflict_do_nothing(index_elements=["crawl_id", "site_url_id"])
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
    return newly_activated


async def admit_candidates(
    session: AsyncSession,
    *,
    crawl: SiteCrawl,
    candidates: list[FrontierCandidate],
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
    admitted = 0
    settings = site_health_settings

    remaining: int | None = None
    if crawl.sample_mode:
        # Lock the entitlement row so the workspace-wide sample allowance is
        # serialized across concurrent crawls in different projects.
        entitlement = await session.scalar(
            select(WorkspaceSiteHealthEntitlement)
            .where(WorkspaceSiteHealthEntitlement.workspace_id == crawl.workspace_id)
            .with_for_update()
        )
        sample_limit = entitlement.sample_url_limit if entitlement is not None else 0
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
            and crawl.admitted_url_count + admitted >= settings.max_frontier_urls
        ):
            break

        site_url_id, created = await _upsert_site_url(
            session, crawl=crawl, candidate=candidate
        )
        if site_url_id is None:
            continue
        site_url_ids[candidate.url_hash] = str(site_url_id)
        if created:
            admitted += 1

        # Per-crawl admission must not be limited to newly-created child
        # identities: a complete recrawl re-observes URLs that already have a
        # SiteUrl identity from an earlier crawl, and a Free crawl's sample
        # allowance must keep filling from EXISTING identities too (otherwise
        # a recrawl of an already-discovered site can admit nothing and Free
        # sites end up with an undersized sample). Both branches below run
        # for every candidate whose identity resolved, not just new ones; the
        # task/membership inserts are themselves conflict-safe so a
        # re-observation of an already-scheduled URL is a safe no-op.
        if crawl.sample_mode:
            newly_activated = await _add_free_sample(
                session,
                crawl=crawl,
                site_url_id=site_url_id,
                url=candidate.url,
                url_hash_value=candidate.url_hash,
                depth=candidate.depth,
                source_kind=candidate.source_kind,
            )
            if newly_activated and remaining is not None:
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
    sample_capped = bool(crawl.sample_mode and remaining is not None and remaining <= 0)
    return AdmissionResult(
        admitted=admitted,
        sample_capped=sample_capped,
        site_url_ids=site_url_ids,
    )
