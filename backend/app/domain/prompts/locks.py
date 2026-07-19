# Shared prompt-set write serialization (PostgreSQL advisory locks).
#
# Generation and the delete paths that can race it (prompt-set delete, topic
# delete) all funnel through the SAME transaction-scoped advisory locks, so a
# delete can never interleave between generation's re-resolution and its
# inserts. Locks are transaction-scoped (``pg_advisory_xact_lock``): they
# release automatically at COMMIT/ROLLBACK, so no caller can leak one.
#
# There are two lock granularities:
#   * a PROJECT lock — serializes topic-level changes (topics are per-project
#     and a topic can be referenced by prompts in any set of the project);
#   * a PROMPT-SET lock — serializes the active-pool count + inserts for a set.
#
# Deadlock avoidance: every caller that needs both acquires them in the SAME
# global order — PROJECT lock FIRST, then PROMPT-SET lock. No path ever takes a
# set lock before a project lock, so opposing lock orders cannot arise.
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Fixed namespaces so our advisory keys can't collide with any other feature's
# advisory locks (or each other) in the same database.
_PROMPT_SET_NAMESPACE = 0x50524F4D  # "PROM"
_PROJECT_NAMESPACE = 0x50524F4A  # "PROJ"


def _to_signed_int32(n: int) -> int:
    """Map an unsigned 32-bit value into the signed int32 range Postgres wants.

    ``pg_advisory_xact_lock(int, int)`` takes signed 32-bit integers.
    """
    return n - 0x100000000 if n >= 0x80000000 else n


def _is_postgres(session: AsyncSession) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


async def _advisory_xact_lock(
    session: AsyncSession, namespace: int, entity_id: uuid.UUID
) -> None:
    if not _is_postgres(session):
        return
    key = int.from_bytes(entity_id.bytes[:4], "big")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)").bindparams(
            k1=_to_signed_int32(namespace), k2=_to_signed_int32(key)
        )
    )


async def acquire_project_lock(
    session: AsyncSession, project_id: uuid.UUID
) -> None:
    """Serialize topic-level writers for one project (transaction-scoped).

    Held by generation and by ``delete_topic`` so a topic cannot be deleted
    between generation's re-resolution and its inserts. Must be acquired
    BEFORE any prompt-set lock (see module docstring).
    """
    await _advisory_xact_lock(session, _PROJECT_NAMESPACE, project_id)


async def acquire_prompt_set_lock(
    session: AsyncSession, prompt_set_id: uuid.UUID
) -> None:
    """Serialize writers for one prompt set (transaction-scoped).

    Held by generation and by ``delete_prompt_set``. Must be acquired AFTER the
    project lock when both are needed (see module docstring). Non-PostgreSQL
    dialects (e.g. SQLite in isolated unit tests) skip the lock; production
    always runs on PostgreSQL where the lock is guaranteed.
    """
    await _advisory_xact_lock(session, _PROMPT_SET_NAMESPACE, prompt_set_id)

