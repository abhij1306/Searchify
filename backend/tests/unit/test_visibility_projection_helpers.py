"""Pure-function regression tests for visibility projection helpers.

Covers two review-hardening fixes in ``app.domain.analysis.service``:
  * ``_normalize_events`` skips malformed entries (no recognized event keys)
    so the evidence endpoint never surfaces phantom all-zero events.
  * ``_mention_sov_of`` aggregates brand share across every brand key present
    in a bucket, so a brand rename across snapshots does not undercount SOV.
"""

from __future__ import annotations

import pytest

from app.domain.analysis.service import _mention_sov_of, _normalize_events


def test_normalize_events_skips_malformed_entries() -> None:
    raw = [
        {"sequence": 0, "query": "shoes"},  # valid
        {},  # phantom — no recognized keys
        {"foo": "bar"},  # phantom — unrecognized key only
        "not-a-dict",  # ignored
        {"call_id": "c1"},  # valid (recognized key present)
    ]
    events = _normalize_events(raw)
    assert len(events) == 2
    assert events[0].query == "shoes"
    assert events[1].call_id == "c1"


def test_normalize_events_preserves_empty_query() -> None:
    # A count-only event legitimately carries an empty query string.
    events = _normalize_events([{"sequence": 0, "query": ""}])
    assert len(events) == 1
    assert events[0].query == ""


def test_normalize_events_non_list_is_empty() -> None:
    assert _normalize_events(None) == []
    assert _normalize_events({"query": "x"}) == []


def test_mention_sov_aggregates_across_renamed_brand_keys() -> None:
    # Two brand keys ("Acme", "Acme Corp") summed into one bucket.
    counts = {"Acme": 3, "Acme Corp": 2, "Rival": 5}
    sov = _mention_sov_of(counts, {"Acme", "Acme Corp"})
    # (3 + 2) / (3 + 2 + 5) = 0.5 — not 3/10 or 2/10 from a single name.
    assert sov == pytest.approx(0.5)


def test_mention_sov_single_name() -> None:
    assert _mention_sov_of({"Acme": 1, "Rival": 3}, {"Acme"}) == pytest.approx(0.25)


def test_mention_sov_zero_total_is_none() -> None:
    assert _mention_sov_of({"Acme": 0, "Rival": 0}, {"Acme"}) is None
