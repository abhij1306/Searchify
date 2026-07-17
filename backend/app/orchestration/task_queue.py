# TaskQueue[T] Protocol: the queue interface orchestration depends on
# (invariant 8).
#
# Orchestration + the workers depend only on this Protocol, never on a concrete
# implementation, so a future Redis-backed queue can replace the Postgres one
# with no domain/worker rewrite. The Protocol is generic over the queue-row
# model ``T`` (``AuditTask`` / ``SiteCrawlTask`` / a future task type) so the
# one implementation serves every task type. The MVP implementation is
# ``PostgresTaskQueue`` (``postgres_task_queue.py``), which uses
# ``FOR UPDATE SKIP LOCKED`` and commits the claim before any network I/O.
from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class TaskQueue[T](Protocol):
    """The generic task queue contract, parameterized by the queue-row model.

    All methods are async. ``claim`` must commit the claim (setting the lease)
    before the caller performs any network I/O, so a DB transaction is never
    held open across a provider/HTTP call (invariant 8).
    """

    async def claim(
        self,
        *,
        owner: str,
        limit: int = 1,
        kinds: Sequence[str] | None = None,
    ) -> list[T]:
        """Atomically claim up to ``limit`` eligible tasks for ``owner``.

        Selects claimable rows (``queued``/``retry_wait`` whose ``available_at``
        has passed) in the spec's deterministic order, optionally restricted to
        ``kinds`` (e.g. a worker that only handles a subset of task kinds),
        locks them with ``FOR UPDATE SKIP LOCKED`` so two workers never grab
        the same row, marks them ``leased`` with a fresh ``lease_owner`` +
        ``lease_expires_at``, and commits. Returns the claimed tasks (detached
        from the claim txn).
        """
        ...

    async def heartbeat(self, *, task_id: uuid.UUID, owner: str) -> bool:
        """Extend the lease on a task this ``owner`` holds. False if lost."""
        ...

    async def mark_running(self, *, task_id: uuid.UUID, owner: str) -> bool:
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

    async def release_expired(self, *, batch_size: int = 500) -> int:
        """Sweeper: reclaim tasks whose lease expired.

        Returns each expired leased/running task to ``retry_wait`` (or ``failed``
        once ``attempt_count`` reaches ``max_attempts``). Bounded to
        ``batch_size`` rows per call in a deterministic order so a mass expiry
        never holds one long-running transaction; repeated polling drains the
        remainder. Returns the count of tasks acted on.
        """
        ...
