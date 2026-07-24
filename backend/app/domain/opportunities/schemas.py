# Opportunities API request/response DTOs.
#
# Every response model mirrors the checked-in strict frontend zod schema in
# ``frontend/lib/api/schemas.ts`` field-for-field so the two contracts can
# never drift (the frontend parses each payload with ``.strict()`` — an extra
# or missing key fails loud). The service builds these from persisted rows
# only; nothing here re-scores, fetches, or fabricates a metric.
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.config.opportunities import OPPORTUNITY_STATUSES


class _Model(BaseModel):
    # Reject unknown keys on the way IN (request bodies) as loudly as the
    # frontend rejects them on the way OUT.
    model_config = ConfigDict(extra="forbid")


# =========================================================================
# Requests
# =========================================================================
class OpportunityStatusPatch(_Model):
    """PATCH body — ``status`` is the ONLY mutable field on an Opportunity."""

    status: str

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str) -> str:
        if value not in OPPORTUNITY_STATUSES:
            raise ValueError(f"unknown opportunity status: {value!r}")
        return value


class RecomputeRequest(_Model):
    """Optional recompute scope — omit both for the latest dashboard sources."""

    audit_id: uuid.UUID | None = None
    site_crawl_id: uuid.UUID | None = None


# =========================================================================
# Responses
# =========================================================================
class OpportunityItem(_Model):
    """One live opportunity row as rendered by the priority-sorted catalog."""

    id: uuid.UUID
    project_id: uuid.UUID
    rule_id: str
    opportunity_type: str
    severity: str
    priority_score: float
    title: str
    target_key: str
    target_prompt_id: uuid.UUID | None
    target_url: str | None
    target_theme: str | None
    status: str
    created_at: str
    updated_at: str


class OpportunityDetail(OpportunityItem):
    """Full evidence bundle + provenance for one opportunity."""

    remediation: str
    evidence: dict
    source_analysis_ids: list[str]
    source_issue_ids: list[str]
    source_metric_ids: list[str]
    source_traffic_ids: list[str]
    analyzer_version: str
    rule_version: str
    formula_version: str
    superseded_by_id: uuid.UUID | None
    superseded_at: str | None


class OpportunitiesPage(_Model):
    items: list[OpportunityItem]
    next_cursor: str | None


class OpportunitySummary(_Model):
    """Latest recompute snapshot projection (``computed=false`` when none)."""

    computed: bool
    run_id: uuid.UUID | None
    audit_id: uuid.UUID | None
    site_crawl_id: uuid.UUID | None
    counts_by_type: dict[str, int]
    counts_by_severity: dict[str, int]
    counts_by_status: dict[str, int]
    total_count: int
    median_priority: float | None
    analyzer_version: str
    rule_version: str
    formula_version: str
    computed_at: str | None


class RecomputeResponse(_Model):
    """The immutable snapshot written by one recompute run."""

    id: uuid.UUID
    run_id: uuid.UUID
    audit_id: uuid.UUID | None
    site_crawl_id: uuid.UUID | None
    counts_by_type: dict[str, int]
    counts_by_severity: dict[str, int]
    counts_by_status: dict[str, int]
    total_count: int
    median_priority: float | None
    analyzer_version: str
    rule_version: str
    formula_version: str
    created_at: str
