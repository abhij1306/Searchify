# Postgres task queue (invariant 8): FOR UPDATE SKIP LOCKED claim + leasing.
#
# The MVP ``TaskQueue`` implementation. Postgres is both durable state and the
# queue (no Redis at MVP). The claim runs in one short transaction that locks
# eligible rows with ``FOR UPDATE SKIP LOCKED`` and commits BEFORE the worker
# does any provider I/O, so a DB transaction is never held across a network
# call. ``SKIP LOCKED`` plus the unique ``idempotency_key`` and slot constraint
# guarantee no double-claim. ``release_expired`` is the sweeper that reclaims
# leases from a crashed worker.
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.audits import (
    ERROR_MAX_ATTEMPTS,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_LEASED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RETRY_WAIT,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCEEDED,
    audit_settings,
)
from app.models.audit import AuditTask

logger = logging.getLogger("app.orchestration.postgres_task_queue")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PostgresTaskQueue:
    """``TaskQueue`` backed by Postgres ``FOR UPDATE SKIP LOCKED``.

    Constructed with a session factory (``async_sessionmaker``) so each queue
    operation runs in its own short-lived transaction — never one held open
    across a provider call.
    """

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._session_factory = session_factory

    async def claim(
        self, *, owner: str, limit: int = 1
    ) -> list[AuditTask]:
        now = _utcnow()
        lease_expires = now + timedelta(
            seconds=audit_settings.lease_ttl_seconds
        )
        async with self._session_factory() as session:
            # Lock eligible rows and skip any another worker already holds. The
            # ORDER BY makes claim order deterministic (priority, then FIFO).
            stmt = (
                select(AuditTask)
                .where(
                    AuditTask.status.in_(
                        [TASK_STATUS_QUEUED, TASK_STATUS_RETRY_WAIT]
                    )
                )
                .where(AuditTask.available_at <= now)
                .order_by(
                    AuditTask.priority.desc(),
                    AuditTask.available_at.asc(),
                    AuditTask.randomized_position.asc(),
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            tasks = list((await session.scalars(stmt)).all())
            for task in tasks:
                task.status = TASK_STATUS_LEASED
                task.lease_owner = owner
                task.lease_expires_at = lease_expires
                task.heartbeat_at = now
            # COMMIT the claim before returning — the caller does network I/O
            # only after this returns (invariant 8).
            await session.commit()
            # Detach loaded snapshots so the caller can read fields after the
            # session closes.
            for task in tasks:
                session.expunge(task)
            return tasks

    async def _owned_task(
        self, session: AsyncSession, task_id: uuid.UUID, owner: str | None
    ) -> AuditTask | None:
        task = await session.get(AuditTask, task_id, with_for_update=True)
        if task is None:
            return None
        if owner is not None and task.lease_owner != owner:
            # Lease was reclaimed by the sweeper or stolen; do not act.
            return None
        return task

    async def heartbeat(self, *, task_id: uuid.UUID, owner: str) -> bool:
        now = _utcnow()
        async with self._session_factory() as session:
            task = await self._owned_task(session, task_id, owner)
            if task is None or task.status not in (
                TASK_STATUS_LEASED,
                TASK_STATUS_RUNNING,
            ):
                await session.commit()
                return False
            task.heartbeat_at = now
            task.lease_expires_at = now + timedelta(
                seconds=audit_settings.lease_ttl_seconds
            )
            await session.commit()
            return True

    async def mark_running(self, *, task_id: uuid.UUID, owner: str) -> bool:
        async with self._session_factory() as session:
            task = await self._owned_task(session, task_id, owner)
            if task is None or task.status != TASK_STATUS_LEASED:
                await session.commit()
                return False
            task.status = TASK_STATUS_RUNNING
            task.heartbeat_at = _utcnow()
            await session.commit()
            return True

    async def succeed(
        self,
        *,
        task_id: uuid.UUID,
        owner: str,
        result_artifact_id: uuid.UUID | None = None,
    ) -> bool:
        return await self._finalize(
            task_id=task_id,
            owner=owner,
            status=TASK_STATUS_SUCCEEDED,
            mutate=lambda task: setattr(
                task, "result_artifact_id", result_artifact_id
            ),
        )

    async def fail(
        self,
        *,
        task_id: uuid.UUID,
        owner: str,
        error_code: str = "",
        error_detail: str = "",
    ) -> bool:
        def _set(task: AuditTask) -> None:
            task.error_code = error_code
            task.error_detail = error_detail[:2000]

        return await self._finalize(
            task_id=task_id, owner=owner, status=TASK_STATUS_FAILED, mutate=_set
        )

    async def _finalize(
        self,
        *,
        task_id: uuid.UUID,
        owner: str,
        status: str,
        mutate: Callable[[AuditTask], None] | None = None,
    ) -> bool:
        now = _utcnow()
        async with self._session_factory() as session:
            task = await self._owned_task(session, task_id, owner)
            if task is None:
                await session.commit()
                return False
            task.status = status
            task.lease_owner = None
            task.lease_expires_at = None
            task.completed_at = now
            if mutate is not None:
                mutate(task)
            await session.commit()
            return True

    async def retry(
        self,
        *,
        task_id: uuid.UUID,
        owner: str,
        delay_seconds: float,
        error_code: str = "",
        error_detail: str = "",
    ) -> bool:
        now = _utcnow()
        async with self._session_factory() as session:
            task = await self._owned_task(session, task_id, owner)
            if task is None:
                await session.commit()
                return False
            task.status = TASK_STATUS_RETRY_WAIT
            task.lease_owner = None
            task.lease_expires_at = None
            task.available_at = now + timedelta(seconds=max(0.0, delay_seconds))
            task.error_code = error_code
            task.error_detail = error_detail[:2000]
            await session.commit()
            return True

    async def cancel(self, *, task_id: uuid.UUID) -> bool:
        now = _utcnow()
        async with self._session_factory() as session:
            task = await self._owned_task(session, task_id, owner=None)
            if task is None or task.status in (
                TASK_STATUS_SUCCEEDED,
                TASK_STATUS_FAILED,
                TASK_STATUS_CANCELLED,
            ):
                await session.commit()
                return False
            task.status = TASK_STATUS_CANCELLED
            task.lease_owner = None
            task.lease_expires_at = None
            task.completed_at = now
            if not task.error_code:
                task.error_code = "cancelled"
            await session.commit()
            return True

    async def release_expired(self) -> int:
        """Reclaim leases whose ``lease_expires_at`` has passed.

        Expired leased/running tasks with attempts remaining return to
        ``retry_wait`` (available immediately); those that have exhausted
        ``max_attempts`` are marked ``failed``. Uses ``SKIP LOCKED`` so it never
        contends with a live worker still holding its row.
        """
        now = _utcnow()
        reclaimed = 0
        async with self._session_factory() as session:
            stmt = (
                select(AuditTask)
                .where(
                    AuditTask.status.in_(
                        [TASK_STATUS_LEASED, TASK_STATUS_RUNNING]
                    )
                )
                .where(AuditTask.lease_expires_at.is_not(None))
                .where(AuditTask.lease_expires_at < now)
                .with_for_update(skip_locked=True)
            )
            tasks = list((await session.scalars(stmt)).all())
            for task in tasks:
                task.lease_owner = None
                task.lease_expires_at = None
                if task.attempt_count >= task.max_attempts:
                    task.status = TASK_STATUS_FAILED
                    task.completed_at = now
                    if not task.error_code:
                        task.error_code = ERROR_MAX_ATTEMPTS
                        task.error_detail = (
                            "lease expired after max attempts exhausted"
                        )
                else:
                    task.status = TASK_STATUS_RETRY_WAIT
                    task.available_at = now
                reclaimed += 1
            await session.commit()
            if reclaimed:
                logger.info(
                    "sweeper reclaimed expired leases",
                    extra={"reclaimed": reclaimed},
                )
            return reclaimed
