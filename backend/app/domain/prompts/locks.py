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

import hashlib
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Fixed namespaces are included in the 64-bit hash, making accidental lock-key
# overlap with another entity class negligibly unlikely at application scale.
_PROMPT_SET_NAMESPACE = 0x50524F4D  # "PROM"
_PROJECT_NAMESPACE = 0x50524F4A  # "PROJ"


def _advisory_lock_key(namespace: int, entity_id: uuid.UUID) -> int:
    """Derive the stable signed 64-bit key used by every lock participant."""
    digest = hashlib.blake2b(
        namespace.to_bytes(4, "big") + entity_id.bytes,
        digest_size=8,
        person=b"searchify-locks",
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


def _is_postgres(session: AsyncSession) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


async def _advisory_xact_lock(
    session: AsyncSession, namespace: int, entity_id: uuid.UUID
) -> None:
    if not _is_postgres(session):
        return
    key = _advisory_lock_key(namespace, entity_id)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)").bindparams(key=key)
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

