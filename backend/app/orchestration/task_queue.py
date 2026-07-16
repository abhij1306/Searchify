# TaskQueue Protocol: the queue interface orchestration depends on (invariant 8).
#
# Orchestration + the worker depend only on this Protocol, never on a concrete
# implementation, so a future Redis-backed queue can replace the Postgres one
# with no domain/worker rewrite. The MVP implementation is
# ``PostgresTaskQueue`` (``postgres_task_queue.py``), which uses
# ``FOR UPDATE SKIP LOCKED`` and commits the claim before any network I/O.
from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from app.models.audit import AuditTask


@runtime_checkable
class TaskQueue(Protocol):
    """The audit task queue contract.

    All methods are async. ``claim`` must commit the claim (setting the lease)
    before the caller performs any network I/O, so a DB transaction is never
    held open across a provider call.
    """

    async def claim(
        self, *, owner: str, limit: int = 1
    ) -> list[AuditTask]:
        """Atomically claim up to ``limit`` eligible tasks for ``owner``.

        Selects claimable rows (``queued``/``retry_wait`` whose ``available_at``
        has passed) in deterministic priority order, locks them with
        ``FOR UPDATE SKIP LOCKED`` so two workers never grab the same row, marks
        them ``leased`` with a fresh ``lease_owner`` + ``lease_expires_at``, and
        commits. Returns the claimed tasks (detached from the claim txn).
        """
        ...

    async def heartbeat(
        self, *, task_id: uuid.UUID, owner: str
    ) -> bool:
        """Extend the lease on a task this ``owner`` holds. False if lost."""
        ...

    async def mark_running(
        self, *, task_id: uuid.UUID, owner: str
    ) -> bool:
        """Transition a leased task to ``running`` (still owned). False if lost."""
        ...

    async def succeed(
        self,
        *,
        task_id: uuid.UUID,
        owner: str,
        result_artifact_id: uuid.UUID | None = None,
    ) -> bool:
        """Mark a task ``succeeded`` and clear its lease. Idempotent-safe."""
        ...

    async def retry(
        self,
        *,
        task_id: uuid.UUID,
        owner: str,
        delay_seconds: float,
        error_code: str = "",
        error_detail: str = "",
    ) -> bool:
        """Return a task to ``retry_wait`` with a future ``available_at``.

        Increments nothing here (the worker increments ``attempt_count`` before
        the call); the queue only reschedules and releases the lease.
        """
        ...

    async def fail(
        self,
        *,
        task_id: uuid.UUID,
        owner: str,
        error_code: str = "",
        error_detail: str = "",
    ) -> bool:
        """Mark a task terminally ``failed`` and clear its lease."""
        ...

    async def cancel(self, *, task_id: uuid.UUID) -> bool:
        """Mark a non-terminal task ``cancelled`` (cooperative cancel)."""
        ...

    async def release_expired(self) -> int:
        """Sweeper: reclaim tasks whose lease expired.

        Returns each expired leased/running task to ``retry_wait`` (or ``failed``
        once ``attempt_count`` reaches ``max_attempts``). Returns the count of
        tasks acted on.
        """
        ...
