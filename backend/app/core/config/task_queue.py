# Queue-neutral task-queue configuration (invariant 1 + invariant 8).
#
# The Postgres ``FOR UPDATE SKIP LOCKED`` queue was originally hard-coded to
# ``AuditTask`` (docs/roadmap/integrations.md lines 206-226 specifies this
# genericization). This module owns the queue-row status vocabulary and the
# ``ERROR_MAX_ATTEMPTS`` token that are shared by EVERY queue-row model
# (``AuditTask``, ``SiteCrawlTask``, and future ones), plus the
# ``PostgresQueueSpec`` contract that parameterizes the one generic
# ``PostgresTaskQueue`` implementation over a concrete model.
#
# ``config/audits.py`` re-exports the ``TASK_STATUS_*`` / ``ERROR_MAX_ATTEMPTS``
# names so existing audit imports keep working unchanged.
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    # Type-only imports: the runtime never imports a model from config (that
    # would create the config <-> models circular import ``model_ref`` exists
    # to avoid). The queue-row union below is the typed version of the shared
    # column contract documented on ``PostgresQueueSpec``.
    from app.models.audit import AuditTask
    from app.models.content import ContentGeneration
    from app.models.site_health import SiteCrawlTask

# --- Queue-neutral task (queue row) statuses -----------------------------
# The queue-row lifecycle is identical for every task type:
#   queued|leased|running|succeeded|retry_wait|failed|cancelled.
TASK_STATUS_QUEUED: Final = "queued"
TASK_STATUS_LEASED: Final = "leased"
TASK_STATUS_RUNNING: Final = "running"
TASK_STATUS_SUCCEEDED: Final = "succeeded"
TASK_STATUS_RETRY_WAIT: Final = "retry_wait"
TASK_STATUS_FAILED: Final = "failed"
TASK_STATUS_CANCELLED: Final = "cancelled"

TASK_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {TASK_STATUS_SUCCEEDED, TASK_STATUS_FAILED, TASK_STATUS_CANCELLED}
)
# Statuses a ``claim()`` may pick up (queued or ready-to-retry).
TASK_CLAIMABLE_STATUSES: Final[frozenset[str]] = frozenset(
    {TASK_STATUS_QUEUED, TASK_STATUS_RETRY_WAIT}
)
# Statuses a sweeper reclaims when their lease expires.
TASK_LEASED_STATUSES: Final[frozenset[str]] = frozenset(
    {TASK_STATUS_LEASED, TASK_STATUS_RUNNING}
)

# Error token stamped on a task the sweeper fails after the retry budget is
# spent. Queue-neutral (shared by audit + Site Health task rows).
ERROR_MAX_ATTEMPTS: Final = "max_attempts_exceeded"


@dataclass(frozen=True)
class PostgresQueueSpec[T: ("AuditTask", "SiteCrawlTask", "ContentGeneration")]:
    """The model/settings contract that parameterizes ``PostgresTaskQueue``.

    A spec supplies everything the otherwise identical
    ``FOR UPDATE SKIP LOCKED`` claim/lease/heartbeat/sweeper code needs to
    operate over a concrete queue-row model without any behavior change:

    - ``model_ref`` — a zero-arg callable returning the ORM model class. It is
      a callable (not the class itself) so this config module never imports a
      model at import time, which would create a circular import
      (``models.* -> config.* -> models.*``).
    - ``lease_ttl`` — a callable returning the lease TTL in seconds, read fresh
      on every claim/heartbeat so a live settings change still applies (matches
      the original audit behavior).
    - ``claim_order`` — given the model class, returns the deterministic
      ``ORDER BY`` expressions used to pick the next claimable rows.
    - ``max_attempts_error`` — the error token the sweeper stamps when a task's
      attempt budget is exhausted.

    Every queue-row model must carry the shared column contract (``status``,
    ``lease_owner``, ``lease_expires_at``, ``heartbeat_at``, ``attempt_count``,
    ``max_attempts``, ``available_at``, ``error_code``/``error_detail``,
    ``completed_at``, ``result_artifact_id``) so the identical implementation
    serves both task types.
    """

    model_ref: Callable[[], type[T]]
    lease_ttl: Callable[[], float]
    claim_order: Callable[[type[T]], Sequence[Any]]
    max_attempts_error: str = field(default=ERROR_MAX_ATTEMPTS)

    @property
    def model(self) -> type[T]:
        """Resolve the concrete queue-row model class (import is cached)."""
        return self.model_ref()
