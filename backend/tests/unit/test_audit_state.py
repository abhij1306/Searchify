"""Audit state-machine transition rules (invariant 9).

Guards the single source of truth for legal audit lifecycle transitions: the
happy path advances, terminal states are dead ends, and any illegal move raises
``InvalidAuditTransition`` rather than silently corrupting the lifecycle.
"""

from __future__ import annotations

import pytest

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
from app.orchestration.audit_state import (
    InvalidAuditTransition,
    can_transition,
    normalize_status,
    transition_status,
)


def test_happy_path_transitions_are_legal() -> None:
    chain = [
        AUDIT_STATUS_DRAFT,
        AUDIT_STATUS_VALIDATING,
        AUDIT_STATUS_QUEUED,
        AUDIT_STATUS_RUNNING,
        AUDIT_STATUS_ANALYZING,
        AUDIT_STATUS_REPORTING,
        AUDIT_STATUS_COMPLETED,
    ]
    for current, target in zip(chain, chain[1:], strict=False):
        assert can_transition(current, target)
        assert transition_status(current, target) == target


def test_self_transition_is_a_noop() -> None:
    assert transition_status(AUDIT_STATUS_RUNNING, AUDIT_STATUS_RUNNING) == (
        AUDIT_STATUS_RUNNING
    )
    assert can_transition(AUDIT_STATUS_RUNNING, AUDIT_STATUS_RUNNING)


@pytest.mark.parametrize(
    "source",
    [
        AUDIT_STATUS_RUNNING,
        AUDIT_STATUS_ANALYZING,
    ],
)
def test_partial_completed_reachable_mid_lifecycle(source: str) -> None:
    assert transition_status(source, AUDIT_STATUS_PARTIALLY_COMPLETED) == (
        AUDIT_STATUS_PARTIALLY_COMPLETED
    )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (AUDIT_STATUS_DRAFT, AUDIT_STATUS_RUNNING),  # skips validating/queued
        (AUDIT_STATUS_QUEUED, AUDIT_STATUS_COMPLETED),  # skips execution
        (AUDIT_STATUS_COMPLETED, AUDIT_STATUS_RUNNING),  # terminal is a dead end
        (AUDIT_STATUS_FAILED, AUDIT_STATUS_QUEUED),
        (AUDIT_STATUS_CANCELLED, AUDIT_STATUS_RUNNING),
        (AUDIT_STATUS_REPORTING, AUDIT_STATUS_CANCELLED),  # too late to cancel
    ],
)
def test_illegal_transitions_raise(current: str, target: str) -> None:
    assert not can_transition(current, target)
    with pytest.raises(InvalidAuditTransition):
        transition_status(current, target)


def test_unknown_source_status_raises() -> None:
    with pytest.raises(InvalidAuditTransition):
        transition_status("not_a_status", AUDIT_STATUS_RUNNING)


def test_normalize_status_lowercases_and_strips() -> None:
    assert normalize_status("  RUNNING  ") == AUDIT_STATUS_RUNNING
