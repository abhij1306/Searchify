# Content worker: claims ContentGeneration queue rows and calls the provider.
#
# A separate process (the ``content-worker`` compose service / Railway
# service). It claims rows through the generic ``PostgresTaskQueue``
# (``FOR UPDATE SKIP LOCKED``; claim committed BEFORE any network I/O —
# invariant 8), builds a fresh discovery client per attempt (env-driven
# ``SecretStr`` key resolved at call time, never logged — invariant 6), and
# heartbeats the lease while the call runs.
#
# ALL attempt + terminal accounting goes through the worker-owned atomic
# ``finalize_attempt``: one locked transaction per actual HTTP call appends
# exactly one ``ContentGenerationAttempt``, increments ``attempt_count`` once,
# and writes the retry/terminal fields together — a crash mid-write can never
# leave a half-counted attempt. The worker deliberately does NOT use
# ``PostgresTaskQueue.succeed()`` (that method only exists to write the audit
# ``result_artifact_id``); the queue is used for claim/heartbeat/mark_running/
# release_expired only.
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors.answer_engines.errors import ProviderError
from app.connectors.discovery_models.contracts import (
    DiscoveryRequest,
    DiscoveryResponse,
)
from app.connectors.discovery_models.factory import build_discovery_client
from app.core.config.content import (
    CONTENT_QUEUE_SPEC,
    content_settings,
)
from app.core.config.provider_catalog import ERROR_PARSE, ERROR_UNKNOWN
from app.core.config.task_queue import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_RETRY_WAIT,
    TASK_STATUS_SUCCEEDED,
    TASK_TERMINAL_STATUSES,
)
from app.core.database import SessionLocal
from app.core.telemetry import configure_logging
from app.domain.content.message_builder import build_messages
from app.domain.content.website_context import WebsiteContext
from app.models.content import ContentGeneration, ContentGenerationAttempt
from app.orchestration.postgres_task_queue import PostgresTaskQueue

logger = logging.getLogger("app.workers.content_worker")

# Attempt-row statuses (what happened on ONE actual HTTP call).
ATTEMPT_STATUS_SUCCEEDED = "succeeded"
ATTEMPT_STATUS_FAILED = "failed"

# Mistral's truncation finish reason: output hit ``max_tokens``.
FINISH_REASON_LENGTH = "length"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AttemptOutcome:
    """The result of ONE actual provider HTTP call, ready to finalize."""

    response: DiscoveryResponse | None
    error: ProviderError | None

    @property
    def succeeded(self) -> bool:
        return self.response is not None


class ContentWorker:
    """Claim/lease loop for ``ContentGeneration`` rows.

    ``transport`` is the test seam: an ``httpx.MockTransport`` makes the real
    ``MistralDiscoveryClient`` run without a network. Production passes none.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        owner: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._session_factory = session_factory or SessionLocal
        self._queue = PostgresTaskQueue(self._session_factory, CONTENT_QUEUE_SPEC)
        self._transport = transport
        self.owner = owner or f"content-worker-{uuid.uuid4().hex[:12]}"

    # --- Loop -------------------------------------------------------------

    async def run_once(self) -> int:
        """Sweep expired leases, claim one row, run it. Returns count run."""
        await self._queue.release_expired()
        rows = await self._queue.claim(owner=self.owner, limit=1)
        for row in rows:
            await self._execute(row)
        return len(rows)

    async def run_until_idle(self, *, max_batches: int = 1000) -> int:
        """Drain the queue until a claim returns nothing (test/one-shot)."""
        total = 0
        for _ in range(max_batches):
            ran = await self.run_once()
            if ran == 0:
                break
            total += ran
        return total

    async def run_forever(self) -> None:  # pragma: no cover - process loop
        logger.info("content worker started", extra={"owner": self.owner})
        while True:
            try:
                ran = await self.run_once()
            except Exception:  # defensive: a bad row must not kill the loop
                logger.exception("content worker loop iteration failed")
                ran = 0
            if ran == 0:
                await asyncio.sleep(max(0.05, content_settings.poll_interval_seconds))

    # --- One claimed row --------------------------------------------------

    async def _execute(self, claimed: ContentGeneration) -> None:
        generation_id = claimed.id
        try:
            # Cooperative cancel at the boundary: if the row was cancelled
            # between enqueue and claim, never touch the provider.
            async with self._session_factory() as session:
                row = await session.get(ContentGeneration, generation_id)
                if row is None or row.status in TASK_TERMINAL_STATUSES:
                    return

            if not await self._queue.mark_running(
                task_id=generation_id, owner=self.owner
            ):
                # Lease lost before the call started; another worker retries.
                return

            await self._run_provider_call(claimed)
        except Exception as exc:  # defensive: never kill the loop
            logger.exception(
                "content generation crashed",
                extra={"generation_id": str(generation_id)},
            )
            with contextlib.suppress(Exception):
                await self.finalize_attempt(
                    generation_id=generation_id,
                    owner=self.owner,
                    outcome=AttemptOutcome(
                        response=None,
                        error=ProviderError(
                            f"worker crash: {type(exc).__name__}",
                            error_code=ERROR_UNKNOWN,
                            retryable=False,
                        ),
                    ),
                )

    async def _run_provider_call(self, claimed: ContentGeneration) -> None:
        # Rebuild the exact frozen messages from the immutable inputs (the
        # snapshot was truncated for provenance; the digest pins the content).
        snapshot = claimed.website_context_snapshot or {}
        website_context = WebsiteContext(
            status=claimed.website_context_status,
            pages=list(snapshot.get("pages") or []),
            summary=snapshot.get("summary"),
        )
        messages, _digest, _snapshot = build_messages(
            prompt=claimed.prompt,
            output_type=claimed.output_type,
            website_context=website_context,
        )
        request = DiscoveryRequest(
            messages=tuple(messages),
            model=claimed.requested_model or content_settings.model,
            timeout_seconds=content_settings.request_timeout_seconds,
            max_output_tokens=content_settings.max_output_tokens,
        )

        # Fresh client per attempt; the SecretStr key resolves inside the
        # factory at call time and never touches this row (invariant 6).
        try:
            client = build_discovery_client(transport=self._transport)
        except ProviderError as exc:
            # No HTTP call happened — a construction failure is pure
            # misconfiguration, so it must not consume the retry budget or
            # append an attempt row (finalize_attempt is reserved for actual
            # provider calls).
            await self._fail_without_attempt(generation_id=claimed.id, error=exc)
            return

        heartbeat = asyncio.create_task(self._heartbeat_loop(claimed.id))
        try:
            response = await client.generate(request)
            outcome = AttemptOutcome(response=response, error=None)
        except ProviderError as exc:
            outcome = AttemptOutcome(response=None, error=exc)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

        # Empty output on an otherwise-successful call is a parse-class
        # failure (retryable per budget), never a success.
        ok_response = outcome.response
        if ok_response is not None and not (ok_response.output_text or "").strip():
            outcome = AttemptOutcome(
                response=None,
                error=ProviderError(
                    "provider returned an empty output",
                    error_code=ERROR_PARSE,
                    retryable=True,
                ),
            )
        await self.finalize_attempt(
            generation_id=claimed.id, owner=self.owner, outcome=outcome
        )

    async def _heartbeat_loop(
        self, generation_id: uuid.UUID
    ) -> None:  # pragma: no cover - timing loop
        interval = max(1.0, content_settings.heartbeat_interval_seconds)
        try:
            while True:
                await asyncio.sleep(interval)
                await self._queue.heartbeat(task_id=generation_id, owner=self.owner)
        except asyncio.CancelledError:
            raise

    # --- Atomic attempt + terminal accounting -----------------------------

    async def _fail_without_attempt(
        self, *, generation_id: uuid.UUID, error: ProviderError
    ) -> None:
        """Terminal failure with NO attempt accounting (no HTTP call ran).

        Same lock + owner/status re-checks as ``finalize_attempt``, but no
        ``ContentGenerationAttempt`` row and no ``attempt_count`` increment:
        once the misconfiguration is fixed, a regenerate starts with the full
        retry budget.
        """
        async with self._session_factory() as session:
            row = await session.get(
                ContentGeneration, generation_id, with_for_update=True
            )
            if (
                row is None
                or row.lease_owner != self.owner
                or row.status in TASK_TERMINAL_STATUSES
            ):
                await session.commit()
                return
            row.status = TASK_STATUS_FAILED
            row.completed_at = _utcnow()
            row.error_code = error.error_code
            row.error_detail = str(error)[:2000]
            row.lease_owner = None
            row.lease_expires_at = None
            await session.commit()

    async def finalize_attempt(
        self,
        *,
        generation_id: uuid.UUID,
        owner: str,
        outcome: AttemptOutcome,
    ) -> bool:
        """ONE locked transaction per actual HTTP call (the only writer).

        Locks the row ``FOR UPDATE``, re-checks owner + status (a lost lease
        or an already-terminal row writes nothing; a ``cancelled`` row still
        records the attempt for auditability but discards the output), then
        appends the attempt, increments ``attempt_count`` exactly once, and
        writes the matching retry/terminal fields — all committed together.
        """
        now = _utcnow()
        async with self._session_factory() as session:
            row = await session.get(
                ContentGeneration, generation_id, with_for_update=True
            )
            if row is None:
                await session.commit()
                return False

            cancelled = row.status == TASK_STATUS_CANCELLED
            if row.lease_owner != owner and not cancelled:
                # Lease lost (sweeper reclaimed it): another worker owns the
                # row now; writing anything would violate single-writer.
                await session.commit()
                return False
            if row.status in TASK_TERMINAL_STATUSES and not cancelled:
                await session.commit()
                return False

            attempt_number = row.attempt_count + 1
            row.attempt_count = attempt_number
            response, error = outcome.response, outcome.error
            session.add(
                ContentGenerationAttempt(
                    content_generation_id=row.id,
                    attempt_number=attempt_number,
                    status=(
                        ATTEMPT_STATUS_SUCCEEDED
                        if outcome.succeeded
                        else ATTEMPT_STATUS_FAILED
                    ),
                    requested_model=row.requested_model,
                    returned_model=(
                        response.returned_model if response is not None else None
                    ),
                    finish_reason=(
                        response.finish_reason if response is not None else None
                    ),
                    error_code=error.error_code if error is not None else "",
                    error_detail=str(error)[:2000] if error is not None else "",
                    usage=dict(response.usage) if response is not None else None,
                    latency_ms=(response.latency_ms if response is not None else None),
                )
            )

            if cancelled:
                # Record the real provider outcome above, but the row stays
                # cancelled and no result fields are written (invariant 3/9).
                await session.commit()
                return True

            if outcome.succeeded:
                assert response is not None  # succeeded == response is not None
                row.output_text = response.output_text
                row.provider = response.provider
                row.returned_model = response.returned_model
                row.finish_reason = response.finish_reason
                row.output_truncated = response.finish_reason == FINISH_REASON_LENGTH
                row.usage = dict(response.usage)
                row.latency_ms = response.latency_ms
                row.status = TASK_STATUS_SUCCEEDED
                row.completed_at = now
                row.error_code = ""
                row.error_detail = ""
            elif (
                error is not None
                and error.retryable
                and (attempt_number < row.max_attempts)
            ):
                delay = content_settings.retry_delay(
                    attempt_number, error.retry_after_seconds
                )
                row.status = TASK_STATUS_RETRY_WAIT
                row.available_at = now + timedelta(seconds=delay)
                row.error_code = error.error_code
                row.error_detail = str(error)[:2000]
            else:
                row.status = TASK_STATUS_FAILED
                row.completed_at = now
                if error is not None and not error.retryable:
                    row.error_code = error.error_code
                    row.error_detail = str(error)[:2000]
                else:
                    row.error_code = CONTENT_QUEUE_SPEC.max_attempts_error
                    row.error_detail = str(error)[:2000] if error is not None else ""
            row.lease_owner = None
            row.lease_expires_at = None
            await session.commit()
            return True


def main() -> None:  # pragma: no cover - process entrypoint
    configure_logging()
    worker = ContentWorker()
    asyncio.run(worker.run_forever())


if __name__ == "__main__":  # pragma: no cover
    main()
