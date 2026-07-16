# Site Health worker: the discover-task claim/lease execution loop (Task 3).
#
# A separate process (a dedicated ``site-health-worker`` compose service). It
# mirrors ``AuditWorker`` exactly on the queue mechanics — claim via
# ``PostgresTaskQueue`` (``FOR UPDATE SKIP LOCKED``, lease committed BEFORE any
# network I/O), ``mark_running`` before the fetch, heartbeat the lease while the
# (possibly slow) fetch runs, cooperative cancel at the task boundary, and a
# ``FOR UPDATE`` owner/liveness re-check before persisting any evidence so a
# lost-lease or cancelled task writes NOTHING (invariant 3, acceptance
# criterion 7).
#
# SCOPE (Task 3): this worker claims and executes ONLY ``discover`` tasks. It
# fetches the target through the SSRF-safe ``SecureFetcher`` (with an injected
# DNS resolver — tests inject a fake one, production a real one), extracts
# in-scope canonical links, admits them into the frontier via
# ``discovery.admit_candidates`` (Starter progressive inventory / Free
# workspace-wide stop-at-10 sample), and persists an immutable
# ``SiteUrlObservation`` + ``SiteFetchAttempt`` (+ ``SiteFetchArtifact``) in the
# SAME transaction as the admitted rows + counter bumps + child-task enqueues.
#
# The ``analyze`` / ``link_check`` branches are EXPLICIT reserved dispatch cases
# for Task 5 — they are never claimed by this worker (the claim is filtered to
# ``discover`` so Free's auto-enqueued ``analyze`` tasks wait untouched in the
# queue rather than being force-failed), and ``_execute_discover``'s dispatch
# raises ``NotImplementedError`` if one is ever routed here, which the crash
# handler records as a failure. Task 5 extends THIS SAME worker (no second
# owner of this file).
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors.web_evidence.contracts import (
    DnsResolver,
    FetchError,
    FetchRequest,
    FetchResult,
)
from app.analysis.site_health.parser import extract_page_facts
from app.analysis.site_health.rules import RuleEvaluation, evaluate_all
from app.analysis.site_health.scoring import (
    AnalysisScoreInput,
    _Scored,
    aggregate_scores,
    score_analysis,
)
from app.connectors.web_evidence.fetcher import SecureFetcher
from app.connectors.web_evidence.url_policy import (
    split_host_port,
)
from app.core.config.site_health import (
    ANALYSIS_STATUS_COMPLETED,
    ANALYSIS_STATUS_FAILED,
    ANALYSIS_STATUS_PARTIALLY_COMPLETED,
    ANALYSIS_STATUS_PENDING,
    ANALYSIS_STATUS_RUNNING,
    ANALYZER_VERSION,
    CRAWL_STATUS_COMPLETED,
    CRAWL_STATUS_FAILED,
    CRAWL_STATUS_PARTIALLY_COMPLETED,
    CRAWL_STATUS_RUNNING,
    DISCOVERY_STATUS_COMPLETED,
    DISCOVERY_STATUS_FAILED,
    DISCOVERY_STATUS_RUNNING,
    DISCOVERY_STATUS_SAMPLE_COMPLETED,
    ERROR_HTTP_4XX,
    ERROR_HTTP_5XX,
    EVENT_ANALYSIS_PROGRESS,
    EVENT_CRAWL_COMPLETED,
    EVENT_DISCOVERY_PROGRESS,
    EXTRACTOR_VERSION,
    FETCH_PURPOSE_ANALYZE,
    FETCH_PURPOSE_DISCOVER,
    FETCH_PURPOSE_LINK_CHECK,
    HTML_CONTENT_TYPES,
    OBSERVATION_SOURCE_LINK,
    OBSERVATION_SOURCE_ROOT,
    PAGE_ANALYSIS_STATUS_COMPLETED,
    PAGE_ANALYSIS_STATUS_FAILED,
    PAGE_ANALYSIS_STATUS_PARTIALLY_COMPLETED,
    RULE_CATALOG_VERSION,
    RULE_OUTCOME_ERROR,
    RULE_OUTCOME_FAIL,
    SCORING_VERSION,
    SELECTION_SOURCE_FREE_SAMPLE,
    SELECTION_SOURCE_USER,
    SITE_CRAWL_QUEUE_SPEC,
    TASK_KIND_ANALYZE,
    TASK_KIND_DISCOVER,
    TASK_KIND_LINK_CHECK,
    site_health_settings,
)
from app.core.config.task_queue import (
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCEEDED,
    TASK_TERMINAL_STATUSES,
)
from app.core.database import SessionLocal
from app.core.telemetry import configure_logging
from app.domain.site_health.discovery import (
    admit_candidates,
    build_frontier_candidates,
    extract_discovery_links,
)
from app.domain.site_health.normalization import (
    canonical_identity,
    url_hash as compute_url_hash,
)
from app.domain.site_health.schemas import (
    DiscoveryOutput,
    FrontierCandidate,
)
from app.domain.site_health.selection import crawl_is_active, lease_is_owned
from app.domain.site_health.state_events import (
    apply_analysis_status,
    apply_crawl_status,
    apply_discovery_status,
    record_crawl_event,
)
from app.models.site_health import (
    MonitoredSiteUrl,
    SiteCrawl,
    SiteCrawlTask,
    SiteFetchArtifact,
    SiteFetchAttempt,
    SiteHealthSnapshot,
    SiteIssue,
    SiteLinkReference,
    SitePageAnalysis,
    SiteRuleEvaluation,
    SiteUrl,
    SiteUrlObservation,
)
from app.orchestration.postgres_task_queue import PostgresTaskQueue

logger = logging.getLogger("app.workers.site_health_worker")

# Outcome tokens for the append-only ``SiteFetchAttempt.outcome`` column.
_OUTCOME_SUCCESS = "success"
_OUTCOME_ERROR = "error"


@dataclass(slots=True)
class _DiscoverOutcome:
    """Bounded, in-memory result of a single discover fetch+parse.

    Holds either a success (``result`` + parsed ``output``) or a classified
    failure (``error_code`` + ``retryable``), never both, so the persist step
    can branch on ``output is not None``. ``result`` is present for HTTP
    4xx/5xx (the fetcher returns them) so an artifact can still be written.
    """

    result: FetchResult | None = None
    output: DiscoveryOutput | None = None
    error_code: str = ""
    error_detail: str = ""
    retryable: bool = False
    latency_ms: int | None = None
    status_code: int | None = None
    retry_after_seconds: float | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _serialize_redirect_chain(result: FetchResult) -> list[dict]:
    """Serialize a fetch result's redirect hops to plain JSON-safe dicts."""
    return [
        {
            "from_url": hop.from_url,
            "to_url": hop.to_url,
            "status_code": hop.status_code,
        }
        for hop in result.redirect_chain
    ]


class _SystemDnsResolver:
    """Production DNS resolver using the event loop's ``getaddrinfo``.

    Returns every resolved address (IPv4 + IPv6) so the URL policy can reject
    the whole target if ANY answer is unsafe (rebinding defence). Injected by
    default; tests pass a fake resolver so nothing hits the network.
    """

    async def resolve(self, host: str, port: int) -> list[str]:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, port)
        # infos: list of (family, type, proto, canonname, sockaddr); sockaddr[0]
        # is the IP literal for both AF_INET and AF_INET6.
        seen: list[str] = []
        for info in infos:
            sockaddr = info[4]
            ip = str(sockaddr[0])
            if ip and ip not in seen:
                seen.append(ip)
        return seen


class SiteHealthWorker:
    """Owns a claim/lease loop over ``SiteCrawlTask`` discover rows.

    Mirrors ``AuditWorker``: a single worker claims up to
    ``worker_concurrency`` discover tasks per poll and runs them serially, each
    in its own short-lived session (never one held open across the fetch). The
    DNS resolver is injected (a real one in production, a fake one in tests) so
    the SSRF-safe fetcher runs fully offline under test.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        owner: str | None = None,
        resolver: DnsResolver | None = None,
        transport=None,
    ) -> None:
        self._session_factory = session_factory or SessionLocal
        self._queue: PostgresTaskQueue[SiteCrawlTask] = PostgresTaskQueue(
            self._session_factory, SITE_CRAWL_QUEUE_SPEC
        )
        self.owner = owner or f"site-worker-{uuid.uuid4().hex[:12]}"
        self._resolver = resolver or _SystemDnsResolver()
        # An injected httpx transport (tests pass ``httpx.MockTransport``);
        # None in production so the fetcher pins the validated connection IP.
        self._transport = transport

    async def run_once(self) -> int:
        """Sweep expired leases, claim a batch of all task kinds, execute it.

        Claims ``discover``, ``analyze``, and ``link_check`` tasks (Task 5): a
        widened claim + the routed dispatch in ``_run_discover`` must change
        together — claiming a kind we do not route would force-fail it, and
        routing a kind we do not claim would leave it queued forever.
        """
        await self._queue.release_expired()
        tasks = await self._queue.claim(
            owner=self.owner,
            limit=max(1, site_health_settings.worker_concurrency),
            kinds=[
                TASK_KIND_DISCOVER,
                TASK_KIND_ANALYZE,
                TASK_KIND_LINK_CHECK,
            ],
        )
        for task in tasks:
            await self._execute_task(task)
        return len(tasks)

    async def run_until_idle(self, *, max_batches: int = 1000) -> int:
        """Drain the discover queue until a claim returns nothing (test mode)."""
        total = 0
        for _ in range(max_batches):
            ran = await self.run_once()
            if ran == 0:
                break
            total += ran
        return total

    async def run_forever(self) -> None:  # pragma: no cover - long-running loop
        logger.info("site health worker started", extra={"owner": self.owner})
        while True:
            try:
                ran = await self.run_once()
            except Exception:  # defensive: a bad task must not kill the loop
                logger.exception("site health worker loop iteration failed")
                ran = 0
            if ran == 0:
                await asyncio.sleep(
                    max(0.05, site_health_settings.poll_interval_seconds)
                )

    # --- per-task execution ------------------------------------------------

    async def _execute_task(self, claimed: SiteCrawlTask) -> None:
        """Run one claimed task end to end inside short-lived sessions.

        Honors cooperative cancel at the boundary (before the fetch),
        ``mark_running`` before network I/O, heartbeats the lease during the
        fetch, and finalizes discovery when the queue drains. Never raises — a
        crash is caught and recorded as a queue failure so the lease is always
        released.
        """
        task_id = claimed.id
        crawl_id = claimed.crawl_id
        kind = claimed.task_kind
        try:
            # Cooperative cancel: stop at this boundary if the crawl was
            # cancelled/terminalized since the claim, rather than fetching.
            async with self._session_factory() as session:
                task = await session.get(SiteCrawlTask, task_id)
                crawl = await session.get(SiteCrawl, crawl_id)
                if task is None or crawl is None:
                    await session.rollback()
                    await self._queue.cancel(task_id=task_id)
                    return
                if not crawl_is_active(crawl):
                    await session.rollback()
                    await self._queue.cancel(task_id=task_id)
                    await self._reconcile_crawl_status(crawl_id)
                    return
                # The first task moves the crawl QUEUED -> RUNNING.
                self._ensure_running(crawl)
                await session.commit()

            # Mark the queue row running (still owned) before the fetch.
            if not await self._queue.mark_running(
                task_id=task_id, owner=self.owner
            ):
                # Lease lost (sweeper reclaimed it); another worker will retry.
                return

            if kind == TASK_KIND_DISCOVER:
                await self._run_discover(task_id, crawl_id)
            elif kind == TASK_KIND_ANALYZE:
                await self._run_analyze(task_id, crawl_id)
            elif kind == TASK_KIND_LINK_CHECK:
                await self._run_link_check(task_id, crawl_id)
            else:
                raise NotImplementedError(f"unknown task kind '{kind}'")
        except Exception as exc:  # defensive: never let one task kill the loop
            logger.exception(
                "site health task crashed",
                extra={"task_id": str(task_id), "task_kind": kind},
            )
            await self._record_crash(task_id, exc)
        finally:
            # ONE shared finalize for every kind: it terminalizes the crawl only
            # when EVERY non-terminal task (all kinds) is drained, so a completing
            # discover task never drives the crawl terminal while analyze/
            # link_check work is still queued (which would make a later analysis
            # finalize raise InvalidSiteCrawlTransition from a terminal state).
            await self._reconcile_crawl_status(crawl_id)

    def _ensure_running(self, crawl: SiteCrawl) -> None:
        if crawl.status == CRAWL_STATUS_RUNNING:
            return
        if crawl.started_at is None:
            crawl.started_at = _utcnow()
        apply_crawl_status(crawl, CRAWL_STATUS_RUNNING)

    async def _run_discover(
        self, task_id: uuid.UUID, crawl_id: uuid.UUID
    ) -> None:
        """Fetch + parse the target, then persist observation/admission atomically.

        Loads the crawl config in one short session, closes it before the fetch
        (no txn held across network I/O), fetches through the SSRF-safe fetcher
        while heartbeating the lease, and hands the bounded result to the
        persistence step, which re-checks ownership under a row lock.
        """
        async with self._session_factory() as session:
            task = await session.get(SiteCrawlTask, task_id)
            crawl = await session.get(SiteCrawl, crawl_id)
            if task is None or crawl is None:
                return
            kind = task.task_kind
            requested_url = task.requested_url
            depth = task.depth
            config = dict(crawl.configuration or {})
            root_registrable_domain = config.get("root_registrable_domain") or ""
            include_globs = config.get("include_globs")
            exclude_globs = config.get("exclude_globs")

        if kind != TASK_KIND_DISCOVER:
            # Routing is done in ``_execute_task``; a mis-routed kind here is a
            # wiring bug (never a silent no-op).
            raise NotImplementedError(f"unexpected task kind '{kind}'")

        # Fetch (heartbeating the lease during the possibly-slow call).
        heartbeat = asyncio.create_task(self._heartbeat_loop(task_id))
        try:
            outcome = await self._fetch_discover(
                requested_url=requested_url,
                root_registrable_domain=root_registrable_domain,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

        await self._persist_discover(
            task_id=task_id,
            crawl_id=crawl_id,
            requested_url=requested_url,
            depth=depth,
            outcome=outcome,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            root_registrable_domain=root_registrable_domain,
        )

    async def _fetch_discover(
        self,
        *,
        requested_url: str,
        root_registrable_domain: str,
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
    ) -> _DiscoverOutcome:
        """Fetch + parse one target into a bounded ``_DiscoverOutcome``.

        Returns the discovery output on success (2xx/3xx-final), a classified
        error token on an HTTP 4xx/5xx or a ``FetchError`` (SSRF, redirect
        limit, oversize, timeout, DNS). Never raises for an expected fetch
        failure — the caller persists an attempt row either way.
        """
        request = FetchRequest(
            url=requested_url,
            purpose=FETCH_PURPOSE_DISCOVER,
            allowed_content_types=HTML_CONTENT_TYPES,
        )
        started = time.monotonic()
        try:
            async with SecureFetcher(
                resolver=self._resolver, transport=self._transport
            ) as fetcher:
                result = await fetcher.fetch(
                    request,
                    root_registrable_domain=root_registrable_domain or None,
                    include_globs=include_globs,
                    exclude_globs=exclude_globs,
                    enforce_scope=bool(root_registrable_domain),
                )
        except FetchError as exc:
            latency = int((time.monotonic() - started) * 1000)
            return _DiscoverOutcome(
                error_code=exc.error_code,
                error_detail=str(exc),
                retryable=exc.retryable,
                latency_ms=latency,
                status_code=exc.status_code,
                retry_after_seconds=exc.retry_after_seconds,
            )

        status = result.status_code
        # A 4xx/5xx is returned by the fetcher (not raised); classify it.
        if 400 <= status < 500:
            return _DiscoverOutcome(
                result=result,
                error_code=ERROR_HTTP_4XX,
                retryable=status == 429,
                latency_ms=result.latency_ms,
                status_code=status,
            )
        if status >= 500:
            return _DiscoverOutcome(
                result=result,
                error_code=ERROR_HTTP_5XX,
                retryable=True,
                latency_ms=result.latency_ms,
                status_code=status,
            )

        # Success: parse in-scope canonical links (HTML only; empty otherwise).
        title, links = extract_discovery_links(
            result.body,
            base_url=result.final_url or requested_url,
            root_registrable_domain=root_registrable_domain,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
        )
        output = DiscoveryOutput(
            requested_url=result.requested_url,
            final_url=result.final_url,
            status_code=status,
            content_type=result.content_type,
            title=title,
            links=tuple(links),
            redirect_chain=tuple(_serialize_redirect_chain(result)),
        )
        return _DiscoverOutcome(result=result, output=output)

    async def _heartbeat_loop(
        self, task_id: uuid.UUID
    ) -> None:  # pragma: no cover - timing loop
        interval = max(1.0, site_health_settings.heartbeat_interval_seconds)
        try:
            while True:
                await asyncio.sleep(interval)
                await self._queue.heartbeat(task_id=task_id, owner=self.owner)
        except asyncio.CancelledError:
            raise

    async def _lock_owned_running_task(
        self,
        session: AsyncSession,
        *,
        task_id: uuid.UUID,
        crawl_id: uuid.UUID,
    ) -> tuple[SiteCrawlTask, SiteCrawl] | None:
        """Lock the task FOR UPDATE and verify we still own it before writing.

        Guards invariant 3/acceptance-criterion 7 (single writer, no artifact
        for a cancelled/lost-lease task). Between the fetch finishing and this
        write the lease could have expired (sweeper -> another worker) or the
        crawl could have been cancelled. Returns ``(task, crawl)`` only when the
        task is still leased to THIS worker, still ``running``, and the crawl is
        still active; otherwise ``None`` and the fetch result is discarded.
        """
        task = await session.get(SiteCrawlTask, task_id, with_for_update=True)
        if not lease_is_owned(task, owner=self.owner):
            return None
        if task.status != TASK_STATUS_RUNNING:
            return None
        # Lock the crawl row too: a concurrent cancellation/terminalization must
        # not be able to commit between this active check and the evidence
        # commit (invariant 3: a cancelled task writes NOTHING).
        crawl = await session.get(SiteCrawl, crawl_id, with_for_update=True)
        if not crawl_is_active(crawl):
            return None
        return task, crawl

    async def _persist_discover(
        self,
        *,
        task_id: uuid.UUID,
        crawl_id: uuid.UUID,
        requested_url: str,
        depth: int,
        outcome: _DiscoverOutcome,
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
        root_registrable_domain: str,
    ) -> None:
        """Persist the discover result atomically, then finalize the queue row.

        All evidence (observation + attempt + optional artifact) and inventory
        mutations (admitted rows, counter bumps, child enqueues) commit in ONE
        transaction, gated by a ``FOR UPDATE`` owner/liveness re-check so a
        lost-lease or cancelled task persists nothing. The queue row is then
        succeeded / retried / failed OUTSIDE that transaction.
        """
        should_retry = False
        should_fail = False
        retry_attempt = 0
        succeeded_artifact_id: uuid.UUID | None = None
        async with self._session_factory() as session:
            locked = await self._lock_owned_running_task(
                session, task_id=task_id, crawl_id=crawl_id
            )
            if locked is None:
                # Lease lost or crawl cancelled/terminal: discard everything.
                await session.rollback()
                return
            task, crawl = locked

            artifact_id: uuid.UUID | None = None
            if outcome.output is not None and outcome.result is not None:
                # Success: write the immutable artifact + observation, admit the
                # frontier, and bump counters — all in this one transaction.
                artifact_id = await self._write_artifact(
                    session,
                    crawl=crawl,
                    task=task,
                    result=outcome.result,
                )
                await self._write_observation(
                    session,
                    crawl=crawl,
                    task=task,
                    output=outcome.output,
                    depth=depth,
                    artifact_id=artifact_id,
                )
                admission = await admit_candidates(
                    session,
                    crawl=crawl,
                    candidates=self._candidates_for(outcome.output, depth),
                    include_globs=include_globs,
                    exclude_globs=exclude_globs,
                )
                crawl.discovered_url_count += 1
                # Link the queue row to its immutable artifact (mirrors the
                # audit worker's result_artifact_id contract).
                task.result_artifact_id = artifact_id
                succeeded_artifact_id = artifact_id
                if admission.sample_capped:
                    # Free stop-at-10: terminate discovery at the cap. No
                    # total-bearing value is computed or persisted.
                    apply_discovery_status(
                        crawl, DISCOVERY_STATUS_SAMPLE_COMPLETED
                    )
                record_crawl_event(
                    session,
                    crawl_id=crawl_id,
                    event_type=EVENT_DISCOVERY_PROGRESS,
                    message="discovery progress",
                    payload={
                        "admitted": admission.admitted,
                        "depth": depth,
                    },
                    count_disclosure=bool(
                        (crawl.configuration or {}).get(
                            "count_disclosure", False
                        )
                    ),
                )
            else:
                # Failure path: append the attempt, bump the failed counter, and
                # decide retry vs. terminal fail from the retry budget.
                exhausted = task.attempt_count + 1 >= task.max_attempts
                should_retry = outcome.retryable and not exhausted
                should_fail = not should_retry
                # Attempt number this failure represents (1-based), used to
                # grow the backoff deterministically across retries.
                retry_attempt = task.attempt_count + 1
                if should_fail:
                    crawl.failed_url_count += 1

            self._write_attempt(
                session,
                crawl=crawl,
                task=task,
                outcome=outcome,
                requested_url=requested_url,
                artifact_id=artifact_id,
            )
            task.attempt_count += 1
            await session.commit()

        if outcome.output is not None:
            await self._queue.succeed(
                task_id=task_id,
                owner=self.owner,
                result_artifact_id=succeeded_artifact_id,
            )
        elif should_retry:
            await self._queue.retry(
                task_id=task_id,
                owner=self.owner,
                delay_seconds=site_health_settings.retry_delay(
                    retry_attempt, outcome.retry_after_seconds
                ),
                error_code=outcome.error_code,
                error_detail=outcome.error_detail,
            )
        else:
            await self._queue.fail(
                task_id=task_id,
                owner=self.owner,
                error_code=outcome.error_code,
                error_detail=outcome.error_detail,
            )

    def _candidates_for(
        self, output: DiscoveryOutput, depth: int
    ) -> list[FrontierCandidate]:
        # The discover task's own position is its randomized_position; children
        # inherit deterministic order via (parent_position, link_ordinal, hash).
        return build_frontier_candidates(
            output, parent_position=0, depth=depth
        )

    async def _write_artifact(
        self,
        session: AsyncSession,
        *,
        crawl: SiteCrawl,
        task: SiteCrawlTask,
        result: FetchResult,
    ) -> uuid.UUID:
        """Write the immutable per-task fetch artifact (unique ``task_id``)."""
        content_hash = hashlib.sha256(result.body or b"").hexdigest()
        artifact = SiteFetchArtifact(
            task_id=task.id,
            crawl_id=crawl.id,
            workspace_id=crawl.workspace_id,
            fetch_purpose=FETCH_PURPOSE_DISCOVER,
            requested_url=result.requested_url,
            final_url=result.final_url,
            redirect_chain=_serialize_redirect_chain(result),
            status_code=result.status_code,
            redacted_headers=dict(result.redacted_headers or {}),
            content_type=result.content_type,
            content_hash=content_hash,
            http_version=result.http_version,
            ttfb_ms=result.ttfb_ms,
            latency_ms=result.latency_ms,
            wire_bytes=result.wire_bytes,
            decoded_bytes=result.decoded_bytes,
            extractor_version=crawl.extractor_version,
        )
        session.add(artifact)
        await session.flush()
        return artifact.id

    async def _write_observation(
        self,
        session: AsyncSession,
        *,
        crawl: SiteCrawl,
        task: SiteCrawlTask,
        output: DiscoveryOutput,
        depth: int,
        artifact_id: uuid.UUID | None,
    ) -> None:
        """Write the immutable per-crawl observation for the fetched URL.

        Conflict-safe on the unique ``(crawl_id, site_url_id)`` — a URL can be
        observed more than once in a crawl, so a plain insert would raise an
        ``IntegrityError`` and poison this transaction. Resolves the SiteUrl
        identity (creating it conflict-safely for the root, which has no
        pre-created inventory row) and refreshes its lightweight state.
        """
        # The observation's own URL identity: the requested URL's SiteUrl row.
        site_url_id = await self._resolve_site_url_id(
            session, crawl=crawl, url=output.requested_url, depth=depth
        )
        if site_url_id is None:
            return
        # Refresh the lightweight discovery state on the identity row.
        site_url = await session.get(SiteUrl, site_url_id)
        if site_url is not None:
            site_url.latest_title = (output.title or "")[:1024]
            site_url.latest_content_type = (output.content_type or "")[:128]
            site_url.last_seen_crawl_id = crawl.id
            site_url.discovery_status = DISCOVERY_STATUS_COMPLETED
        await session.execute(
            pg_insert(SiteUrlObservation)
            .values(
                workspace_id=crawl.workspace_id,
                crawl_id=crawl.id,
                site_url_id=site_url_id,
                source_kind=(
                    OBSERVATION_SOURCE_ROOT if depth == 0 else OBSERVATION_SOURCE_LINK
                ),
                parent_site_url_id=task.parent_site_url_id,
                source_artifact_id=artifact_id,
                depth=depth,
                observed_url=output.requested_url,
                final_url=output.final_url,
                status_code=output.status_code,
                content_type=(output.content_type or "")[:128],
                title=(output.title or "")[:1024],
            )
            .on_conflict_do_nothing(
                index_elements=["crawl_id", "site_url_id"]
            )
        )

    async def _resolve_site_url_id(
        self,
        session: AsyncSession,
        *,
        crawl: SiteCrawl,
        url: str,
        depth: int,
    ) -> uuid.UUID | None:
        """Return the SiteUrl id for ``url``, creating it conflict-safely.

        Child URLs already have an identity from admission, but the root's
        identity is created here on its first (depth 0) fetch. Uses the same
        ``ON CONFLICT (project_id, url_hash) DO NOTHING`` pattern as admission.
        """
        try:
            canonical, url_hash_value = canonical_identity(url)
        except Exception:
            return None
        try:
            host, _port = split_host_port(canonical)
        except Exception:
            host = ""
        now = _utcnow()
        inserted_id = await session.scalar(
            pg_insert(SiteUrl)
            .values(
                workspace_id=crawl.workspace_id,
                project_id=crawl.project_id,
                normalized_url=canonical,
                url_hash=url_hash_value,
                display_url=canonical,
                host=host[:255],
                depth=depth,
                discovery_status=DISCOVERY_STATUS_RUNNING,
                latest_source_kind=(
                    OBSERVATION_SOURCE_ROOT if depth == 0 else OBSERVATION_SOURCE_LINK
                ),
                first_seen_crawl_id=crawl.id,
                last_seen_crawl_id=crawl.id,
                first_seen_at=now,
                last_seen_at=now,
            )
            .on_conflict_do_nothing(index_elements=["project_id", "url_hash"])
            .returning(SiteUrl.id)
        )
        if inserted_id is not None:
            return inserted_id
        return await session.scalar(
            select(SiteUrl.id).where(
                SiteUrl.project_id == crawl.project_id,
                SiteUrl.url_hash == url_hash_value,
            )
        )

    def _write_attempt(
        self,
        session: AsyncSession,
        *,
        crawl: SiteCrawl,
        task: SiteCrawlTask,
        outcome: _DiscoverOutcome,
        requested_url: str,
        artifact_id: uuid.UUID | None,
    ) -> None:
        """Append the append-only diagnostic attempt (host only, no secrets)."""
        try:
            host, _port = split_host_port(requested_url)
        except Exception:
            host = ""
        succeeded = outcome.output is not None
        session.add(
            SiteFetchAttempt(
                task_id=task.id,
                crawl_id=crawl.id,
                workspace_id=crawl.workspace_id,
                attempt_number=task.attempt_count + 1,
                method="GET",
                target_host=host[:255],
                outcome=_OUTCOME_SUCCESS if succeeded else _OUTCOME_ERROR,
                error_code=outcome.error_code,
                status_code=outcome.status_code,
                latency_ms=outcome.latency_ms,
                wire_bytes=(
                    outcome.result.wire_bytes
                    if outcome.result is not None
                    else None
                ),
                decoded_bytes=(
                    outcome.result.decoded_bytes
                    if outcome.result is not None
                    else None
                ),
                artifact_id=artifact_id,
            )
        )

    async def _record_crash(
        self, task_id: uuid.UUID, exc: Exception
    ) -> None:
        detail = f"{type(exc).__name__}: {exc}"
        await self._queue.fail(
            task_id=task_id,
            owner=self.owner,
            error_code="crawl_task_crashed",
            error_detail=detail,
        )

    async def _finalize_discovery(self, crawl_id: uuid.UUID) -> None:
        """Terminalize discovery + the crawl when no discover work remains.

        Runs after each discover task terminalizes. When no non-terminal
        discover task is left and discovery is still ``running``, moves
        discovery -> completed and the crawl -> completed. A crawl whose
        discovery already reached ``sample_completed`` (Free cap) is completed
        the same way. Guarded with ``FOR UPDATE`` so concurrent workers never
        double-finalize. (Analysis finalization is Task 5's concern.)
        """
        async with self._session_factory() as session:
            crawl = await session.get(
                SiteCrawl, crawl_id, with_for_update=True
            )
            if crawl is None or not crawl_is_active(crawl):
                if crawl is not None:
                    await session.rollback()
                return
            remaining = await session.scalar(
                select(func.count())
                .select_from(SiteCrawlTask)
                .where(SiteCrawlTask.crawl_id == crawl_id)
                .where(SiteCrawlTask.task_kind == TASK_KIND_DISCOVER)
                .where(
                    SiteCrawlTask.status.not_in(
                        list(TASK_TERMINAL_STATUSES)
                    )
                )
            )
            if remaining and remaining > 0:
                await session.rollback()
                return
            # Discovery is drained. Classify the terminal outcome by how many
            # URLs were discovered vs. failed. Task 5 will own the analysis
            # lifecycle; here discovery-only crawls terminalize.
            #   - no discovered URLs (root itself failed)  -> failed
            #   - some discovered but some failed          -> partially_completed
            #   - all discovered cleanly                   -> completed
            # Only reclassify discovery while it is still ``running``; a Free
            # crawl already at ``sample_completed`` (and ``completed``/``failed``)
            # is terminal and must not be transitioned again (state_events would
            # raise InvalidSiteCrawlTransition).
            fully_failed = crawl.discovered_url_count == 0
            partial = (
                crawl.discovered_url_count > 0 and crawl.failed_url_count > 0
            )
            if crawl.discovery_status == DISCOVERY_STATUS_RUNNING:
                if fully_failed:
                    apply_discovery_status(crawl, DISCOVERY_STATUS_FAILED)
                else:
                    apply_discovery_status(crawl, DISCOVERY_STATUS_COMPLETED)
            # A fully-failed crawl produced no inventory; only mark the
            # inventory complete when at least one URL was discovered.
            crawl.inventory_complete = not fully_failed
            if crawl.status == CRAWL_STATUS_RUNNING:
                crawl.completed_at = _utcnow()
                if fully_failed:
                    apply_crawl_status(crawl, CRAWL_STATUS_FAILED)
                elif partial:
                    apply_crawl_status(
                        crawl, CRAWL_STATUS_PARTIALLY_COMPLETED
                    )
                else:
                    apply_crawl_status(crawl, CRAWL_STATUS_COMPLETED)
            await session.commit()


def main() -> None:  # pragma: no cover - process entrypoint
    configure_logging()
    worker = SiteHealthWorker()
    asyncio.run(worker.run_forever())


if __name__ == "__main__":  # pragma: no cover
    main()
