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
from enum import StrEnum

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


class VisibilityTrendSov(BaseModel):
    """Both Share-of-Voice definitions for one trend point (projection only).

    ``response`` is the response-level SOV (brand response-presence share vs the
    competitors' response-presence rates); ``mention`` is the mention-level SOV
    derived from the persisted ``share_of_voice.mention_counts``. Both are
    deterministic reprojections of persisted metrics — no re-scoring (inv. 7).
    """

    response: float | None = None
    mention: float | None = None


class VisibilityTrendRankingRow(BaseModel):
    """One brand-vs-competitor ranking-history row within a trend point.

    Projected from the persisted snapshot(s) the point folds; for a bucket the
    mention counts are summed and the mention-level share recomputed from those
    sums (deterministic, no re-scoring — invariant 7).
    """

    name: str
    is_brand: bool = False
    mention_rate: float | None = None
    citation_rate: float | None = None
    share_of_voice: float | None = None
    mention_count: int = 0
    # Roadmap (B-2): present but null until an LLM stage is added.
    sentiment: str | None = None
    avg_position: float | None = None


class VisibilityTrendPoint(BaseModel):
    """One point in the cross-run Visibility trend (projection only, inv. 7).

    A raw per-run point projects a single persisted ``MetricSnapshot`` (its
    ``audit_id`` is set); a week/month bucket folds every contributing snapshot
    (``audit_id`` is null) and carries the full provenance list. ``sentiment``
    and ``avg_position`` stay null (decision B-2 / invariant 9). Version
    metadata lists every distinct analyzer/scoring version the point folds, with
    ``spans_version_boundary`` set when a bucket mixes versions.
    """

    audit_id: uuid.UUID | None = None
    completed_at: datetime
    logical_engine: str | None = None
    visibility_score: float | None = None
    brand_mention_rate: float | None = None
    owned_citation_rate: float | None = None
    sov: VisibilityTrendSov = Field(default_factory=VisibilityTrendSov)
    rankings: list[VisibilityTrendRankingRow] = Field(default_factory=list)
    # Roadmap (B-2): present but null until an LLM stage is added.
    sentiment: str | None = None
    avg_position: float | None = None
    # Provenance (invariant 4): every source snapshot this point folds.
    source_snapshot_ids: list[uuid.UUID] = Field(default_factory=list)
    # Distinct versions across the folded snapshots (invariant 4).
    analyzer_versions: list[str] = Field(default_factory=list)
    scoring_rule_versions: list[str] = Field(default_factory=list)
    spans_version_boundary: bool = False


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
    """One execution's persisted analysis + evidence (``GET /executions/{id}``).

    ``id``/``task_id`` are the *execution* (``AuditTask``) id — the id clients
    receive from ``GET /audits/{id}/executions`` and pass here. ``analysis_id``
    is the internal ``ResponseAnalysis`` id (exposed for traceability).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    analysis_id: uuid.UUID
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


# ---------------------------------------------------------------------------
# Execution-evidence projection for the Mentions & Citations and Query Fanout
# tabs (``GET /projects/{id}/visibility/evidence``). Every field is a pure
# projection of already-persisted rows — no provider is called and no evidence
# is inferred/backfilled at read time (invariant 7).
# ---------------------------------------------------------------------------


class VisibilityFanoutState(StrEnum):
    """Three-state query-fanout availability for one execution.

    - ``queries_available``: at least one stored search event has non-blank
      query text (e.g. a Gemini/Anthropic/OpenAI grounded response).
    - ``count_only``: search was used or the persisted count is positive, but no
      stored event carries query text (e.g. a legacy OpenRouter count-only row).
    - ``no_search``: neither a search signal nor a positive count is present.
    """

    QUERIES_AVAILABLE = "queries_available"
    COUNT_ONLY = "count_only"
    NO_SEARCH = "no_search"


class VisibilityEvidenceSearchEvent(BaseModel):
    """One normalized stored search event (projection only).

    Mirrors the persisted JSONB shape on ``AuditTask.search_events`` /
    ``RawResponseArtifact.search_events``. Empty query strings are preserved
    verbatim (a count-only event); query text is never invented.
    """

    sequence: int = 0
    query: str = ""
    call_id: str = ""
    call_sequence: int = 0
    query_sequence: int = 0


class VisibilityMentionEvidence(BaseModel):
    """One persisted brand/competitor mention row (projection only).

    Projected directly from ``BrandMention`` / ``CompetitorMention``; mentions
    are never inferred from answer text at read time.
    """

    kind: str  # "brand" | "competitor"
    name: str = ""
    first_offset: int | None = None
    artifact_id: uuid.UUID | None = None
    analyzer_version: str = ""


class VisibilityExecutionEvidence(BaseModel):
    """One execution's persisted mention/citation + query-fanout evidence.

    Read-only projection over ``ResponseAnalysis`` + its child mention/citation
    rows + the frozen ``AuditTask``/immutable ``RawResponseArtifact`` search
    events. ``transport_provider`` / ``transport_model`` stay plain strings so a
    historical (e.g. ``openrouter``) row still renders (invariant tolerant read).
    """

    audit_id: uuid.UUID
    task_id: uuid.UUID
    analysis_id: uuid.UUID
    artifact_id: uuid.UUID | None = None

    # Frozen prompt provenance (``prompt_id`` is nullable so a deleted source
    # prompt stays readable via its frozen text under "All prompts").
    prompt_snapshot_id: uuid.UUID
    prompt_id: uuid.UUID | None = None
    prompt_index: int = 0
    prompt_text: str = ""
    repetition: int = 0

    completed_at: datetime | None = None

    # Historical provenance strings (tolerant of retired transports).
    logical_engine: str = ""
    transport_provider: str = ""
    transport_model: str = ""

    # Query-fanout signals + derived availability state.
    search_used: bool = False
    search_query_count: int = 0
    query_text_available: bool = False
    state: VisibilityFanoutState = VisibilityFanoutState.NO_SEARCH
    search_events: list[VisibilityEvidenceSearchEvent] = Field(default_factory=list)
    event_source: str = "none"  # "raw_artifact" | "audit_task" | "none"

    mentions: list[VisibilityMentionEvidence] = Field(default_factory=list)
    citations: list[CitationEvidence] = Field(default_factory=list)


class VisibilityEvidenceResponse(BaseModel):
    """The shared persisted evidence dataset for the two evidence tabs.

    ``items`` is newest-first (by audit completion, prompt index, engine,
    repetition); ``truncated`` is set when more than ``limit`` matches exist.
    No offset/cursor/total (the endpoint returns a bounded newest window).
    """

    items: list[VisibilityExecutionEvidence] = Field(default_factory=list)
    truncated: bool = False
