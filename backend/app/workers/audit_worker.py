# Audit worker: the Postgres-queue claim/lease execution loop (invariant 8).
#
# A separate process (the ``worker`` compose service). It claims ``AuditTask``
# rows via ``PostgresTaskQueue`` (``FOR UPDATE SKIP LOCKED``, lease committed
# BEFORE any network I/O), resolves the decrypted BYOK key from the task's
# ``ProviderConnection`` at execution time (never env, never logged — invariant
# 6), builds the answer-engine adapter, and calls it with request pacing, a hard
# per-call ceiling, and a bounded retry budget. Each attempt appends an
# immutable ``ProviderAttempt``; a successful call persists an immutable
# ``RawResponseArtifact`` plus the task's execution fields (single writer = the
# claiming worker — invariant 3). It heartbeats the lease while a call runs,
# drives the audit lifecycle (QUEUED -> RUNNING, then RUNNING -> ANALYZING /
# FAILED at the execution boundary), and honors cooperative cancel + the per-run
# wall-clock deadline at each task boundary (invariant 9).
#
# Scoring/analysis is B6's job: this worker persists the raw answer + citations
# and hands a finished-execution audit off at ``analyzing``.
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analysis.service import (
    analyze_task,
    build_scoring_config,
    finalize_audit_analysis,
)
from app.connectors.answer_engines.contracts import (
    AnswerEngineRequest,
    AnswerEngineResponse,
)
from app.connectors.answer_engines.errors import ProviderError
from app.connectors.answer_engines.factory import build_adapter
from app.core.config.audits import (
    ATTEMPT_STATUS_FAILED,
    ATTEMPT_STATUS_SUCCEEDED,
    AUDIT_QUEUE_SPEC,
    AUDIT_STATUS_ANALYZING,
    AUDIT_STATUS_CANCELLED,
    AUDIT_STATUS_FAILED,
    AUDIT_STATUS_QUEUED,
    AUDIT_STATUS_RUNNING,
    AUDIT_TERMINAL_STATUSES,
    ERROR_NO_CONNECTION,
    ERROR_RUN_DEADLINE,
    EVENT_AUDIT_RUNNING,
    EVENT_TASK_FAILED,
    EVENT_TASK_RETRY,
    EVENT_TASK_SUCCEEDED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCEEDED,
    TASK_TERMINAL_STATUSES,
    audit_settings,
)
from app.core.config.provider_catalog import (
    ERROR_INVALID_SURFACE,
    ERROR_PARSE,
    ERROR_TIMEOUT,
    RETRYABLE_ERRORS,
    is_active_transport,
)
from app.core.database import SessionLocal
from app.core.security import decrypt_secret
from app.core.telemetry import configure_logging
from app.domain.audits.state_events import apply_transition, record_event
from app.models.audit import (
    Audit,
    AuditEngineSnapshot,
    AuditTask,
    ProviderAttempt,
    RawResponseArtifact,
)
from app.models.provider import ProviderConnection
from app.orchestration.postgres_task_queue import PostgresTaskQueue

logger = logging.getLogger("app.workers.audit_worker")


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- Request pacing (per transport, across concurrent tasks in this process) --
_provider_pacing_locks: dict[str, asyncio.Lock] = {}
_provider_last_request_started: dict[str, float] = {}


async def pace_provider_request(transport_provider: str) -> None:
    """Space provider request starts per transport to respect rate limits.

    Mainly protects Gemini's low per-minute quota. A no-op when
    ``min_request_interval_seconds`` is 0 (the default).
    """
    interval = max(0.0, audit_settings.min_request_interval_seconds)
    if interval <= 0:
        return
    lock = _provider_pacing_locks.setdefault(transport_provider, asyncio.Lock())
    async with lock:
        last_started = _provider_last_request_started.get(transport_provider)
        if last_started is not None:
            remaining = interval - (time.monotonic() - last_started)
            if remaining > 0:
                await asyncio.sleep(remaining)
        _provider_last_request_started[transport_provider] = time.monotonic()


@dataclass
class CallAttempt:
    """The outcome of ONE actual provider call (a success or a failure).

    ``_call_with_retries`` returns one of these per real call it made so the
    caller can persist an append-only ``ProviderAttempt`` row for EACH attempt
    (retryable failures + the final success/failure), not just the last one.
    """

    response: AnswerEngineResponse | None
    error: ProviderError | None

    @property
    def succeeded(self) -> bool:
        return self.response is not None


async def _call_with_retries(
    adapter, request: AnswerEngineRequest
) -> list[CallAttempt]:
    """Call the provider with pacing, a hard per-call ceiling, and retries.

    Returns one ``CallAttempt`` per actual provider call made (never empty). The
    last entry is the terminal outcome: a success (``response`` set) or the final
    failure once the retry budget is spent / a non-retryable error is hit.
    Earlier entries are retryable failures. A single call can never run past
    ``max_call_seconds`` (``asyncio.wait_for`` guards the HTTP client); the loop
    is bounded by ``max_attempts``.
    """
    attempts = max(1, audit_settings.max_attempts)
    results: list[CallAttempt] = []
    for attempt in range(attempts):
        error: ProviderError | None = None
        try:
            await pace_provider_request(adapter.transport_provider)
            # Hard per-call ceiling independent of the HTTP client timeout: a
            # stalled call (hung socket, redirect loop) can never run past this.
            response = await asyncio.wait_for(
                adapter.execute(request),
                timeout=audit_settings.max_call_seconds,
            )
            results.append(CallAttempt(response=response, error=None))
            return results
        except TimeoutError:
            error = ProviderError(
                "provider call exceeded max_call_seconds "
                f"({audit_settings.max_call_seconds}s)",
                error_code=ERROR_TIMEOUT,
                retryable=True,
            )
        except ProviderError as exc:
            error = exc
        results.append(CallAttempt(response=None, error=error))
        retryable = bool(
            error and error.retryable and error.error_code in RETRYABLE_ERRORS
        )
        if not retryable or attempt == attempts - 1:
            break
        retry_after = getattr(error, "retry_after_seconds", None)
        await asyncio.sleep(audit_settings.retry_delay(attempt, retry_after))
    return results


def _build_request_snapshot(
    *,
    logical_engine: str,
    transport_provider: str,
    transport_model: str,
    request: AnswerEngineRequest,
    configuration: dict,
) -> dict:
    """What determined the request. Proves statelessness; never the key/brand.

    Records the visible prompt, the resolved model + provenance triple, the
    neutral system instruction, and locale — enough to reproduce the call. The
    brand/competitor list and the API key are intentionally excluded
    (invariant 6).
    """
    return {
        "logical_engine": logical_engine,
        "transport_provider": transport_provider,
        "transport_model": transport_model,
        "model": request.model,
        "prompt": request.prompt,
        "system_instruction": request.system_instruction,
        "stateless": True,
        "benchmark_mode": configuration.get("benchmark_mode", ""),
        "country_code": configuration.get("country_code", ""),
        "language_code": configuration.get("language_code", ""),
    }


def _serialize_search_events(response: AnswerEngineResponse) -> list[dict]:
    return [
        {
            "sequence": event.sequence,
            "query": event.query,
            "call_id": event.call_id,
            "call_sequence": event.call_sequence,
            "query_sequence": event.query_sequence,
        }
        for event in response.search_events
    ]


def _serialize_citations(response: AnswerEngineResponse) -> list[dict]:
    """One row per distinct source URL (collapse per-span duplicates).

    Grounded providers cite the same source once per supported text span; the
    UI and later scoring want one row per distinct source. Keeps the first
    occurrence and re-numbers ``ordinal`` densely. Scoring/classification is
    deferred to B6.
    """
    seen: set = set()
    deduped: list[dict] = []
    for citation in response.citations:
        url = str(citation.url or "").strip()
        key = url or (citation.domain, citation.title)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(
            {
                "ordinal": len(deduped),
                "url": citation.url,
                "domain": citation.domain,
                "title": citation.title,
                "start_index": citation.start_index,
                "end_index": citation.end_index,
                "cited_text": citation.cited_text,
            }
        )
    return deduped


class AuditWorker:
    """Owns a claim/lease loop against ``PostgresTaskQueue``.

    A single worker claims up to ``worker_concurrency`` tasks per poll and runs
    them serially inside its loop (each in its own short-lived session). Sharing
    an async session across concurrent tasks corrupts session state, so a worker
    never holds one open across a provider call.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        owner: str | None = None,
    ) -> None:
        self._session_factory = session_factory or SessionLocal
        self._queue = PostgresTaskQueue(self._session_factory, AUDIT_QUEUE_SPEC)
        self.owner = owner or f"worker-{uuid.uuid4().hex[:12]}"

    async def run_once(self) -> int:
        """Sweep expired leases, claim a batch, execute it. Returns count run."""
        await self._queue.release_expired()
        tasks = await self._queue.claim(
            owner=self.owner,
            limit=max(1, audit_settings.worker_concurrency),
        )
        for task in tasks:
            await self._execute_task(task)
        return len(tasks)

    async def run_until_idle(self, *, max_batches: int = 1000) -> int:
        """Drain the queue until a claim returns nothing (test/one-shot mode)."""
        total = 0
        for _ in range(max_batches):
            ran = await self.run_once()
            if ran == 0:
                break
            total += ran
        return total

    async def run_forever(self) -> None:  # pragma: no cover - long-running loop
        logger.info("audit worker started", extra={"owner": self.owner})
        while True:
            try:
                ran = await self.run_once()
            except Exception:  # defensive: a bad task must not kill the loop
                logger.exception("audit worker loop iteration failed")
                ran = 0
            if ran == 0:
                await asyncio.sleep(max(0.05, audit_settings.poll_interval_seconds))

    # --- per-task execution ------------------------------------------------

    async def _execute_task(self, claimed: AuditTask) -> None:
        """Run one claimed task end to end inside its own session.

        Honors cooperative cancel + the per-run wall-clock deadline at the
        boundary (before touching the provider). Persists the immutable artifact
        + attempt and finalizes the task through the queue so the lease is always
        released. Never raises — a crash is caught and recorded as a failure.
        """
        task_id = claimed.id
        audit_id = claimed.audit_id
        try:
            async with self._session_factory() as session:
                task = await session.get(AuditTask, task_id)
                if task is None:
                    return
                audit = await session.get(Audit, audit_id)
                if audit is None:
                    return

                # Cooperative cancel: stop at this boundary if the audit was
                # killed since the claim, rather than hitting the provider.
                if audit.status == AUDIT_STATUS_CANCELLED:
                    await session.rollback()
                    await self._queue.cancel(task_id=task_id)
                    return

                # Per-run wall-clock deadline: once the audit has been running
                # longer than max_run_seconds, terminalize remaining tasks
                # instead of starting another provider call.
                if self._deadline_passed(audit):
                    await session.rollback()
                    await self._queue.fail(
                        task_id=task_id,
                        owner=self.owner,
                        error_code=ERROR_RUN_DEADLINE,
                        error_detail=(
                            "audit exceeded max_run_seconds "
                            f"({audit_settings.max_run_seconds}s)"
                        ),
                    )
                    await self._finalize_audit(audit_id)
                    return

                # First task moves the audit QUEUED -> RUNNING.
                self._ensure_running(session, audit)
                await session.commit()

            # Mark the queue row running (still owned) before the network call.
            if not await self._queue.mark_running(task_id=task_id, owner=self.owner):
                # Lease lost (sweeper reclaimed it); another worker will retry.
                return

            await self._run_provider_call(task_id, audit_id)
        except Exception as exc:  # defensive: never let one task kill the loop
            logger.exception("audit task crashed", extra={"task_id": str(task_id)})
            await self._record_crash(task_id, audit_id, exc)
        finally:
            await self._finalize_audit(audit_id)

    def _deadline_passed(self, audit: Audit) -> bool:
        started = audit.started_at
        if started is None:
            return False
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        elapsed = (_utcnow() - started).total_seconds()
        return elapsed >= audit_settings.max_run_seconds

    def _ensure_running(self, session: AsyncSession, audit: Audit) -> None:
        if audit.status == AUDIT_STATUS_QUEUED:
            audit.started_at = _utcnow()
            apply_transition(
                session,
                audit=audit,
                target=AUDIT_STATUS_RUNNING,
                message="audit running",
            )
            record_event(
                session,
                audit_id=audit.id,
                event_type=EVENT_AUDIT_RUNNING,
                message="audit running",
            )

    async def _run_provider_call(self, task_id: uuid.UUID, audit_id: uuid.UUID) -> None:
        # Load everything the call needs in one short session, then close it
        # before the (long) network call so no txn is held across provider I/O.
        async with self._session_factory() as session:
            task = await session.get(AuditTask, task_id)
            audit = await session.get(Audit, audit_id)
            if task is None or audit is None:
                return
            snapshot = await session.get(AuditEngineSnapshot, task.engine_snapshot_id)
            connection: ProviderConnection | None = None
            if snapshot is not None and snapshot.connection_id is not None:
                connection = await session.get(
                    ProviderConnection, snapshot.connection_id
                )
            configuration = dict(audit.configuration or {})
            system_instruction = audit.system_instruction or ""
            logical_engine = task.logical_engine
            transport_provider = task.transport_provider
            transport_model = task.transport_model
            prompt_text = task.prompt_text or ""
            base_url = snapshot.base_url if snapshot is not None else ""

        # A frozen task on a retired transport (e.g. a legacy OpenRouter task
        # persisted before the v2 retirement) fails terminally BEFORE the
        # connection-activity check, key decryption, or any network I/O — no
        # provider attempt, no external call, no raw artifact (invariant 6/10).
        if not is_active_transport(transport_provider):
            await self._fail_terminal(
                task_id=task_id,
                audit_id=audit_id,
                logical_engine=logical_engine,
                transport_provider=transport_provider,
                transport_model=transport_model,
                error_code=ERROR_INVALID_SURFACE,
                error_detail="transport provider is retired and not executable",
            )
            return

        # A missing/inactive connection is a terminal misconfiguration.
        if connection is None or not connection.active:
            await self._fail_terminal(
                task_id=task_id,
                audit_id=audit_id,
                logical_engine=logical_engine,
                transport_provider=transport_provider,
                transport_model=transport_model,
                error_code=ERROR_NO_CONNECTION,
                error_detail="provider connection missing or inactive",
            )
            return

        # Resolve the BYOK key at execution time. Never logged/persisted.
        api_key = decrypt_secret(connection.api_key_encrypted)
        request = AnswerEngineRequest(
            prompt=prompt_text,
            system_instruction=system_instruction,
            model=transport_model,
            timeout_seconds=audit_settings.request_timeout_seconds,
        )
        request_snapshot = _build_request_snapshot(
            logical_engine=logical_engine,
            transport_provider=transport_provider,
            transport_model=transport_model,
            request=request,
            configuration=configuration,
        )

        try:
            adapter = build_adapter(
                logical_engine=logical_engine,
                transport_provider=transport_provider,
                api_key=api_key,
                country_code=str(configuration.get("country_code", "")),
                base_url=base_url,
            )
        except ProviderError as exc:
            await self._fail_terminal(
                task_id=task_id,
                audit_id=audit_id,
                logical_engine=logical_engine,
                transport_provider=transport_provider,
                transport_model=transport_model,
                error_code=exc.error_code,
                error_detail=str(exc),
                request_snapshot=request_snapshot,
            )
            return

        # Heartbeat the lease while the (possibly slow) call runs.
        heartbeat = asyncio.create_task(self._heartbeat_loop(task_id))
        try:
            attempts = await _call_with_retries(adapter, request)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

        final = attempts[-1]
        if final.succeeded:
            await self._persist_success(
                task_id=task_id,
                audit_id=audit_id,
                attempts=attempts,
                logical_engine=logical_engine,
                transport_provider=transport_provider,
                transport_model=transport_model,
                request_snapshot=request_snapshot,
            )
        else:
            await self._handle_failure(
                task_id=task_id,
                audit_id=audit_id,
                attempts=attempts,
                logical_engine=logical_engine,
                transport_provider=transport_provider,
                transport_model=transport_model,
                request_snapshot=request_snapshot,
            )

    async def _heartbeat_loop(
        self, task_id: uuid.UUID
    ) -> None:  # pragma: no cover - timing loop
        interval = max(1.0, audit_settings.heartbeat_interval_seconds)
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
        audit_id: uuid.UUID,
    ) -> tuple[AuditTask, Audit] | None:
        """Lock the task FOR UPDATE and verify we still own it before writing.

        Guards invariant 3/8 (single writer / no double-claim). Between the
        provider call finishing and this write, the lease could have expired
        (sweeper -> another worker claimed it) or the audit could have been
        cancelled. Returns ``(task, audit)`` only when the task is still leased
        to THIS worker, still ``running``, and the audit is not cancelled or
        terminal; otherwise ``None`` and the stale provider result is discarded.
        """
        task = await session.get(AuditTask, task_id, with_for_update=True)
        if task is None:
            return None
        if task.lease_owner != self.owner or task.status != TASK_STATUS_RUNNING:
            return None
        audit = await session.get(Audit, audit_id)
        if (
            audit is None
            or audit.status == AUDIT_STATUS_CANCELLED
            or (audit.status in AUDIT_TERMINAL_STATUSES)
        ):
            return None
        return task, audit

    def _record_attempts(
        self,
        session: AsyncSession,
        *,
        task: AuditTask,
        audit_id: uuid.UUID,
        attempts: list[CallAttempt],
        logical_engine: str,
        transport_provider: str,
        transport_model: str,
        artifact_id: uuid.UUID | None,
    ) -> None:
        """Append one immutable ProviderAttempt per actual provider call.

        ProviderAttempt is append-only "one row per attempt" (invariant 3): a
        run that retried twice then succeeded records three rows (two failed +
        one succeeded), not a single collapsed row. Advances ``attempt_count``
        by the number of calls made and stamps each row's ``attempt_number``.
        """
        base = task.attempt_count
        for offset, attempt in enumerate(attempts, start=1):
            attempt_number = base + offset
            if attempt.succeeded:
                response = attempt.response
                session.add(
                    ProviderAttempt(
                        task_id=task.id,
                        audit_id=audit_id,
                        attempt_number=attempt_number,
                        logical_engine=response.logical_engine,
                        transport_provider=response.transport_provider,
                        transport_model=response.transport_model,
                        status=ATTEMPT_STATUS_SUCCEEDED,
                        latency_ms=response.latency_ms,
                        artifact_id=artifact_id,
                    )
                )
            else:
                error = attempt.error
                error_code = error.error_code if error else ERROR_PARSE
                error_detail = str(error) if error else "unknown provider error"
                session.add(
                    ProviderAttempt(
                        task_id=task.id,
                        audit_id=audit_id,
                        attempt_number=attempt_number,
                        logical_engine=logical_engine,
                        transport_provider=transport_provider,
                        transport_model=transport_model,
                        status=ATTEMPT_STATUS_FAILED,
                        error_code=error_code,
                        error_detail=error_detail[:2000],
                    )
                )
        task.attempt_count = base + len(attempts)

    async def _persist_success(
        self,
        *,
        task_id: uuid.UUID,
        audit_id: uuid.UUID,
        attempts: list[CallAttempt],
        logical_engine: str,
        transport_provider: str,
        transport_model: str,
        request_snapshot: dict,
    ) -> None:
        response = attempts[-1].response
        assert response is not None  # caller only invokes on a success
        search_events = _serialize_search_events(response)
        citations = _serialize_citations(response)
        artifact_id: uuid.UUID | None = None
        async with self._session_factory() as session:
            # Owner + liveness check under a row lock BEFORE writing any evidence
            # (invariant 3/8). If the lease was lost or the audit cancelled, the
            # provider response is discarded — no artifact/attempt/analysis.
            locked = await self._lock_owned_running_task(
                session, task_id=task_id, audit_id=audit_id
            )
            if locked is None:
                await session.rollback()
                return
            task, audit = locked
            # Immutable raw artifact (invariant 3): written once, never mutated.
            artifact = RawResponseArtifact(
                audit_id=audit_id,
                task_id=task_id,
                logical_engine=response.logical_engine,
                transport_provider=response.transport_provider,
                transport_model=response.transport_model,
                answer_text=response.answer_text,
                search_used=response.search_used,
                search_events=search_events,
                citations=citations,
                provider_metadata=dict(response.provider_metadata),
                usage=dict(response.usage),
                latency_ms=response.latency_ms,
            )
            session.add(artifact)
            await session.flush()
            artifact_id = artifact.id

            task.answer_text = response.answer_text
            task.search_used = response.search_used
            task.search_events = search_events
            task.citations = citations
            task.result_artifact_id = artifact_id
            task.request_snapshot = request_snapshot
            task.provider_metadata = dict(response.provider_metadata)
            task.latency_ms = response.latency_ms
            task.error_code = ""
            task.error_detail = ""

            # Score on persist (invariants 4/9): the deterministic analyzer runs
            # against the just-persisted answer + citations (no provider call)
            # and writes the derived ResponseAnalysis + mention/citation rows,
            # each stamped with the raw-artifact provenance + analyzer_version.
            config = build_scoring_config(audit.configuration)
            analysis = await analyze_task(session, task=task, config=config)
            if analysis is not None:
                task.score = analysis.score

            # One ProviderAttempt per actual call (retries + final success).
            self._record_attempts(
                session,
                task=task,
                audit_id=audit_id,
                attempts=attempts,
                logical_engine=logical_engine,
                transport_provider=transport_provider,
                transport_model=transport_model,
                artifact_id=artifact_id,
            )
            record_event(
                session,
                audit_id=audit_id,
                event_type=EVENT_TASK_SUCCEEDED,
                message="task succeeded",
                payload={"task_id": str(task_id)},
            )
            await session.commit()

        await self._queue.succeed(
            task_id=task_id, owner=self.owner, result_artifact_id=artifact_id
        )

    async def _handle_failure(
        self,
        *,
        task_id: uuid.UUID,
        audit_id: uuid.UUID,
        attempts: list[CallAttempt],
        logical_engine: str,
        transport_provider: str,
        transport_model: str,
        request_snapshot: dict,
    ) -> None:
        error = attempts[-1].error
        error_code = error.error_code if error else ERROR_PARSE
        error_detail = str(error) if error else "unknown provider error"
        retryable = bool(error and error.retryable and error_code in RETRYABLE_ERRORS)
        retry_after = getattr(error, "retry_after_seconds", None)

        will_retry = False
        attempt_number = 0
        async with self._session_factory() as session:
            # Owner + liveness check under a row lock before writing evidence
            # (invariant 3/8): a stale/cancelled worker must not touch the task.
            locked = await self._lock_owned_running_task(
                session, task_id=task_id, audit_id=audit_id
            )
            if locked is None:
                await session.rollback()
                return
            task, _audit = locked
            task.request_snapshot = request_snapshot
            # One ProviderAttempt per actual call (all failed on this path).
            self._record_attempts(
                session,
                task=task,
                audit_id=audit_id,
                attempts=attempts,
                logical_engine=logical_engine,
                transport_provider=transport_provider,
                transport_model=transport_model,
                artifact_id=None,
            )
            attempt_number = task.attempt_count
            exhausted = task.attempt_count >= task.max_attempts
            will_retry = retryable and not exhausted
            record_event(
                session,
                audit_id=audit_id,
                event_type=EVENT_TASK_RETRY if will_retry else EVENT_TASK_FAILED,
                message="task retry" if will_retry else "task failed",
                payload={"task_id": str(task_id), "error_code": error_code},
            )
            await session.commit()

        if will_retry:
            await self._queue.retry(
                task_id=task_id,
                owner=self.owner,
                delay_seconds=audit_settings.retry_delay(attempt_number, retry_after),
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

    async def _fail_terminal(
        self,
        *,
        task_id: uuid.UUID,
        audit_id: uuid.UUID,
        logical_engine: str,
        transport_provider: str,
        transport_model: str,
        error_code: str,
        error_detail: str,
        request_snapshot: dict | None = None,
    ) -> None:
        """Terminally fail a task (non-retryable misconfiguration)."""
        async with self._session_factory() as session:
            # Owner + liveness check under a row lock before writing evidence
            # (invariant 3/8): even a terminal fail must not touch a task this
            # worker no longer owns or an audit that was cancelled meanwhile.
            locked = await self._lock_owned_running_task(
                session, task_id=task_id, audit_id=audit_id
            )
            if locked is None:
                await session.rollback()
                return
            task, _audit = locked
            task.attempt_count += 1
            if request_snapshot is not None:
                task.request_snapshot = request_snapshot
            session.add(
                ProviderAttempt(
                    task_id=task_id,
                    audit_id=audit_id,
                    attempt_number=task.attempt_count,
                    logical_engine=logical_engine,
                    transport_provider=transport_provider,
                    transport_model=transport_model,
                    status=ATTEMPT_STATUS_FAILED,
                    error_code=error_code,
                    error_detail=error_detail[:2000],
                )
            )
            record_event(
                session,
                audit_id=audit_id,
                event_type=EVENT_TASK_FAILED,
                message="task failed",
                payload={"task_id": str(task_id), "error_code": error_code},
            )
            await session.commit()
        await self._queue.fail(
            task_id=task_id,
            owner=self.owner,
            error_code=error_code,
            error_detail=error_detail,
        )

    async def _record_crash(
        self, task_id: uuid.UUID, audit_id: uuid.UUID, exc: Exception
    ) -> None:
        detail = f"{type(exc).__name__}: {exc}"
        await self._queue.fail(
            task_id=task_id,
            owner=self.owner,
            error_code=ERROR_PARSE,
            error_detail=detail,
        )

    async def _finalize_audit(self, audit_id: uuid.UUID) -> None:
        """Move a finished-execution audit off ``running`` at the boundary.

        Runs after each task terminalizes. When no non-terminal task remains,
        counts outcomes and transitions RUNNING -> ANALYZING (>=1 success) or
        RUNNING -> FAILED (0 successes). On ANALYZING it hands straight to the
        analysis stage (aggregate + terminal). A cancelled audit keeps its
        status. Guarded with ``FOR UPDATE`` so concurrent workers don't
        double-finalize.
        """
        reached_analyzing = False
        async with self._session_factory() as session:
            audit = await session.get(Audit, audit_id, with_for_update=True)
            if audit is None or audit.status in AUDIT_TERMINAL_STATUSES:
                if audit is not None:
                    await session.rollback()
                return
            remaining = await session.scalar(
                select(func.count())
                .select_from(AuditTask)
                .where(AuditTask.audit_id == audit_id)
                .where(AuditTask.status.not_in(list(TASK_TERMINAL_STATUSES)))
            )
            if remaining and remaining > 0:
                await session.rollback()
                return
            succeeded = await session.scalar(
                select(func.count())
                .select_from(AuditTask)
                .where(AuditTask.audit_id == audit_id)
                .where(AuditTask.status == TASK_STATUS_SUCCEEDED)
            )
            total = await session.scalar(
                select(func.count())
                .select_from(AuditTask)
                .where(AuditTask.audit_id == audit_id)
            )
            succeeded = int(succeeded or 0)
            total = int(total or 0)
            audit.completed_count = succeeded
            audit.failed_count = total - succeeded
            if audit.status == AUDIT_STATUS_RUNNING:
                if succeeded == 0:
                    audit.completed_at = _utcnow()
                    apply_transition(
                        session,
                        audit=audit,
                        target=AUDIT_STATUS_FAILED,
                        message="audit failed: no successful executions",
                    )
                    audit.error_message = "no successful executions"
                else:
                    # Execution done; hand to the deterministic analysis stage.
                    apply_transition(
                        session,
                        audit=audit,
                        target=AUDIT_STATUS_ANALYZING,
                        message="execution complete; ready for analysis",
                        payload={"completed": succeeded, "failed": total - succeeded},
                    )
                    reached_analyzing = True
            await session.commit()

        if reached_analyzing:
            await self._finalize_analysis(audit_id)

    async def _finalize_analysis(self, audit_id: uuid.UUID) -> None:
        """Aggregate the MetricSnapshot + resolve the terminal status (B6).

        Runs once an audit reaches ANALYZING. Aggregates from persisted analyses
        only (invariant 7 — no provider call) and drives ANALYZING -> REPORTING
        -> COMPLETED / PARTIALLY_COMPLETED. Guarded with ``FOR UPDATE`` so
        concurrent workers don't double-finalize.
        """
        async with self._session_factory() as session:
            audit = await session.get(Audit, audit_id, with_for_update=True)
            if audit is None or audit.status != AUDIT_STATUS_ANALYZING:
                if audit is not None:
                    await session.rollback()
                return
            await finalize_audit_analysis(session, audit=audit)
            await session.commit()


def main() -> None:  # pragma: no cover - process entrypoint
    configure_logging()
    worker = AuditWorker()
    asyncio.run(worker.run_forever())


if __name__ == "__main__":  # pragma: no cover
    main()
