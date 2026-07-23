# Analytics worker: claims AnalyticsTask queue rows and runs the per-kind
# executor registered in the kind dispatch table.
#
# A separate process (the ``analytics-worker`` compose service). It mirrors
# ``ContentWorker`` exactly on the queue mechanics — claim via the generic
# ``PostgresTaskQueue`` (``FOR UPDATE SKIP LOCKED``, claim committed BEFORE
# any work — invariant 8), sweep expired leases FIRST in every loop
# iteration, ``mark_running`` before dispatch, heartbeat the lease while the
# executor runs, and cooperative cancel at the task boundary. Terminal
# accounting goes through the worker-owned atomic ``_finalize``: one locked
# transaction per dispatch re-checks owner/status (a lost lease or an
# already-terminal row writes NOTHING — single-writer, invariant 3) and
# increments ``attempt_count`` exactly once.
#
# NO kind performs network I/O — every executor is a pure projection over
# persisted rows (invariant 7), so this worker takes no transport; the test
# seam is the executor mapping override instead.
#
# DISPATCH TABLE: kinds without a landed executor map to a stub that raises
# ``ExecutorNotWiredError`` (stamped as terminal ``executor_not_wired``,
# never retried). A5 wired ``ingest_referrals``; A6 wired
# ``classify_referrals`` + ``referral_retention_sweep``; A7
# (traffic_snapshot_refresh) and A8 (analytics_snapshot_refresh) replace the
# remaining stubs as they land.
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.analytics import (
    ANALYTICS_QUEUE_SPEC,
    ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH,
    ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS,
    ANALYTICS_TASK_KIND_INGEST_REFERRALS,
    ANALYTICS_TASK_KIND_REFERRAL_RETENTION_SWEEP,
    ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH,
    ERROR_EXECUTOR_NOT_WIRED,
    analytics_settings,
)
from app.core.config.provider_catalog import ERROR_UNKNOWN
from app.core.config.task_queue import (
    TASK_STATUS_FAILED,
    TASK_STATUS_RETRY_WAIT,
    TASK_STATUS_SUCCEEDED,
    TASK_TERMINAL_STATUSES,
)
from app.core.database import SessionLocal
from app.core.telemetry import configure_logging
from app.domain.analytics.ingest import ingest_referrals
from app.domain.analytics.tasks import (
    run_classify_referrals,
    run_referral_retention_sweep,
)
from app.models.analytics import AnalyticsTask
from app.orchestration.postgres_task_queue import PostgresTaskQueue

logger = logging.getLogger("app.workers.analytics_worker")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ExecutorNotWiredError(RuntimeError):
    """A claimed task kind has no registered executor yet (A5/A6/A7/A8)."""


# Executor contract (A5+): one async callable per task kind. It receives the
# session factory + the claimed queue row and performs the kind's projection
# work (DB only — NO network I/O, invariant 7). The worker owns the queue
# lifecycle around it (mark_running / heartbeat / finalize).
type AnalyticsExecutor = Callable[
    [async_sessionmaker[AsyncSession], AnalyticsTask], Awaitable[None]
]


async def _executor_not_wired(
    session_factory: async_sessionmaker[AsyncSession], task: AnalyticsTask
) -> None:
    # TODO(A6/A7/A8): those tasks register the real executor for this kind
    # in ``EXECUTORS`` below; until then every claimed row fails loud.
    raise ExecutorNotWiredError(
        f"analytics task kind {task.task_kind!r} has no registered executor"
    )


# Kind dispatch table (invariant 2: one owner of kind -> executor routing).
# Each executor-landing task substitutes its real executor for the stub on
# its own line: A5 wired ingest_referrals; A6 wired classify_referrals +
# referral_retention_sweep; A7/A8 wire the rest.
EXECUTORS: dict[str, AnalyticsExecutor] = {
    ANALYTICS_TASK_KIND_INGEST_REFERRALS: ingest_referrals,
    ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS: run_classify_referrals,
    ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH: _executor_not_wired,
    ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH: _executor_not_wired,
    ANALYTICS_TASK_KIND_REFERRAL_RETENTION_SWEEP: run_referral_retention_sweep,
}


class AnalyticsWorker:
    """Claim/lease loop for ``AnalyticsTask`` rows.

    ``executors`` is the test seam: a dispatch-table override so tests drive
    the loop with fake executors. Production passes none and uses the module
    ``EXECUTORS`` table. No ``transport``: no kind performs network I/O.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        owner: str | None = None,
        executors: dict[str, AnalyticsExecutor] | None = None,
    ) -> None:
        self._session_factory = session_factory or SessionLocal
        self._queue = PostgresTaskQueue(self._session_factory, ANALYTICS_QUEUE_SPEC)
        self._executors = executors if executors is not None else EXECUTORS
        self.owner = owner or f"analytics-worker-{uuid.uuid4().hex[:12]}"

    # --- Loop --------------------------------------------------------------

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
        logger.info("analytics worker started", extra={"owner": self.owner})
        while True:
            try:
                ran = await self.run_once()
            except Exception:  # defensive: a bad row must not kill the loop
                logger.exception("analytics worker loop iteration failed")
                ran = 0
            if ran == 0:
                await asyncio.sleep(
                    max(0.05, analytics_settings.poll_interval_seconds)
                )

    # --- One claimed row -----------------------------------------------------

    async def _execute(self, claimed: AnalyticsTask) -> None:
        task_id = claimed.id
        try:
            # Cooperative cancel at the boundary: if the row reached a
            # terminal status between enqueue and claim, never dispatch.
            async with self._session_factory() as session:
                row = await session.get(AnalyticsTask, task_id)
                if row is None or row.status in TASK_TERMINAL_STATUSES:
                    return

            if not await self._queue.mark_running(
                task_id=task_id, owner=self.owner
            ):
                # Lease lost before dispatch; another worker retries.
                return

            await self._run_executor(claimed)
        except Exception as exc:  # defensive: never kill the loop
            logger.exception(
                "analytics task crashed",
                extra={"task_id": str(task_id)},
            )
            with contextlib.suppress(Exception):
                await self._finalize(task_id=task_id, owner=self.owner, error=exc)

    async def _run_executor(self, claimed: AnalyticsTask) -> None:
        executor = self._executors.get(claimed.task_kind)
        heartbeat = asyncio.create_task(self._heartbeat_loop(claimed.id))
        error: Exception | None = None
        try:
            if executor is None:
                # A kind outside the dispatch table is a config bug — fail
                # loud exactly like a not-yet-wired kind.
                await _executor_not_wired(self._session_factory, claimed)
            else:
                await executor(self._session_factory, claimed)
        except Exception as exc:
            error = exc
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        await self._finalize(task_id=claimed.id, owner=self.owner, error=error)

    async def _heartbeat_loop(self, task_id: uuid.UUID) -> None:
        interval = max(1.0, analytics_settings.heartbeat_interval_seconds)
        while True:
            await asyncio.sleep(interval)
            try:
                await self._queue.heartbeat(task_id=task_id, owner=self.owner)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A dead heartbeat loop silently expires the lease and lets
                # the sweeper hand the task to another worker mid-run; keep
                # beating through transient failures instead.
                logger.exception(
                    "heartbeat failed; retrying",
                    extra={"task_id": str(task_id)},
                )

    # --- Atomic terminal accounting -------------------------------------------

    async def _finalize(
        self, *, task_id: uuid.UUID, owner: str, error: Exception | None
    ) -> bool:
        """ONE locked transaction per dispatch (the only terminal writer).

        Locks the row ``FOR UPDATE``, re-checks owner + status (a lost lease
        or an already-terminal row writes nothing), increments
        ``attempt_count`` exactly once, and writes the success / retry /
        terminal-failure fields together. A not-wired executor is a
        permanent-until-deploy condition: terminal failure WITHOUT consuming
        the retry budget on further attempts.
        """
        now = _utcnow()
        async with self._session_factory() as session:
            row = await session.get(AnalyticsTask, task_id, with_for_update=True)
            if row is None:
                await session.commit()
                return False
            if row.lease_owner != owner or row.status in TASK_TERMINAL_STATUSES:
                await session.commit()
                return False

            attempt_number = row.attempt_count + 1
            row.attempt_count = attempt_number
            if error is None:
                row.status = TASK_STATUS_SUCCEEDED
                row.completed_at = now
                row.error_code = ""
                row.error_detail = ""
            elif isinstance(error, ExecutorNotWiredError):
                row.status = TASK_STATUS_FAILED
                row.completed_at = now
                row.error_code = ERROR_EXECUTOR_NOT_WIRED
                row.error_detail = str(error)[:2000]
            elif attempt_number < row.max_attempts:
                row.status = TASK_STATUS_RETRY_WAIT
                row.available_at = now + timedelta(
                    seconds=analytics_settings.retry_delay_seconds
                )
                row.error_code = ERROR_UNKNOWN
                row.error_detail = str(error)[:2000]
            else:
                row.status = TASK_STATUS_FAILED
                row.completed_at = now
                row.error_code = ANALYTICS_QUEUE_SPEC.max_attempts_error
                row.error_detail = str(error)[:2000]
            row.lease_owner = None
            row.lease_expires_at = None
            await session.commit()
            return True


def main() -> None:  # pragma: no cover - process entrypoint
    configure_logging()
    worker = AnalyticsWorker()
    asyncio.run(worker.run_forever())


if __name__ == "__main__":  # pragma: no cover
    main()
