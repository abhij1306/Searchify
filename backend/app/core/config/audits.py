# Audit lifecycle + queue + execution guardrail configuration (invariant 1).
#
# Owns every tunable knob for the B5 audit-execution subsystem: the audit
# lifecycle statuses + the queue/task statuses, the deterministic system
# instructions per benchmark mode, and the provider-agnostic execution
# guardrails (pacing, per-call ceiling, retry budget, run deadline, lease TTL,
# heartbeat interval). Orchestration, the planner, and the worker READ these;
# they never hard-code the literals inline. Adapted from the reference
# ``config/ai_visibility.py`` guardrail knobs.
from __future__ import annotations

from typing import Final

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.projects import (
    BENCHMARK_MODE_CONSUMER_LIKE,
    BENCHMARK_MODE_CONTROLLED_LOCALIZED,
)

# --- Audit lifecycle statuses --------------------------------------------
# The state machine (``app/orchestration/audit_state.py``) enforces the legal
# transitions between these.
AUDIT_STATUS_DRAFT: Final = "draft"
AUDIT_STATUS_VALIDATING: Final = "validating"
AUDIT_STATUS_QUEUED: Final = "queued"
AUDIT_STATUS_RUNNING: Final = "running"
AUDIT_STATUS_ANALYZING: Final = "analyzing"
AUDIT_STATUS_REPORTING: Final = "reporting"
AUDIT_STATUS_COMPLETED: Final = "completed"
AUDIT_STATUS_PARTIALLY_COMPLETED: Final = "partially_completed"
AUDIT_STATUS_FAILED: Final = "failed"
AUDIT_STATUS_CANCELLED: Final = "cancelled"

AUDIT_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {
        AUDIT_STATUS_COMPLETED,
        AUDIT_STATUS_PARTIALLY_COMPLETED,
        AUDIT_STATUS_FAILED,
        AUDIT_STATUS_CANCELLED,
    }
)
# Statuses at which a cooperative cancel is still meaningful (a live worker can
# stop at its boundary).
AUDIT_ACTIVE_STATUSES: Final[frozenset[str]] = frozenset(
    {
        AUDIT_STATUS_DRAFT,
        AUDIT_STATUS_VALIDATING,
        AUDIT_STATUS_QUEUED,
        AUDIT_STATUS_RUNNING,
        AUDIT_STATUS_ANALYZING,
        AUDIT_STATUS_REPORTING,
    }
)

# --- Task (queue row) statuses -------------------------------------------
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
# Statuses a claim() may pick up (queued or ready-to-retry).
TASK_CLAIMABLE_STATUSES: Final[frozenset[str]] = frozenset(
    {TASK_STATUS_QUEUED, TASK_STATUS_RETRY_WAIT}
)
# Statuses a sweeper reclaims when their lease expires.
TASK_LEASED_STATUSES: Final[frozenset[str]] = frozenset(
    {TASK_STATUS_LEASED, TASK_STATUS_RUNNING}
)

# --- Attempt outcomes ----------------------------------------------------
ATTEMPT_STATUS_SUCCEEDED: Final = "succeeded"
ATTEMPT_STATUS_FAILED: Final = "failed"

# --- Audit lifecycle event types (SSE source) ----------------------------
EVENT_AUDIT_CREATED: Final = "audit.created"
EVENT_AUDIT_QUEUED: Final = "audit.queued"
EVENT_AUDIT_RUNNING: Final = "audit.running"
EVENT_AUDIT_STATUS: Final = "audit.status"
EVENT_TASK_SUCCEEDED: Final = "task.succeeded"
EVENT_TASK_FAILED: Final = "task.failed"
EVENT_TASK_RETRY: Final = "task.retry"
EVENT_AUDIT_CANCELLED: Final = "audit.cancelled"
EVENT_AUDIT_COMPLETED: Final = "audit.completed"

# --- Error tokens specific to the run lifecycle ---------------------------
# Provider-call error tokens live in ``provider_catalog`` (reused by the
# worker); these two are orchestration-level (no provider call involved).
ERROR_RUN_DEADLINE: Final = "run_deadline_exceeded"
ERROR_CANCELLED: Final = "cancelled"
ERROR_MAX_ATTEMPTS: Final = "max_attempts_exceeded"
ERROR_NO_CONNECTION: Final = "provider_connection_missing"

# --- Deterministic system instructions per benchmark mode -----------------
# Consumer-like sends no hidden instruction; the localized + forced-grounded
# modes prepend a neutral, brand-free instruction (invariant 6 — the brand list
# is never transmitted). Ported from the reference ``config/ai_visibility.py``.
LOCALIZED_INSTRUCTION: Final = (
    "Answer for a shopper in the market identified by ISO country code "
    "{country_code}, using language {language_code}. Prioritize retailers that "
    "serve that market and sources relevant to that market."
)
FORCED_GROUNDED_INSTRUCTION: Final = (
    "Answer the shopping question using current web information. "
    "Cite the sources supporting your recommendations."
)


def system_instruction_for_mode(
    *, mode: str, country_code: str, language_code: str
) -> str:
    """Resolve the neutral system instruction frozen onto an audit.

    Never contains any brand/competitor identity (invariant 6).
    """
    if mode == BENCHMARK_MODE_CONSUMER_LIKE:
        return ""
    localized = LOCALIZED_INSTRUCTION.format(
        country_code=(country_code or "unspecified"),
        language_code=(language_code or "unspecified"),
    )
    if mode == BENCHMARK_MODE_CONTROLLED_LOCALIZED:
        return localized
    # forced_grounded: localized + explicit grounding directive.
    return f"{localized} {FORCED_GROUNDED_INSTRUCTION}"


class AuditSettings(BaseSettings):
    """Provider-agnostic audit execution guardrails (env-overridable).

    One set of knobs bounds every audit so a stray or throttled run cannot run
    away in tokens, time, or duration regardless of provider.
    """

    model_config = SettingsConfigDict(env_prefix="AUDIT_", extra="ignore")

    # Hard cap on slots (prompts x engines x repetitions) an audit may create.
    max_tasks_per_audit: int = 500
    # Up to N tasks a single worker runs concurrently within its loop.
    worker_concurrency: int = 4
    # How long the loop sleeps when the queue is empty before polling again.
    poll_interval_seconds: float = 1.0
    # Minimum spacing between provider request starts, per transport, to respect
    # rate limits (mainly Gemini's low per-minute quota).
    min_request_interval_seconds: float = 0.0
    # Hard per-call ceiling enforced with ``asyncio.wait_for`` around the
    # provider call, independent of the HTTP client timeout.
    max_call_seconds: float = 90.0
    # Per-run wall-clock deadline. Once exceeded, remaining tasks stop at their
    # boundary and terminalize, so a run can never sit live forever.
    max_run_seconds: float = 1800.0
    # Retry budget for a single task (attempt_count is bounded by max_attempts).
    max_attempts: int = 5
    retry_base_delay_seconds: float = 2.0
    retry_max_delay_seconds: float = 45.0
    retry_jitter_seconds: float = 1.5
    # Lease TTL: a claimed task's lease expires after this many seconds unless
    # the worker heartbeats to extend it.
    lease_ttl_seconds: float = 120.0
    # Worker heartbeats at this cadence while a task runs.
    heartbeat_interval_seconds: float = 30.0
    # HTTP client timeout for a single provider call (passed to the adapter).
    request_timeout_seconds: float = 60.0

    def retry_delay(
        self, attempt: int, retry_after_seconds: float | None = None
    ) -> float:
        """Seconds to wait before the next attempt.

        Prefers a provider-advised ``Retry-After`` (clamped to the cap); else
        exponential backoff ``base * 2**attempt`` capped at the max, plus a
        small deterministic jitter (derived from ``attempt``, not RNG, so it
        stays reproducible).
        """
        cap = self.retry_max_delay_seconds
        if retry_after_seconds is not None:
            return min(retry_after_seconds, cap)
        base = self.retry_base_delay_seconds * (2**attempt)
        jitter = (attempt * 0.37) % 1.0 * self.retry_jitter_seconds
        return min(base, cap) + jitter


audit_settings = AuditSettings()
