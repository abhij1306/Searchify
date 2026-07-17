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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin

from sqlalchemy import Row, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analysis.site_health.parser import extract_page_facts
from app.analysis.site_health.rules import RuleEvaluation, evaluate_all
from app.analysis.site_health.scoring import (
    AnalysisScoreInput,
    aggregate_scores,
    score_analysis,
)
from app.connectors.web_evidence.contracts import (
    DnsResolver,
    FetchError,
    FetchRequest,
    FetchResult,
)
from app.connectors.web_evidence.fetcher import SecureFetcher
from app.connectors.web_evidence.url_policy import (
    split_host_port,
)
from app.core.config.site_health import (
    ANALYSIS_STATUS_CANCELLED,
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
    RULE_OUTCOME_FAIL,
    SCORING_VERSION,
    SITE_CRAWL_QUEUE_SPEC,
    TASK_KIND_ANALYZE,
    TASK_KIND_DISCOVER,
    TASK_KIND_LINK_CHECK,
    site_health_settings,
)
from app.core.config.task_queue import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCEEDED,
    TASK_TERMINAL_STATUSES,
)
from app.core.database import SessionLocal
from app.core.telemetry import configure_logging
from app.domain.site_health.discovery import (
    _enqueue_task as _enqueue_discovery_task,
)
from app.domain.site_health.discovery import (
    admit_candidates,
    build_frontier_candidates,
    extract_discovery_links,
)
from app.domain.site_health.normalization import (
    canonical_identity,
)
from app.domain.site_health.schemas import (
    DiscoveryOutput,
    FrontierCandidate,
)
from app.domain.site_health.selection import (
    crawl_is_active,
    evaluate_task_guard,
    lease_is_owned,
)
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
    WorkspaceSiteHealthEntitlement,
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


@dataclass(slots=True)
class _AnalyzeOutcome:
    """Bounded, in-memory result of a single analyze fetch+parse.

    Holds either a success (``result`` + parsed ``facts``) or a classified
    failure (``error_code`` + ``retryable``). ``result`` is present for HTTP
    4xx/5xx so an artifact/attempt can still be recorded on a hard failure.
    """

    result: FetchResult | None = None
    facts: dict | None = None
    error_code: str = ""
    error_detail: str = ""
    retryable: bool = False
    latency_ms: int | None = None
    status_code: int | None = None
    retry_after_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class _LinkProbeOutcome:
    """Observable inputs captured by a bounded link probe."""

    reachable: bool
    method: str
    status_code: int | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _classify_http_error(status: int) -> tuple[str, bool] | None:
    """Map an HTTP status the fetcher returned (not raised) to (code, retry).

    Returns ``None`` for a non-error status. A 4xx is terminal except 429
    (rate limit, retryable); every 5xx is retryable. Shared by the discover
    and analyze fetch paths so the classification stays in one place.
    """
    if 400 <= status < 500:
        return ERROR_HTTP_4XX, status == 429
    if status >= 500:
        return ERROR_HTTP_5XX, True
    return None


def _count_disclosure(crawl: SiteCrawl) -> bool:
    """Whether this crawl opted into exact-count disclosure in its config."""
    return bool((crawl.configuration or {}).get("count_disclosure", False))


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
        await self._queue.release_expired(
            batch_size=site_health_settings.lease_reclaim_batch_size
        )
        tasks = await self._queue.claim(
            owner=self.owner,
            # Execution is serial, so claiming a batch would leave every task
            # after the first without a heartbeat while the first performs
            # network I/O. Claim one lease at a time to prevent expiry/reclaim.
            limit=1,
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
            if not await self._queue.mark_running(task_id=task_id, owner=self.owner):
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

    async def _run_discover(self, task_id: uuid.UUID, crawl_id: uuid.UUID) -> None:
        """Fetch + parse the target, then persist observation/admission atomically.

        Loads the crawl config in one short session, closes it before the fetch
        (no txn held across network I/O), fetches through the SSRF-safe fetcher
        while heartbeating the lease, and hands the bounded result to the
        persistence step, which re-checks ownership under a row lock.
        """
        # Discover evidence (artifact + observation + admission) commits before
        # ``_queue.succeed()``. If that out-of-transaction acknowledgement
        # fails, a reclaimed task must acknowledge the durable result instead
        # of refetching and colliding with the existing unique
        # ``(task_id, fetch_purpose)`` artifact row (mirrors the analyze flow).
        persisted_artifact_id = await self._persisted_discover_artifact_id(task_id)
        if persisted_artifact_id is not None:
            await self._queue.succeed(
                task_id=task_id,
                owner=self.owner,
                result_artifact_id=persisted_artifact_id,
            )
            return

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
        classified = _classify_http_error(status)
        if classified is not None:
            error_code, retryable = classified
            return _DiscoverOutcome(
                result=result,
                error_code=error_code,
                retryable=retryable,
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
            charset=result.charset,
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
                    apply_discovery_status(crawl, DISCOVERY_STATUS_SAMPLE_COMPLETED)
                record_crawl_event(
                    session,
                    crawl_id=crawl_id,
                    event_type=EVENT_DISCOVERY_PROGRESS,
                    message="discovery progress",
                    payload={
                        "admitted": admission.admitted,
                        "depth": depth,
                    },
                    count_disclosure=_count_disclosure(crawl),
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
                succeeded=outcome.output is not None,
                requested_url=requested_url,
                artifact_id=artifact_id,
            )
            task.attempt_count += 1
            await session.commit()

        await self._finalize_queue_row(
            task_id=task_id,
            succeeded=outcome.output is not None,
            succeeded_artifact_id=succeeded_artifact_id,
            should_retry=should_retry,
            retry_attempt=retry_attempt,
            error_code=outcome.error_code,
            error_detail=outcome.error_detail,
            retry_after_seconds=outcome.retry_after_seconds,
        )

    def _candidates_for(
        self, output: DiscoveryOutput, depth: int
    ) -> list[FrontierCandidate]:
        # The discover task's own position is its randomized_position; children
        # inherit deterministic order via (parent_position, link_ordinal, hash).
        candidates = build_frontier_candidates(output, parent_position=0, depth=depth)
        if depth == 0:
            # The root/fetched identity itself must also go through admission
            # (not just its extracted child links): a Free crawl's sample
            # allowance is filled from admitted identities, and the root's
            # SiteUrl identity is created lazily on its first fetch (it has no
            # pre-existing inventory row), so skipping it here would leave
            # Free crawls with no or an undersized sample and would exclude
            # the root from ``free_sample`` monitoring/auto-analysis.
            root_url_hash = canonical_identity(output.requested_url)[1]
            candidates.append(
                FrontierCandidate(
                    url=output.requested_url,
                    url_hash=root_url_hash,
                    depth=depth,
                    source_kind=OBSERVATION_SOURCE_ROOT,
                    parent_position=-1,
                    link_ordinal=-1,
                )
            )
        return candidates

    async def _write_artifact(
        self,
        session: AsyncSession,
        *,
        crawl: SiteCrawl,
        task: SiteCrawlTask,
        result: FetchResult,
        fetch_purpose: str = FETCH_PURPOSE_DISCOVER,
        normalized_facts: dict | None = None,
    ) -> uuid.UUID:
        """Write the immutable per-task fetch artifact (unique ``task_id``).

        Reused by both discover and analyze; ``fetch_purpose`` records why the
        fetch happened and ``normalized_facts`` carries the bounded parsed page
        facts for an analyze artifact (there is NO raw body column anywhere).
        """
        content_hash = hashlib.sha256(result.body or b"").hexdigest()
        artifact = SiteFetchArtifact(
            task_id=task.id,
            crawl_id=crawl.id,
            workspace_id=crawl.workspace_id,
            fetch_purpose=fetch_purpose,
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
            extractor_version=crawl.extractor_version or EXTRACTOR_VERSION,
            normalized_facts=normalized_facts,
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
                project_id=crawl.project_id,
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
            .on_conflict_do_nothing(index_elements=["crawl_id", "site_url_id"])
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
        outcome: _DiscoverOutcome | _AnalyzeOutcome,
        succeeded: bool,
        requested_url: str,
        artifact_id: uuid.UUID | None,
    ) -> None:
        """Append the append-only diagnostic attempt (host only, no secrets).

        Shared by discover and analyze; ``succeeded`` is decided by the caller
        (a discover success has a parsed ``output``, an analyze success has
        parsed ``facts``) so this stays agnostic to the outcome payload shape.
        """
        try:
            host, _port = split_host_port(requested_url)
        except Exception:
            host = ""
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
                    outcome.result.wire_bytes if outcome.result is not None else None
                ),
                decoded_bytes=(
                    outcome.result.decoded_bytes if outcome.result is not None else None
                ),
                artifact_id=artifact_id,
            )
        )

    async def _record_crash(self, task_id: uuid.UUID, exc: Exception) -> None:
        detail = f"{type(exc).__name__}: {exc}"
        await self._queue.fail(
            task_id=task_id,
            owner=self.owner,
            error_code="crawl_task_crashed",
            error_detail=detail,
        )

    async def _finalize_queue_row(
        self,
        *,
        task_id: uuid.UUID,
        succeeded: bool,
        succeeded_artifact_id: uuid.UUID | None,
        should_retry: bool,
        retry_attempt: int,
        error_code: str,
        error_detail: str,
        retry_after_seconds: float | None,
    ) -> None:
        """Succeed / retry / fail the queue row OUTSIDE the evidence txn.

        Shared by the discover and analyze persist flows: a success acks with
        the immutable artifact id, a retryable failure re-queues with the
        deterministic backoff, and everything else fails terminally.
        """
        if succeeded:
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
                    retry_attempt, retry_after_seconds
                ),
                error_code=error_code,
                error_detail=error_detail,
            )
        else:
            await self._queue.fail(
                task_id=task_id,
                owner=self.owner,
                error_code=error_code,
                error_detail=error_detail,
            )

    # --- analyze flow ------------------------------------------------------

    async def _run_analyze(self, task_id: uuid.UUID, crawl_id: uuid.UUID) -> None:
        """Fetch + deep-analyze one monitored URL, persisting evidence atomically.

        Mirrors the discover flow: load config in one short session, fetch the
        URL through the SSRF-safe fetcher (heartbeating the lease), parse the
        bounded page facts, then persist ONE immutable artifact + attempt +
        page analysis + rule evaluations + issues + scores in a single
        transaction gated by a ``FOR UPDATE`` owner/liveness re-check. The queue
        row is succeeded / retried / failed OUTSIDE that transaction.
        """
        # If evidence committed but the out-of-transaction queue acknowledgement
        # failed, a reclaimed task must acknowledge that durable result instead
        # of fetching and attempting the unique inserts again.
        persisted_artifact_id = await self._persisted_analysis_artifact_id(task_id)
        if persisted_artifact_id is not None:
            await self._queue.succeed(
                task_id=task_id,
                owner=self.owner,
                result_artifact_id=persisted_artifact_id,
            )
            return

        async with self._session_factory() as session:
            task = await session.get(SiteCrawlTask, task_id)
            crawl = await session.get(SiteCrawl, crawl_id)
            if task is None or crawl is None:
                return
            guard = await self._evaluate_analyze_guard(
                session, task=task, crawl=crawl, lock=False
            )
            if not guard.ok:
                await session.rollback()
                await self._queue.cancel(task_id=task_id)
                return
            requested_url = task.requested_url
            config = dict(crawl.configuration or {})
            root_registrable_domain = config.get("root_registrable_domain") or ""

        heartbeat = asyncio.create_task(self._heartbeat_loop(task_id))
        try:
            outcome = await self._fetch_analyze(
                requested_url=requested_url,
                root_registrable_domain=root_registrable_domain,
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

        await self._persist_analyze(
            task_id=task_id,
            crawl_id=crawl_id,
            requested_url=requested_url,
            outcome=outcome,
        )

    async def _persisted_discover_artifact_id(
        self, task_id: uuid.UUID
    ) -> uuid.UUID | None:
        """Return durable discover evidence for an idempotently reclaimed task."""
        async with self._session_factory() as session:
            return await session.scalar(
                select(SiteFetchArtifact.id)
                .where(
                    SiteFetchArtifact.task_id == task_id,
                    SiteFetchArtifact.fetch_purpose == FETCH_PURPOSE_DISCOVER,
                )
                .limit(1)
            )

    async def _persisted_analysis_artifact_id(
        self, task_id: uuid.UUID
    ) -> uuid.UUID | None:
        """Return durable analyze evidence for an idempotently reclaimed task."""
        async with self._session_factory() as session:
            return await session.scalar(
                select(SiteFetchArtifact.id)
                .join(
                    SitePageAnalysis,
                    SitePageAnalysis.artifact_id == SiteFetchArtifact.id,
                )
                .where(
                    SiteFetchArtifact.task_id == task_id,
                    SiteFetchArtifact.fetch_purpose == FETCH_PURPOSE_ANALYZE,
                    SitePageAnalysis.status == PAGE_ANALYSIS_STATUS_COMPLETED,
                )
                .limit(1)
            )

    async def _persisted_link_check_done(self, task_id: uuid.UUID) -> bool:
        """Return True if this link-check task already persisted references.

        The presence of any ``SiteLinkReference`` row tagged with this task's
        ``target_task_id`` is the durable evidence that the task committed its
        probe results before the (possibly lost) queue acknowledgement — so a
        reclaimed run can ack the durable result instead of re-probing links.
        """
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(SiteLinkReference.id)
                .where(SiteLinkReference.target_task_id == task_id)
                .limit(1)
            )
            return existing is not None

    async def _evaluate_analyze_guard(
        self,
        session: AsyncSession,
        *,
        task: SiteCrawlTask,
        crawl: SiteCrawl,
        lock: bool,
    ):
        """Evaluate Task 4's live membership/entitlement guard from DB rows."""
        monitored_stmt = select(MonitoredSiteUrl).where(
            MonitoredSiteUrl.project_id == crawl.project_id,
            MonitoredSiteUrl.site_url_id == task.site_url_id,
        )
        entitlement_stmt = select(WorkspaceSiteHealthEntitlement).where(
            WorkspaceSiteHealthEntitlement.workspace_id == crawl.workspace_id
        )
        if lock:
            monitored_stmt = monitored_stmt.with_for_update()
            entitlement_stmt = entitlement_stmt.with_for_update()
        monitored = (await session.execute(monitored_stmt)).scalar_one_or_none()
        entitlement = (await session.execute(entitlement_stmt)).scalar_one_or_none()
        return evaluate_task_guard(
            crawl=crawl,
            task=task,
            monitored=monitored,
            entitlement=entitlement,
            owner=self.owner,
        )

    async def _lock_guarded_analyze_task(
        self,
        session: AsyncSession,
        *,
        task_id: uuid.UUID,
        crawl_id: uuid.UUID,
    ) -> tuple[tuple[SiteCrawlTask, SiteCrawl] | None, bool]:
        """Lock live entitlement/membership and the owned task before writes.

        The entitlement is the selection flow's serialization point, so lock it
        before membership/task rows to follow that flow's lock order and avoid
        deadlocks with a concurrent monitored-set replacement.

        Returns ``(locked_rows, guard_denied)``. ``guard_denied`` is true only
        while this worker still owns the task but live crawl/membership/
        entitlement state blocks analysis; a lost lease is not ours to cancel.
        """
        task_hint = await session.get(SiteCrawlTask, task_id)
        crawl_hint = await session.get(SiteCrawl, crawl_id)
        if task_hint is None or crawl_hint is None:
            return None, False

        entitlement = (
            await session.execute(
                select(WorkspaceSiteHealthEntitlement)
                .where(
                    WorkspaceSiteHealthEntitlement.workspace_id
                    == crawl_hint.workspace_id
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        monitored = (
            await session.execute(
                select(MonitoredSiteUrl)
                .where(
                    MonitoredSiteUrl.project_id == crawl_hint.project_id,
                    MonitoredSiteUrl.site_url_id == task_hint.site_url_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        crawl = await session.get(SiteCrawl, crawl_id, with_for_update=True)
        task = await session.get(SiteCrawlTask, task_id, with_for_update=True)
        decision = evaluate_task_guard(
            crawl=crawl,
            task=task,
            monitored=monitored,
            entitlement=entitlement,
            owner=self.owner,
        )
        if not decision.ok:
            still_owned = lease_is_owned(task, owner=self.owner)
            return None, still_owned
        if task is None or crawl is None:  # unreachable: guard checked both
            return None, False
        return (task, crawl), False

    async def _fetch_analyze(
        self,
        *,
        requested_url: str,
        root_registrable_domain: str,
    ) -> _AnalyzeOutcome:
        """Fetch + parse one monitored URL into a bounded ``_AnalyzeOutcome``.

        Returns parsed page facts on success (2xx), a classified error token on
        an HTTP 4xx/5xx or a ``FetchError``. Never raises for an expected fetch
        failure — the caller records an attempt row either way.
        """
        request = FetchRequest(
            url=requested_url,
            purpose=FETCH_PURPOSE_ANALYZE,
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
                    enforce_scope=False,
                )
        except FetchError as exc:
            latency = int((time.monotonic() - started) * 1000)
            return _AnalyzeOutcome(
                error_code=exc.error_code,
                error_detail=str(exc),
                retryable=exc.retryable,
                latency_ms=latency,
                status_code=exc.status_code,
                retry_after_seconds=exc.retry_after_seconds,
            )

        status = result.status_code
        classified = _classify_http_error(status)
        if classified is not None:
            error_code, retryable = classified
            return _AnalyzeOutcome(
                result=result,
                error_code=error_code,
                retryable=retryable,
                latency_ms=result.latency_ms,
                status_code=status,
            )

        facts = extract_page_facts(
            result.body,
            final_url=result.final_url or requested_url,
            content_type=result.content_type,
            charset=result.charset,
            status_code=status,
            redacted_headers=result.redacted_headers,
            http_version=result.http_version,
            ttfb_ms=result.ttfb_ms,
            latency_ms=result.latency_ms,
            wire_bytes=result.wire_bytes,
            decoded_bytes=result.decoded_bytes,
        )
        return _AnalyzeOutcome(
            result=result,
            facts=facts,
            status_code=status,
            latency_ms=result.latency_ms,
        )

    async def _persist_analyze(
        self,
        *,
        task_id: uuid.UUID,
        crawl_id: uuid.UUID,
        requested_url: str,
        outcome: _AnalyzeOutcome,
    ) -> None:
        """Persist the analyze result atomically, then finalize the queue row."""
        should_retry = False
        retry_attempt = 0
        succeeded_artifact_id: uuid.UUID | None = None
        guard_denied = False
        async with self._session_factory() as session:
            locked, guard_denied = await self._lock_guarded_analyze_task(
                session, task_id=task_id, crawl_id=crawl_id
            )
            if locked is None:
                await session.rollback()
                if not guard_denied:
                    return
            else:
                task, crawl = locked
                artifact_id: uuid.UUID | None = None
                if outcome.facts is not None and outcome.result is not None:
                    artifact_id = await self._write_artifact(
                        session,
                        crawl=crawl,
                        task=task,
                        result=outcome.result,
                        fetch_purpose=FETCH_PURPOSE_ANALYZE,
                        normalized_facts=outcome.facts,
                    )
                    await self._write_page_analysis(
                        session,
                        crawl=crawl,
                        task=task,
                        artifact_id=artifact_id,
                        facts=outcome.facts,
                    )
                    crawl.analyzed_url_count += 1
                    task.result_artifact_id = artifact_id
                    succeeded_artifact_id = artifact_id
                    # Automatically enqueue the link-check task for this URL in
                    # the same transaction as the completed analysis, so the
                    # worker's own ``TASK_KIND_LINK_CHECK`` handling is ever
                    # reached for a normal crawl. Conflict-safe (``ON CONFLICT
                    # DO NOTHING`` on the unique
                    # ``(crawl_id, task_kind, url_hash, generation)`` slot) so
                    # a reclaimed/retried analyze task never double-enqueues.
                    await _enqueue_discovery_task(
                        session,
                        crawl=crawl,
                        site_url_id=task.site_url_id,
                        url=requested_url,
                        url_hash_value=task.url_hash,
                        task_kind=TASK_KIND_LINK_CHECK,
                        depth=task.depth,
                        generation=task.generation,
                        parent_site_url_id=task.parent_site_url_id,
                    )
                    record_crawl_event(
                        session,
                        crawl_id=crawl_id,
                        event_type=EVENT_ANALYSIS_PROGRESS,
                        message="analysis progress",
                        payload={"analyzed": crawl.analyzed_url_count},
                        count_disclosure=_count_disclosure(crawl),
                    )
                else:
                    exhausted = task.attempt_count + 1 >= task.max_attempts
                    should_retry = outcome.retryable and not exhausted
                    retry_attempt = task.attempt_count + 1

                self._write_attempt(
                    session,
                    crawl=crawl,
                    task=task,
                    outcome=outcome,
                    succeeded=outcome.facts is not None,
                    requested_url=requested_url,
                    artifact_id=artifact_id,
                )
                task.attempt_count += 1
                await session.commit()

        if guard_denied:
            await self._queue.cancel(task_id=task_id)
            return

        await self._finalize_queue_row(
            task_id=task_id,
            succeeded=succeeded_artifact_id is not None,
            succeeded_artifact_id=succeeded_artifact_id,
            should_retry=should_retry,
            retry_attempt=retry_attempt,
            error_code=outcome.error_code,
            error_detail=outcome.error_detail,
            retry_after_seconds=outcome.retry_after_seconds,
        )

    async def _write_page_analysis(
        self,
        session: AsyncSession,
        *,
        crawl: SiteCrawl,
        task: SiteCrawlTask,
        artifact_id: uuid.UUID,
        facts: dict,
    ) -> uuid.UUID:
        """Create the page analysis + rule evaluations + issues + scores.

        One ``SitePageAnalysis`` (unique ``artifact_id``), one
        ``SiteRuleEvaluation`` per rule (unique ``(analysis_id, rule_id)``), a
        ``SiteIssue`` snapshot per FAIL (unique ``evaluation_id``), and the
        deterministic Technical/AEO/overall scores stamped with the versions.
        """
        site_url_id = await self._resolve_analysis_site_url_id(
            session, crawl=crawl, task=task
        )
        evaluations: list[RuleEvaluation] = evaluate_all(facts)
        scores = score_analysis(evaluations)
        analysis = SitePageAnalysis(
            workspace_id=crawl.workspace_id,
            project_id=crawl.project_id,
            crawl_id=crawl.id,
            site_url_id=site_url_id,
            artifact_id=artifact_id,
            status=PAGE_ANALYSIS_STATUS_COMPLETED,
            technical_score=scores.technical_score,
            aeo_score=scores.aeo_score,
            overall_score=scores.overall_score,
            analyzer_version=crawl.analyzer_version or ANALYZER_VERSION,
            scoring_version=crawl.scoring_version or SCORING_VERSION,
            source_artifact_ids=[artifact_id],
            finalized_at=_utcnow(),
        )
        session.add(analysis)
        await session.flush()

        evaluation_ids: list[uuid.UUID] = []
        for ev in evaluations:
            evaluation = SiteRuleEvaluation(
                workspace_id=crawl.workspace_id,
                analysis_id=analysis.id,
                source_artifact_id=artifact_id,
                rule_id=ev.rule_id,
                dimension=ev.dimension,
                category=ev.category,
                severity=ev.severity,
                weight=ev.weight,
                outcome=ev.outcome,
                evidence=ev.evidence,
                supporting_artifact_ids=[artifact_id],
                extractor_version=crawl.extractor_version or EXTRACTOR_VERSION,
                analyzer_version=crawl.analyzer_version or ANALYZER_VERSION,
                rule_version=ev.rule_version,
            )
            session.add(evaluation)
            await session.flush()
            evaluation_ids.append(evaluation.id)
            if ev.outcome == RULE_OUTCOME_FAIL:
                session.add(
                    SiteIssue(
                        workspace_id=crawl.workspace_id,
                        project_id=crawl.project_id,
                        crawl_id=crawl.id,
                        site_url_id=site_url_id,
                        analysis_id=analysis.id,
                        evaluation_id=evaluation.id,
                        source_artifact_id=artifact_id,
                        rule_id=ev.rule_id,
                        dimension=ev.dimension,
                        category=ev.category,
                        severity=ev.severity,
                        evidence=ev.evidence,
                        remediation=ev.remediation,
                        analyzer_version=crawl.analyzer_version or ANALYZER_VERSION,
                        rule_version=ev.rule_version,
                    )
                )
        analysis.source_evaluation_ids = evaluation_ids
        return analysis.id

    async def _resolve_analysis_site_url_id(
        self,
        session: AsyncSession,
        *,
        crawl: SiteCrawl,
        task: SiteCrawlTask,
    ) -> uuid.UUID:
        """Resolve the SiteUrl identity for an analyze task's URL.

        Prefers the task's own ``site_url_id`` (set at admission for monitored
        URLs); falls back to a lookup / conflict-safe create keyed on the
        canonical url hash so an analyze task never fails for a missing row.
        """
        if task.site_url_id is not None:
            return task.site_url_id
        resolved = await self._resolve_site_url_id(
            session, crawl=crawl, url=task.requested_url, depth=task.depth
        )
        if resolved is not None:
            return resolved
        # Last resort: create/lookup by hash directly (depth 0).
        fallback = await self._resolve_site_url_id(
            session, crawl=crawl, url=task.requested_url, depth=0
        )
        if fallback is None:
            # Only reachable when the URL cannot be canonicalized at all —
            # admission already canonicalized it, so treat as a hard bug.
            raise RuntimeError(
                f"could not resolve SiteUrl identity for {task.requested_url!r}"
            )
        return fallback

    # --- link-check flow ---------------------------------------------------

    async def _run_link_check(self, task_id: uuid.UUID, crawl_id: uuid.UUID) -> None:
        """Deduped HEAD-first + bounded GET-fallback link check for one page.

        Reads the source page's persisted analyze artifact facts, dedupes the
        referenced links (bounded by ``max_link_checks_per_page``), probes each
        HEAD-first with a bounded GET fallback (best-effort, offline-safe under
        test), and writes deduped ``SiteLinkReference`` rows. Independent of the
        discovery fast path. The queue row is always finalized.
        """
        # Durable-ack recovery (mirrors discover/analyze). Link references are
        # committed BEFORE the out-of-transaction ``_queue.succeed()``. If that
        # acknowledgement is lost (crash/restart between commit and ack) the
        # lease is reclaimed and this task re-runs. Without a durable check a
        # reclaimed run would re-probe every referenced link over the network —
        # wasteful and observable to third-party sites. If this task already
        # persisted its link references, acknowledge the durable result and
        # return before any network I/O instead of re-probing.
        if await self._persisted_link_check_done(task_id):
            await self._queue.succeed(task_id=task_id, owner=self.owner)
            return

        async with self._session_factory() as session:
            task = await session.get(SiteCrawlTask, task_id)
            crawl = await session.get(SiteCrawl, crawl_id)
            if task is None or crawl is None:
                return
            requested_url = task.requested_url
            source = await self._load_link_check_source(
                session, crawl=crawl, requested_url=requested_url
            )

        if source is None:
            # No source analysis/artifact to check against — nothing to do, but
            # the task still succeeds so the queue drains and reconcile runs.
            await self._queue.succeed(task_id=task_id, owner=self.owner)
            return

        analysis_id, artifact_id, source_final_url, facts = source
        targets = self._link_check_targets(facts, source_final_url=source_final_url)

        heartbeat = asyncio.create_task(self._heartbeat_loop(task_id))
        try:
            for target in targets:
                target["probe"] = await self._probe_link(target["url"])
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

        async with self._session_factory() as session:
            locked = await self._lock_owned_running_task(
                session, task_id=task_id, crawl_id=crawl_id
            )
            if locked is None:
                await session.rollback()
                return
            _task, crawl = locked
            for target in targets:
                await self._write_link_reference(
                    session,
                    crawl=crawl,
                    analysis_id=analysis_id,
                    artifact_id=artifact_id,
                    task_id=task_id,
                    target=target,
                )
            await session.commit()

        await self._queue.succeed(task_id=task_id, owner=self.owner)

    async def _load_link_check_source(
        self,
        session: AsyncSession,
        *,
        crawl: SiteCrawl,
        requested_url: str,
    ) -> tuple[uuid.UUID, uuid.UUID, str, dict] | None:
        """Find the latest analyze artifact + analysis + facts for the URL."""
        try:
            _canonical, url_hash_value = canonical_identity(requested_url)
        except Exception:
            return None
        site_url_id = await session.scalar(
            select(SiteUrl.id).where(
                SiteUrl.project_id == crawl.project_id,
                SiteUrl.url_hash == url_hash_value,
            )
        )
        if site_url_id is None:
            return None
        row = (
            await session.execute(
                select(SitePageAnalysis.id, SitePageAnalysis.artifact_id)
                .where(
                    SitePageAnalysis.crawl_id == crawl.id,
                    SitePageAnalysis.site_url_id == site_url_id,
                )
                .order_by(SitePageAnalysis.created_at.desc())
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        analysis_id, artifact_id = row
        artifact = await session.get(SiteFetchArtifact, artifact_id)
        if artifact is None:
            return None
        facts = dict(artifact.normalized_facts or {})
        return analysis_id, artifact_id, artifact.final_url, facts

    def _link_check_targets(self, facts: dict, *, source_final_url: str) -> list[dict]:
        """Return a bounded, deduped list of link targets from page facts.

        Deduplicates on ``(kind, target_hash)`` so a page linking the same URL
        twice checks it once, and caps at ``max_link_checks_per_page``.
        """
        links = facts.get("links") or {}
        collected: list[dict] = []
        seen: set[tuple[str, str]] = set()
        limit = site_health_settings.max_link_checks_per_page
        for kind in ("anchors", "images", "scripts", "stylesheets"):
            for entry in links.get(kind) or []:
                if len(collected) >= limit:
                    return collected
                raw_url = str(entry.get("url") or "").strip()
                if not raw_url:
                    continue
                url = urljoin(source_final_url, raw_url)
                target_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:64]
                entry_kind = str(entry.get("kind") or kind)
                key = (entry_kind, target_hash)
                if key in seen:
                    continue
                seen.add(key)
                collected.append(
                    {
                        "url": url,
                        "kind": entry_kind,
                        "target_hash": target_hash,
                        "is_internal": bool(entry.get("is_internal")),
                        "rel": str(entry.get("rel") or "")[:128],
                        "anchor_text": str(entry.get("anchor_text") or "")[:1024],
                    }
                )
        return collected

    async def _probe_link(self, url: str) -> _LinkProbeOutcome:
        """Best-effort HEAD-first + GET-fallback reachability probe.

        Returns method/status/reachability evidence. Never raises — link
        checking must not crash the task.
        """
        timeout = site_health_settings.link_check_timeout_seconds
        for method in ("HEAD", "GET"):
            request = FetchRequest(
                url=url,
                purpose=FETCH_PURPOSE_LINK_CHECK,
                method=method,
                timeout_seconds=timeout,
            )
            try:
                async with SecureFetcher(
                    resolver=self._resolver, transport=self._transport
                ) as fetcher:
                    result = await fetcher.fetch(request, enforce_scope=False)
            except FetchError:
                continue
            status = result.status_code
            if status in (405, 501) and method == "HEAD":
                # Method not allowed on HEAD: fall back to GET.
                continue
            return _LinkProbeOutcome(
                reachable=status < 400,
                method=method,
                status_code=status,
            )
        return _LinkProbeOutcome(
            reachable=False,
            method="GET",
            status_code=None,
        )

    async def _write_link_reference(
        self,
        session: AsyncSession,
        *,
        crawl: SiteCrawl,
        analysis_id: uuid.UUID,
        artifact_id: uuid.UUID,
        task_id: uuid.UUID,
        target: dict,
    ) -> None:
        """Write one deduped ``SiteLinkReference`` (ON CONFLICT DO NOTHING)."""
        probe: _LinkProbeOutcome = target["probe"]
        evidence_digest = hashlib.sha256(
            (
                f"{target['kind']}|{target['rel']}|{target['anchor_text']}|"
                f"{target['url']}|{probe.method}|{probe.status_code}|"
                f"reachable={probe.reachable}"
            ).encode()
        ).hexdigest()
        outcome_prefix = "reachable:" if probe.reachable else "unreachable:"
        fingerprint = outcome_prefix + evidence_digest[: 64 - len(outcome_prefix)]
        await session.execute(
            pg_insert(SiteLinkReference)
            .values(
                workspace_id=crawl.workspace_id,
                source_analysis_id=analysis_id,
                source_artifact_id=artifact_id,
                kind=target["kind"],
                target_url=target["url"][:2048],
                target_hash=target["target_hash"],
                is_internal=target["is_internal"],
                rel=target["rel"],
                anchor_text=target["anchor_text"],
                evidence_fingerprint=fingerprint,
                # Existing schema has no explicit status/reachability fields.
                # This is the task provenance for the probe; the evidence
                # fingerprint carries an observable outcome prefix and hashes
                # method/status evidence without overloading rel, anchor text,
                # kind, or another semantic field.
                target_task_id=task_id,
                analyzer_version=crawl.analyzer_version or ANALYZER_VERSION,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "source_artifact_id",
                    "kind",
                    "target_hash",
                    "evidence_fingerprint",
                ]
            )
        )

    # --- shared reconcile --------------------------------------------------

    async def _reconcile_crawl_status(self, crawl_id: uuid.UUID) -> None:
        """Reconcile the crawl's overall status from discovery AND analysis.

        The single shared finalize for every task kind. It:
          - terminalizes the DISCOVERY sub-state once discover tasks drain
            (progressively, even while analyze/link_check work remains);
          - drives the independent ANALYSIS lifecycle (pending -> running ->
            completed/partially_completed/failed) from the analyze task
            outcomes;
          - terminalizes the OVERALL crawl ONLY when EVERY non-terminal task of
            ALL kinds is drained, classifying completed / partially_completed /
            failed and (on analysis terminalization) persisting the aggregate
            ``SiteHealthSnapshot`` + a ``crawl.completed`` event.

        Keeping the crawl row ``FOR UPDATE`` and terminalizing exactly once (a
        completed crawl short-circuits) is what prevents a late analyze finalize
        from calling ``apply_crawl_status`` out of a terminal state (which would
        raise ``InvalidSiteCrawlTransition`` — all terminal states are empty
        sets in the transition tables).
        """
        async with self._session_factory() as session:
            crawl = await session.get(SiteCrawl, crawl_id, with_for_update=True)
            if crawl is None or not crawl_is_active(crawl):
                if crawl is not None:
                    await session.rollback()
                return

            counts = await self._task_counts(session, crawl_id)
            discover_remaining = counts["discover_non_terminal"]
            analyze_remaining = counts["analyze_non_terminal"]
            link_remaining = counts["link_non_terminal"]
            analyze_total = counts["analyze_total"]
            analyze_succeeded = counts["analyze_succeeded"]
            analyze_cancelled = counts["analyze_cancelled"]
            analyze_applicable = analyze_total - analyze_cancelled

            # Discovery sub-state: terminalize progressively once discover
            # tasks drain, independent of analyze/link_check work.
            fully_failed = crawl.discovered_url_count == 0
            discovery_partial = (
                crawl.discovered_url_count > 0 and crawl.failed_url_count > 0
            )
            if discover_remaining == 0:
                if crawl.discovery_status == DISCOVERY_STATUS_RUNNING:
                    if fully_failed:
                        apply_discovery_status(crawl, DISCOVERY_STATUS_FAILED)
                    else:
                        apply_discovery_status(crawl, DISCOVERY_STATUS_COMPLETED)
                crawl.inventory_complete = not fully_failed

            # Analysis lifecycle: move pending -> running once any analyze task
            # exists (work has been admitted), so a later terminal transition
            # is legal.
            if analyze_total > 0 and crawl.analysis_status == ANALYSIS_STATUS_PENDING:
                apply_analysis_status(crawl, ANALYSIS_STATUS_RUNNING)

            all_drained = (
                discover_remaining == 0
                and analyze_remaining == 0
                and link_remaining == 0
            )
            if not all_drained:
                await session.commit()
                return

            # Every task of every kind is terminal: terminalize analysis + the
            # overall crawl exactly once.
            analysis_terminalized = False
            if analyze_total == 0 and crawl.analysis_status == ANALYSIS_STATUS_PENDING:
                # An empty analysis plan is a successful, terminal lifecycle,
                # not a crawl left permanently "pending". Traverse the legal
                # state machine and persist the corresponding empty snapshot.
                apply_analysis_status(crawl, ANALYSIS_STATUS_RUNNING)
            if crawl.analysis_status == ANALYSIS_STATUS_RUNNING:
                if analyze_total > 0 and analyze_applicable == 0:
                    apply_analysis_status(crawl, ANALYSIS_STATUS_CANCELLED)
                elif analyze_succeeded == analyze_applicable:
                    apply_analysis_status(crawl, ANALYSIS_STATUS_COMPLETED)
                elif analyze_succeeded > 0:
                    apply_analysis_status(crawl, ANALYSIS_STATUS_PARTIALLY_COMPLETED)
                else:
                    apply_analysis_status(crawl, ANALYSIS_STATUS_FAILED)
                analysis_terminalized = True

            if analysis_terminalized:
                await self._persist_snapshot(session, crawl=crawl)

            if crawl.status == CRAWL_STATUS_RUNNING:
                crawl.completed_at = _utcnow()
                if fully_failed:
                    apply_crawl_status(crawl, CRAWL_STATUS_FAILED)
                elif discovery_partial or (
                    analyze_applicable > 0 and analyze_succeeded < analyze_applicable
                ):
                    apply_crawl_status(crawl, CRAWL_STATUS_PARTIALLY_COMPLETED)
                else:
                    apply_crawl_status(crawl, CRAWL_STATUS_COMPLETED)
                record_crawl_event(
                    session,
                    crawl_id=crawl_id,
                    event_type=EVENT_CRAWL_COMPLETED,
                    message="crawl completed",
                    payload={"status": crawl.status},
                    count_disclosure=_count_disclosure(crawl),
                )
            await session.commit()

    async def _task_counts(
        self, session: AsyncSession, crawl_id: uuid.UUID
    ) -> dict[str, int]:
        """Aggregate per-kind terminal/non-terminal task counts for a crawl."""

        async def _non_terminal(kind: str) -> int:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(SiteCrawlTask)
                    .where(SiteCrawlTask.crawl_id == crawl_id)
                    .where(SiteCrawlTask.task_kind == kind)
                    .where(SiteCrawlTask.status.not_in(list(TASK_TERMINAL_STATUSES)))
                )
                or 0
            )

        analyze_total = int(
            await session.scalar(
                select(func.count())
                .select_from(SiteCrawlTask)
                .where(SiteCrawlTask.crawl_id == crawl_id)
                .where(SiteCrawlTask.task_kind == TASK_KIND_ANALYZE)
            )
            or 0
        )
        analyze_succeeded = int(
            await session.scalar(
                select(func.count())
                .select_from(SiteCrawlTask)
                .where(SiteCrawlTask.crawl_id == crawl_id)
                .where(SiteCrawlTask.task_kind == TASK_KIND_ANALYZE)
                .where(SiteCrawlTask.status == TASK_STATUS_SUCCEEDED)
            )
            or 0
        )
        analyze_cancelled = int(
            await session.scalar(
                select(func.count())
                .select_from(SiteCrawlTask)
                .where(SiteCrawlTask.crawl_id == crawl_id)
                .where(SiteCrawlTask.task_kind == TASK_KIND_ANALYZE)
                .where(SiteCrawlTask.status == TASK_STATUS_CANCELLED)
            )
            or 0
        )
        return {
            "discover_non_terminal": await _non_terminal(TASK_KIND_DISCOVER),
            "analyze_non_terminal": await _non_terminal(TASK_KIND_ANALYZE),
            "link_non_terminal": await _non_terminal(TASK_KIND_LINK_CHECK),
            "analyze_total": analyze_total,
            "analyze_succeeded": analyze_succeeded,
            "analyze_cancelled": analyze_cancelled,
        }

    async def _persist_snapshot(
        self, session: AsyncSession, *, crawl: SiteCrawl
    ) -> None:
        """Compute + persist the crawl aggregate snapshot (unique per crawl).

        Aggregates only the LATEST completed analyses for ACTIVE monitored URLs
        (ignoring missing/errored URLs — never a fabricated zero), rolls up the
        issue severity/category counts, and writes both the immutable
        ``SiteHealthSnapshot`` and the crawl's rolled-up ``score_summary``.
        """
        # Exactly one latest completed analysis per ACTIVE monitored URL in
        # this crawl. Rank by the full timestamp, then UUID for a deterministic
        # tie-break (never truncate timestamps to whole seconds).
        ranked = (
            select(
                SitePageAnalysis.id.label("id"),
                SitePageAnalysis.site_url_id.label("site_url_id"),
                SitePageAnalysis.artifact_id.label("artifact_id"),
                SitePageAnalysis.technical_score.label("technical_score"),
                SitePageAnalysis.aeo_score.label("aeo_score"),
                SitePageAnalysis.overall_score.label("overall_score"),
                func.row_number()
                .over(
                    partition_by=SitePageAnalysis.site_url_id,
                    order_by=(
                        SitePageAnalysis.created_at.desc(),
                        SitePageAnalysis.id.desc(),
                    ),
                )
                .label("latest_rank"),
            )
            .join(
                MonitoredSiteUrl,
                MonitoredSiteUrl.site_url_id == SitePageAnalysis.site_url_id,
            )
            .where(
                SitePageAnalysis.crawl_id == crawl.id,
                SitePageAnalysis.status == PAGE_ANALYSIS_STATUS_COMPLETED,
                MonitoredSiteUrl.project_id == crawl.project_id,
                MonitoredSiteUrl.active.is_(True),
            )
            .subquery()
        )
        rows = (
            await session.execute(
                select(
                    ranked.c.id,
                    ranked.c.site_url_id,
                    ranked.c.artifact_id,
                    ranked.c.technical_score,
                    ranked.c.aeo_score,
                    ranked.c.overall_score,
                ).where(ranked.c.latest_rank == 1)
            )
        ).all()

        inputs: list[AnalysisScoreInput] = []
        analysis_ids: list[uuid.UUID] = []
        artifact_ids: list[uuid.UUID] = []
        for row in rows:
            analysis_ids.append(row.id)
            artifact_ids.append(row.artifact_id)
            inputs.append(
                AnalysisScoreInput(
                    url_key=str(row.site_url_id),
                    ordinal=0,
                    technical_score=row.technical_score,
                    aeo_score=row.aeo_score,
                    overall_score=row.overall_score,
                )
            )
        aggregate = aggregate_scores(inputs)

        # Issue severity/category rollups for this crawl.
        severity_counts: dict[str, int] = {}
        category_counts: dict[str, int] = {}
        issue_total = 0
        evaluation_ids: list[uuid.UUID] = []
        issue_rows: Sequence[Row[tuple[str, str, uuid.UUID]]] = []
        if analysis_ids:
            issue_rows = (
                await session.execute(
                    select(
                        SiteIssue.severity,
                        SiteIssue.category,
                        SiteIssue.evaluation_id,
                    ).where(SiteIssue.analysis_id.in_(analysis_ids))
                )
            ).all()
        for severity, category, evaluation_id in issue_rows:
            issue_total += 1
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
            category_counts[category] = category_counts.get(category, 0) + 1
            evaluation_ids.append(evaluation_id)

        selected_url_count = int(
            await session.scalar(
                select(func.count())
                .select_from(MonitoredSiteUrl)
                .where(
                    MonitoredSiteUrl.project_id == crawl.project_id,
                    MonitoredSiteUrl.active.is_(True),
                )
            )
            or 0
        )

        session.add(
            SiteHealthSnapshot(
                workspace_id=crawl.workspace_id,
                project_id=crawl.project_id,
                crawl_id=crawl.id,
                selected_url_count=selected_url_count,
                analyzed_url_count=aggregate.analyzed_url_count,
                technical_score=aggregate.technical_score,
                aeo_score=aggregate.aeo_score,
                overall_score=aggregate.overall_score,
                issue_count=issue_total,
                severity_counts=severity_counts,
                category_counts=category_counts,
                source_analysis_ids=analysis_ids,
                source_artifact_ids=artifact_ids,
                source_evaluation_ids=evaluation_ids,
                analyzer_version=crawl.analyzer_version or ANALYZER_VERSION,
                scoring_version=crawl.scoring_version or SCORING_VERSION,
            )
        )
        crawl.score_summary = {
            "technical_score": aggregate.technical_score,
            "aeo_score": aggregate.aeo_score,
            "overall_score": aggregate.overall_score,
            "analyzed_url_count": aggregate.analyzed_url_count,
            "selected_count": selected_url_count,
            "issue_count": issue_total,
            "scoring_version": aggregate.scoring_version,
        }


def main() -> None:  # pragma: no cover - process entrypoint
    configure_logging()
    worker = SiteHealthWorker()
    asyncio.run(worker.run_forever())


if __name__ == "__main__":  # pragma: no cover
    main()
