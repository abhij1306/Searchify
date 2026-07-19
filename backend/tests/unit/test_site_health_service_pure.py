"""Unit tests for the pure Site Health service/normalization helpers.

Covers the logic that does not need a database:
  - typed keyset cursors: fingerprint stability, cross-scope/cross-filter
    replay rejection (``CursorScopeError``), and tamper rejection
    (``ValueError``);
  - ``display_label_for`` current labels + rule-id fallback;
  - ``presentation_status_for`` derivation, including the policy ``blocked``
    vs generic ``error`` split and the invariant that ``failed`` is never
    surfaced as page copy.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from app.core.config.site_health import (
    ERROR_ROBOTS_DENIED,
    ERROR_SSRF_BLOCKED,
    PAGE_ANALYSIS_STATUS_COMPLETED,
    PAGE_ANALYSIS_STATUS_PARTIALLY_COMPLETED,
)
from app.core.config.task_queue import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_LEASED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCEEDED,
)
from app.domain.site_health.normalization import (
    CursorScopeError,
    decode_keyset_cursor,
    encode_keyset_cursor,
    filter_fingerprint,
)
from app.domain.site_health.service import (
    display_label_for,
    presentation_status_for,
)
from app.models.site_health import SiteCrawlTask, SitePageAnalysis

# --------------------------------------------------------------------------
# Keyset cursors
# --------------------------------------------------------------------------


def test_fingerprint_is_stable_and_ignores_empty_values() -> None:
    a = filter_fingerprint("pages", {"status": "completed", "monitored": None})
    b = filter_fingerprint("pages", {"status": "completed"})
    c = filter_fingerprint("pages", {"status": "completed", "monitored": ""})
    assert a == b == c


def test_fingerprint_changes_on_scope_or_filter() -> None:
    base = filter_fingerprint("pages", {"status": "completed"})
    assert base != filter_fingerprint("inventory", {"status": "completed"})
    assert base != filter_fingerprint("pages", {"status": "error"})
    assert base != filter_fingerprint("pages", {"monitored": True})


def test_cursor_round_trips_within_same_scope_and_filters() -> None:
    scope, filters = "pages", {"status": "completed"}
    cursor = encode_keyset_cursor(
        scope=scope, filters=filters, sort_values=["https://x/a", "id-1"]
    )
    assert decode_keyset_cursor(cursor, scope=scope, filters=filters) == [
        "https://x/a",
        "id-1",
    ]


def test_cursor_replay_across_filters_raises_scope_error() -> None:
    cursor = encode_keyset_cursor(
        scope="pages", filters={"status": "completed"}, sort_values=["u", "i"]
    )
    with pytest.raises(CursorScopeError):
        decode_keyset_cursor(cursor, scope="pages", filters={"status": "error"})


def test_cursor_replay_across_scope_raises_scope_error() -> None:
    cursor = encode_keyset_cursor(scope="pages", filters={}, sort_values=["u", "i"])
    with pytest.raises(CursorScopeError):
        decode_keyset_cursor(cursor, scope="inventory", filters={})


def test_tampered_cursor_raises_value_error() -> None:
    with pytest.raises(ValueError):
        decode_keyset_cursor("!!!not-base64!!!", scope="pages", filters={})


# --------------------------------------------------------------------------
# Display labels
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule_id", "expected"),
    [
        ("technical.title_present", "Missing page title"),
        ("technical.canonical_present", "Missing canonical URL"),
        ("technical.indexable", "Page blocked from indexing"),
        ("aeo.structured_data_present", "Missing structured data"),
    ],
)
def test_display_label_for_known_rules(rule_id: str, expected: str) -> None:
    assert display_label_for(rule_id) == expected


def test_display_label_for_unknown_rule_falls_back_to_rule_id() -> None:
    assert display_label_for("does.not.exist") == "does.not.exist"


# --------------------------------------------------------------------------
# Presentation status derivation
# --------------------------------------------------------------------------


def _analysis(status: str) -> SimpleNamespace:
    return SimpleNamespace(status=status)


def _task(status: str, error_code: str = "") -> SimpleNamespace:
    return SimpleNamespace(status=status, error_code=error_code)


def test_completed_analysis_wins() -> None:
    st, code = presentation_status_for(
        analysis=cast(SitePageAnalysis, _analysis(PAGE_ANALYSIS_STATUS_COMPLETED)),
        monitored=True,
        latest_analyze_task=cast(
            SiteCrawlTask, _task(TASK_STATUS_FAILED, ERROR_SSRF_BLOCKED)
        ),
    )
    assert st == PAGE_ANALYSIS_STATUS_COMPLETED
    assert code == ""


def test_partially_completed_analysis_wins() -> None:
    st, code = presentation_status_for(
        analysis=cast(
            SitePageAnalysis, _analysis(PAGE_ANALYSIS_STATUS_PARTIALLY_COMPLETED)
        ),
        monitored=False,
        latest_analyze_task=None,
    )
    assert st == PAGE_ANALYSIS_STATUS_PARTIALLY_COMPLETED
    assert code == ""


@pytest.mark.parametrize("code", [ERROR_ROBOTS_DENIED, ERROR_SSRF_BLOCKED])
def test_policy_denial_maps_to_blocked(code: str) -> None:
    st, out_code = presentation_status_for(
        analysis=None,
        monitored=True,
        latest_analyze_task=cast(SiteCrawlTask, _task(TASK_STATUS_FAILED, code)),
    )
    assert st == "blocked"
    assert out_code == code


def test_other_terminal_failure_maps_to_error_not_failed() -> None:
    st, code = presentation_status_for(
        analysis=None,
        monitored=True,
        latest_analyze_task=cast(SiteCrawlTask, _task(TASK_STATUS_FAILED, "timeout")),
    )
    assert st == "error"
    assert st != "failed"
    assert code == "timeout"


def test_cancelled_without_code_maps_to_cancelled() -> None:
    st, code = presentation_status_for(
        analysis=None,
        monitored=True,
        latest_analyze_task=cast(SiteCrawlTask, _task(TASK_STATUS_CANCELLED, "")),
    )
    assert st == "cancelled"
    assert code == ""


def test_succeeded_task_without_analysis_is_pending() -> None:
    st, _ = presentation_status_for(
        analysis=None,
        monitored=True,
        latest_analyze_task=cast(SiteCrawlTask, _task(TASK_STATUS_SUCCEEDED)),
    )
    assert st == "pending"


@pytest.mark.parametrize("status", [TASK_STATUS_RUNNING, TASK_STATUS_LEASED])
def test_in_flight_task_is_running(status: str) -> None:
    st, _ = presentation_status_for(
        analysis=None,
        monitored=True,
        latest_analyze_task=cast(SiteCrawlTask, _task(status)),
    )
    assert st == "running"


def test_queued_task_is_pending() -> None:
    st, _ = presentation_status_for(
        analysis=None,
        monitored=True,
        latest_analyze_task=cast(SiteCrawlTask, _task(TASK_STATUS_QUEUED)),
    )
    assert st == "pending"


def test_monitored_with_nothing_is_pending() -> None:
    st, _ = presentation_status_for(
        analysis=None, monitored=True, latest_analyze_task=None
    )
    assert st == "pending"


def test_unmonitored_with_nothing_is_not_selected() -> None:
    st, _ = presentation_status_for(
        analysis=None, monitored=False, latest_analyze_task=None
    )
    assert st == "not_selected"
