# Audit event recording + guarded status transitions (shared by planner/worker).
#
# ``record_event`` appends an immutable ``AuditEvent`` (the SSE source,
# invariant 3). ``apply_transition`` runs a status change through the state
# machine (``orchestration/audit_state``) so an illegal transition raises rather
# than silently corrupting the lifecycle, and records the change as an event.
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.audits import EVENT_AUDIT_STATUS
from app.models.audit import Audit, AuditEvent
from app.orchestration.audit_state import transition_status


def record_event(
    session: AsyncSession,
    *,
    audit_id: uuid.UUID,
    event_type: str,
    message: str = "",
    payload: dict | None = None,
) -> AuditEvent:
    """Append a lifecycle event. Caller owns the commit (invariant 3)."""
    event = AuditEvent(
        audit_id=audit_id,
        event_type=event_type,
        message=message,
        payload=payload,
    )
    session.add(event)
    return event


def apply_transition(
    session: AsyncSession,
    *,
    audit: Audit,
    target: str,
    message: str = "",
    payload: dict | None = None,
) -> str:
    """Validate + apply an audit status transition and record it.

    Raises ``InvalidAuditTransition`` (from the state machine) on an illegal
    move. Records an ``audit.status`` event. Caller owns the commit.
    """
    new_status = transition_status(audit.status, target)
    if new_status != audit.status:
        audit.status = new_status
        record_event(
            session,
            audit_id=audit.id,
            event_type=EVENT_AUDIT_STATUS,
            message=message or f"status -> {new_status}",
            payload={**(payload or {}), "status": new_status},
        )
    return new_status
