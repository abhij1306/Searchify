# Analysis/metrics + dashboard response DTOs (B6, projections only — invariant 7).
#
# Every response here is a PROJECTION of persisted analysis rows: the metrics
# endpoint serves the ``MetricSnapshot``, the dashboard serves a server-side
# view over the same snapshot, and the execution-evidence endpoint serves one
# ``ResponseAnalysis`` + its child rows. No provider is ever called to build
# these (invariant 7). Sentiment + average position are present but null until
# the roadmap adds an LLM stage (decision B-2).
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MetricsResponse(BaseModel):
    """Single-run ``MetricSnapshot`` projection (``GET /audits/{id}/metrics``)."""

    model_config = ConfigDict(from_attributes=True)

    audit_id: uuid.UUID
    project_id: uuid.UUID
    analyzer_version: str
    scoring_rule_version: str
    total_completed: int
    total_failed: int
    visibility_score: float
    metrics: dict = Field(default_factory=dict)
    created_at: datetime


class RankingRow(BaseModel):
    """One brand-vs-competitor rankings-table row for the dashboard."""

    name: str
    is_brand: bool = False
    mention_rate: float | None = None
    citation_rate: float | None = None
    share_of_voice: float | None = None
    mention_count: int = 0
    # Roadmap (B-2): present but null until an LLM stage is added.
    sentiment: str | None = None
    avg_position: float | None = None


class EngineComparisonRow(BaseModel):
    """One per-engine comparison row for the selected run."""

    logical_engine: str
    total_completed: int
    brand_mention_rate: float | None = None
    owned_citation_rate: float | None = None
    search_use_rate: float | None = None
    visibility_score: float | None = None


class VisibilityResponse(BaseModel):
    """Selected-run dashboard projection (``GET /projects/{id}/visibility``).

    Computed server-side from the persisted ``MetricSnapshot`` for the selected
    audit (defaults to the project's latest completed audit). No cross-run trend
    at MVP (roadmap). Visibility % + SOV are populated; sentiment + average
    position are present but null (decision B-2).
    """

    project_id: uuid.UUID
    audit_id: uuid.UUID
    audit_status: str
    analyzer_version: str
    scoring_rule_version: str
    total_completed: int
    total_failed: int
    visibility_score: float
    rankings: list[RankingRow] = Field(default_factory=list)
    per_engine: list[EngineComparisonRow] = Field(default_factory=list)
    # Roadmap (B-2): present but null until an LLM stage is added.
    sentiment: str | None = None
    avg_position: float | None = None
    created_at: datetime


class CitationEvidence(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ordinal: int
    url: str = ""
    title: str = ""
    domain: str = ""
    classification: str = "third_party"
    is_owned: bool = False
    is_unintended: bool = False
    matched_competitor: str | None = None


class ExecutionEvidenceResponse(BaseModel):
    """One execution's persisted analysis + evidence (``GET /executions/{id}``)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    audit_id: uuid.UUID
    task_id: uuid.UUID
    artifact_id: uuid.UUID | None = None
    analyzer_version: str
    scoring_rule_version: str
    logical_engine: str = ""
    transport_provider: str = ""
    transport_model: str = ""
    prompt_index: int
    repetition: int
    prompt_class: str = ""
    brand_mentioned: bool = False
    brand_first_offset: int | None = None
    owned_domain_cited: bool = False
    owned_citation_count: int = 0
    unintended_domain_cited: bool = False
    citation_count: int = 0
    search_used: bool = False
    search_query_count: int = 0
    # Roadmap (B-2): present but null until an LLM stage is added.
    sentiment: str | None = None
    avg_position: float | None = None
    score: dict | None = None
    citations: list[CitationEvidence] = Field(default_factory=list)
    competitors_mentioned: list[str] = Field(default_factory=list)
    created_at: datetime
