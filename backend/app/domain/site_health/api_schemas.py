# Site Health API request/response DTOs (Slice 6).
#
# Every response model mirrors the checked-in strict frontend zod schema in
# ``frontend/lib/api/schemas.ts`` field-for-field so the two contracts can never
# drift (the frontend parses each payload with ``.strict()`` — an extra or
# missing key fails loud). The API layer builds these DTOs from persisted rows
# only (the service owns the projection rules); nothing here re-scores, fetches,
# or fabricates a metric. Count-bearing fields the backend redacts for a Free
# workspace are ``None`` (never a number), never leaking a full-site total.
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Presentation-status literals (superset of the persisted page-analysis states,
# adding the mockup-facing `not_selected` / `error` / `blocked` / `cancelled`).
PageAnalysisStatus = Literal[
    "not_selected",
    "pending",
    "running",
    "completed",
    "partially_completed",
    "failed",
    "error",
    "blocked",
    "cancelled",
]
AccessMode = Literal["sample", "selection"]
IssueSeverity = Literal["critical", "high", "medium", "low", "info"]
IssueDimension = Literal["technical", "aeo"]
SiteUrlSource = Literal["root", "link", "sitemap", "redirect"]
SelectionSource = Literal["user", "free_sample"]


class _Model(BaseModel):
    # Reject unknown keys on the way IN (request bodies) as loudly as the
    # frontend rejects them on the way OUT.
    model_config = ConfigDict(extra="forbid")


# =========================================================================
# Requests
# =========================================================================
class CreateCrawlRequest(_Model):
    project_id: uuid.UUID
    include_globs: list[str] | None = None
    exclude_globs: list[str] | None = None
    seed: str | None = None


class ReplaceMonitoredRequest(_Model):
    site_url_ids: list[uuid.UUID]
    expected_selection_version: int


# =========================================================================
# Entitlement
# =========================================================================
class EntitlementResponse(_Model):
    workspace_id: uuid.UUID
    plan_key: Literal["free", "starter"]
    access_mode: AccessMode
    sample_url_limit: int
    monitored_url_limit: int
    can_view_discovered_total: bool
    capability_revision: int
    created_at: str
    updated_at: str


# =========================================================================
# Crawl
# =========================================================================
class ScoreSummary(_Model):
    overall_score: float | None
    technical_score: float | None
    aeo_score: float | None
    selected_count: int
    analyzed_count: int
    issue_count: int
    scoring_version: str


class CrawlResponse(_Model):
    id: uuid.UUID
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    profile_id: uuid.UUID
    status: str
    discovery_status: str
    analysis_status: str
    root_url: str
    sample_mode: bool
    seed: str
    inventory_complete: bool
    visible_url_count: int
    analyzed_count: int
    failed_count: int
    # Redactable count fields (Free → None, never a number).
    discovered_count: int | None = None
    total_url_count: int | None = None
    has_more_site_urls: bool | None = None
    score_summary: ScoreSummary | None = None
    extractor_version: str
    analyzer_version: str
    rule_version: str
    scoring_version: str
    error_message: str
    created_at: str
    updated_at: str
    started_at: str | None
    completed_at: str | None


class CrawlListPage(_Model):
    items: list[CrawlResponse]
    next_cursor: str | None


# =========================================================================
# Inventory
# =========================================================================
class InventoryRow(_Model):
    site_url_id: uuid.UUID
    normalized_url: str
    display_url: str
    title: str | None
    content_type: str | None
    source: SiteUrlSource | None
    depth: int | None
    monitored: bool
    first_seen_at: str | None
    last_seen_at: str | None
    issue_count: int | None
    technical_score: float | None
    aeo_score: float | None
    overall_score: float | None
    last_audited: str | None


class InventoryPage(_Model):
    items: list[InventoryRow]
    next_cursor: str | None


# =========================================================================
# Monitored set
# =========================================================================
class MonitoredQuota(_Model):
    used: int
    limit: int


class MonitoredUrl(_Model):
    site_url_id: uuid.UUID
    normalized_url: str
    display_url: str
    title: str | None
    active: bool
    selection_source: SelectionSource
    selected_at: str | None
    deselected_at: str | None


class MonitoredUrlsResponse(_Model):
    project_id: uuid.UUID
    selection_version: int
    monitored_urls: list[MonitoredUrl]
    quota: MonitoredQuota


# =========================================================================
# Pages
# =========================================================================
class PageSummary(_Model):
    site_url_id: uuid.UUID
    crawl_id: uuid.UUID
    normalized_url: str
    display_url: str
    title: str | None
    monitored: bool
    analysis_status: PageAnalysisStatus
    error_code: str
    issue_count: int | None
    technical_score: float | None
    aeo_score: float | None
    overall_score: float | None
    last_audited: str | None


class PagesPage(_Model):
    items: list[PageSummary]
    next_cursor: str | None


class PageFacts(_Model):
    title: str | None
    meta_description: str | None
    canonical_url: str | None
    robots_directives: list[str]
    h1_count: int
    heading_count: int
    image_count: int
    image_missing_alt_count: int
    word_count: int
    internal_link_count: int
    external_link_count: int
    structured_data_types: list[str]


class DeliveryFacts(_Model):
    field_cwv_available: Literal[False] = False
    status_code: int | None
    ttfb_ms: float | None
    wire_bytes: int | None
    decoded_bytes: int | None
    html_bytes: int | None
    http_version: str | None
    compression: str | None
    cache_control: str | None
    blocking_resource_count: int | None


class SiteIssue(_Model):
    id: uuid.UUID
    crawl_id: uuid.UUID
    rule_id: str
    dimension: IssueDimension
    category: str
    severity: IssueSeverity
    title: str
    remediation: str
    affected_url_count: int
    analyzer_version: str
    rule_version: str
    created_at: str


class PageDetail(_Model):
    site_url_id: uuid.UUID
    crawl_id: uuid.UUID
    normalized_url: str
    display_url: str
    title: str | None
    analysis_status: PageAnalysisStatus
    error_code: str
    field_cwv_available: Literal[False] = False
    technical_score: float | None
    aeo_score: float | None
    overall_score: float | None
    issue_count: int | None
    last_audited: str | None
    facts: PageFacts
    delivery: DeliveryFacts
    issues: list[SiteIssue]
    artifact_id: uuid.UUID | None
    extractor_version: str
    analyzer_version: str
    rule_version: str
    scoring_version: str


# =========================================================================
# Issues (grouped) + detail + history
# =========================================================================
class AffectedUrl(_Model):
    site_url_id: uuid.UUID
    normalized_url: str
    display_url: str
    title: str | None


class IssuesSummary(_Model):
    issue_count: int
    severity_counts: dict[str, int]
    affected_url_count: int
    monitored_affected_url_count: int


class SiteIssuesPage(_Model):
    items: list[SiteIssue]
    next_cursor: str | None
    summary: IssuesSummary


class SiteIssueDetail(_Model):
    id: uuid.UUID
    crawl_id: uuid.UUID
    rule_id: str
    dimension: IssueDimension
    category: str
    severity: IssueSeverity
    title: str
    remediation: str
    evidence: dict[str, object]
    affected_urls: list[AffectedUrl]
    affected_url_count: int
    analyzer_version: str
    rule_version: str
    created_at: str
    next_cursor: str | None = None


class IssueHistoryRow(_Model):
    id: uuid.UUID
    crawl_id: uuid.UUID
    rule_id: str
    dimension: IssueDimension
    category: str
    severity: IssueSeverity
    title: str
    remediation: str
    analyzer_version: str
    rule_version: str
    created_at: str


class IssueHistoryPage(_Model):
    items: list[IssueHistoryRow]
    next_cursor: str | None


# =========================================================================
# Events + dashboard
# =========================================================================
class CrawlEvent(_Model):
    id: uuid.UUID
    crawl_id: uuid.UUID
    event_type: str
    message: str
    payload: dict[str, object]
    created_at: str


class DashboardResponse(_Model):
    project_id: uuid.UUID
    crawl: CrawlResponse | None
    score_summary: ScoreSummary | None
    quota: MonitoredQuota


class SiteHealthError(_Model):
    code: str
    message: str
    limit: int | None = None
    currently_used: int | None = None
    expected_selection_version: int | None = None
    current_selection_version: int | None = Field(default=None)
