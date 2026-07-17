# Audit lifecycle state machine (invariant 9 — cooperative, deterministic).
#
# An ``_ALLOWED_TRANSITIONS`` / ``transition_status`` table for the Searchify
# audit lifecycle. A single source of truth for
# which status transitions are legal; an illegal transition raises so a caller
# can never silently drive an audit into an impossible state.
#
# Lifecycle (docs/backend-architecture.md §11):
#
#   DRAFT -> VALIDATING -> QUEUED -> RUNNING -> ANALYZING -> REPORTING -> COMPLETED
#   VALIDATING -> FAILED
#   RUNNING/ANALYZING -> PARTIALLY_COMPLETED
#   QUEUED/RUNNING -> CANCELLED
from __future__ import annotations

from app.core.config.audits import (
    AUDIT_STATUS_ANALYZING,
    AUDIT_STATUS_CANCELLED,
    AUDIT_STATUS_COMPLETED,
    AUDIT_STATUS_DRAFT,
    AUDIT_STATUS_FAILED,
    AUDIT_STATUS_PARTIALLY_COMPLETED,
    AUDIT_STATUS_QUEUED,
    AUDIT_STATUS_REPORTING,
    AUDIT_STATUS_RUNNING,
    AUDIT_STATUS_VALIDATING,
)


class InvalidAuditTransition(ValueError):
    """Raised when an audit status transition is not permitted."""


# The legal target statuses reachable from each status. A terminal status maps
# to the empty set. Modeled directly on the reference ``_ALLOWED_TRANSITIONS``.
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    AUDIT_STATUS_DRAFT: {AUDIT_STATUS_VALIDATING, AUDIT_STATUS_CANCELLED},
    AUDIT_STATUS_VALIDATING: {
        AUDIT_STATUS_QUEUED,
        AUDIT_STATUS_FAILED,
        AUDIT_STATUS_CANCELLED,
    },
    AUDIT_STATUS_QUEUED: {
        AUDIT_STATUS_RUNNING,
        AUDIT_STATUS_CANCELLED,
    },
    AUDIT_STATUS_RUNNING: {
        AUDIT_STATUS_ANALYZING,
        AUDIT_STATUS_PARTIALLY_COMPLETED,
        AUDIT_STATUS_FAILED,
        AUDIT_STATUS_CANCELLED,
    },
    AUDIT_STATUS_ANALYZING: {
        AUDIT_STATUS_REPORTING,
        AUDIT_STATUS_PARTIALLY_COMPLETED,
        AUDIT_STATUS_FAILED,
        AUDIT_STATUS_CANCELLED,
    },
    AUDIT_STATUS_REPORTING: {
        AUDIT_STATUS_COMPLETED,
        AUDIT_STATUS_PARTIALLY_COMPLETED,
        AUDIT_STATUS_FAILED,
    },
    AUDIT_STATUS_COMPLETED: set(),
    AUDIT_STATUS_PARTIALLY_COMPLETED: set(),
    AUDIT_STATUS_FAILED: set(),
    AUDIT_STATUS_CANCELLED: set(),
}


def normalize_status(value: str) -> str:
    """Normalize a raw status string to the canonical lower-case token."""
    return str(value).strip().lower()


def can_transition(current: str, target: str) -> bool:
    """True when ``current -> target`` is a legal transition (or a self-loop)."""
    current_status = normalize_status(current)
    target_status = normalize_status(target)
    if current_status == target_status:
        return True
    return target_status in _ALLOWED_TRANSITIONS.get(current_status, set())


def transition_status(current: str, target: str) -> str:
    """Validate and return the target status, or raise on an illegal move.

    A self-transition (``current == target``) is a no-op that returns the
    target. Any target not reachable from ``current`` raises
    ``InvalidAuditTransition``. Unknown source statuses also raise.
    """
    current_status = normalize_status(current)
    target_status = normalize_status(target)
    if current_status not in _ALLOWED_TRANSITIONS:
        raise InvalidAuditTransition(f"Unknown audit status: {current_status}")
    if current_status == target_status:
        return target_status
    if target_status not in _ALLOWED_TRANSITIONS[current_status]:
        raise InvalidAuditTransition(
            f"Invalid audit status transition: {current_status} -> {target_status}"
        )
    return target_status
