# Site Health configuration (invariant 1: all config lives in core/config).
#
# Owns EVERY tunable knob, enum, and version string for the Site Health
# (HTTP-level technical/AEO crawler) subsystem: Free/Starter workspace
# capabilities (keyed by capability, never by a plan display name), the
# lifecycle state vocabularies (crawl / discovery / analysis sub-states and the
# queue-neutral task states reused from ``config/task_queue``), the secure
# crawler/fetch/frontier/robots/sitemap limits, the URL-normalization knobs, the
# retry/lease queue settings, the deterministic rule catalog, the structured
# schema-property maps, and all extractor/analyzer/rule/scoring versions.
#
# Domain, connector, analysis, worker, and API code READS these; it never
# hard-codes the literals inline. Operational values are frozen into each
# ``SiteCrawl.configuration`` at creation so a live env change never alters an
# in-flight run (matches the audit determinism contract, invariant 9).
from __future__ import annotations

from typing import Final

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.task_queue import (
    ERROR_MAX_ATTEMPTS,
    PostgresQueueSpec,
)

# =========================================================================
# Entitlement capabilities (keyed by CAPABILITY, not plan display name)
# =========================================================================
# The two approved capability keys. These are stable machine keys used for the
# workspace entitlement row + every capability-based redaction decision. A
# user-facing "plan display name" (marketing label) is intentionally NOT stored
# or matched here — capability is the single source of truth.
CAPABILITY_FREE: Final = "free"
CAPABILITY_STARTER: Final = "starter"
SITE_HEALTH_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {CAPABILITY_FREE, CAPABILITY_STARTER}
)
# The capability seeded/resolved for a workspace that has none yet.
DEFAULT_SITE_HEALTH_CAPABILITY: Final = CAPABILITY_FREE

# Discovery modes: Free crawls a deterministic bounded SAMPLE; Starter runs a
# progressive FULL inventory.
DISCOVERY_MODE_SAMPLE: Final = "sample"
DISCOVERY_MODE_FULL: Final = "full"

# Free: deterministic automatic sample of 10 admitted URLs across the whole
# workspace; no user selection; no monitored set beyond the system sample.
FREE_SAMPLE_URL_LIMIT: Final = 10
FREE_MONITORED_URL_LIMIT: Final = 10

# Starter: progressive inventory; up to 50 active monitored URLs workspace-wide.
STARTER_MONITORED_URL_LIMIT: Final = 50


class SiteHealthCapability:
    """A frozen capability profile resolved from the workspace entitlement.

    Immutable, value-typed record of exactly what a capability may do. Built
    from the config constants above so there is one owner for every limit and
    disclosure flag. ``count_disclosure`` gates whether total/frontier/overflow
    counts may ever leave the backend (Free = never; Starter = yes).
    """

    __slots__ = (
        "capability",
        "discovery_mode",
        "discovery_url_cap",
        "sample_url_limit",
        "monitored_url_limit",
        "allows_user_selection",
        "count_disclosure",
    )

    def __init__(
        self,
        *,
        capability: str,
        discovery_mode: str,
        discovery_url_cap: int | None,
        sample_url_limit: int,
        monitored_url_limit: int,
        allows_user_selection: bool,
        count_disclosure: bool,
    ) -> None:
        self.capability = capability
        self.discovery_mode = discovery_mode
        # None means "no hard discovery cap" (Starter). Free caps at 10.
        self.discovery_url_cap = discovery_url_cap
        self.sample_url_limit = sample_url_limit
        self.monitored_url_limit = monitored_url_limit
        self.allows_user_selection = allows_user_selection
        self.count_disclosure = count_disclosure


# The two capability profiles, keyed by capability. Everything that must know
# "what can this workspace do" resolves through ``capability_profile``.
_CAPABILITY_PROFILES: Final[dict[str, SiteHealthCapability]] = {
    CAPABILITY_FREE: SiteHealthCapability(
        capability=CAPABILITY_FREE,
        discovery_mode=DISCOVERY_MODE_SAMPLE,
        discovery_url_cap=FREE_SAMPLE_URL_LIMIT,
        sample_url_limit=FREE_SAMPLE_URL_LIMIT,
        monitored_url_limit=FREE_MONITORED_URL_LIMIT,
        allows_user_selection=False,
        count_disclosure=False,
    ),
    CAPABILITY_STARTER: SiteHealthCapability(
        capability=CAPABILITY_STARTER,
        discovery_mode=DISCOVERY_MODE_FULL,
        discovery_url_cap=None,
        sample_url_limit=0,
        monitored_url_limit=STARTER_MONITORED_URL_LIMIT,
        allows_user_selection=True,
        count_disclosure=True,
    ),
}


def normalize_capability(value: str | None) -> str:
    """Coerce an entitlement value to a known capability key (default Free)."""
    key = str(value or "").strip().lower()
    return key if key in SITE_HEALTH_CAPABILITIES else DEFAULT_SITE_HEALTH_CAPABILITY


def capability_profile(capability: str | None) -> SiteHealthCapability:
    """Resolve the frozen capability profile for a workspace entitlement.

    Unknown/missing values resolve to the Free profile (fail-closed to the most
    restrictive capability).
    """
    return _CAPABILITY_PROFILES[normalize_capability(capability)]


# Selection source: a monitored row is either user-managed or a system-managed
# Free sample membership.
SELECTION_SOURCE_USER: Final = "user"
SELECTION_SOURCE_FREE_SAMPLE: Final = "free_sample"
SELECTION_SOURCES: Final[frozenset[str]] = frozenset(
    {SELECTION_SOURCE_USER, SELECTION_SOURCE_FREE_SAMPLE}
)

# =========================================================================
# Lifecycle state vocabularies (normative — plan Persistence contract)
# =========================================================================
# Overall crawl:
#   draft -> validating -> queued -> running ->
#     completed | partially_completed | failed | cancelled
CRAWL_STATUS_DRAFT: Final = "draft"
CRAWL_STATUS_VALIDATING: Final = "validating"
CRAWL_STATUS_QUEUED: Final = "queued"
CRAWL_STATUS_RUNNING: Final = "running"
CRAWL_STATUS_COMPLETED: Final = "completed"
CRAWL_STATUS_PARTIALLY_COMPLETED: Final = "partially_completed"
CRAWL_STATUS_FAILED: Final = "failed"
CRAWL_STATUS_CANCELLED: Final = "cancelled"
CRAWL_STATUSES: Final[frozenset[str]] = frozenset(
    {
        CRAWL_STATUS_DRAFT,
        CRAWL_STATUS_VALIDATING,
        CRAWL_STATUS_QUEUED,
        CRAWL_STATUS_RUNNING,
        CRAWL_STATUS_COMPLETED,
        CRAWL_STATUS_PARTIALLY_COMPLETED,
        CRAWL_STATUS_FAILED,
        CRAWL_STATUS_CANCELLED,
    }
)
CRAWL_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {
        CRAWL_STATUS_COMPLETED,
        CRAWL_STATUS_PARTIALLY_COMPLETED,
        CRAWL_STATUS_FAILED,
        CRAWL_STATUS_CANCELLED,
    }
)
CRAWL_ACTIVE_STATUSES: Final[frozenset[str]] = frozenset(
    {
        CRAWL_STATUS_DRAFT,
        CRAWL_STATUS_VALIDATING,
        CRAWL_STATUS_QUEUED,
        CRAWL_STATUS_RUNNING,
    }
)

# Discovery sub-state:
#   pending -> running ->
#     completed | sample_completed | failed | cancelled
DISCOVERY_STATUS_PENDING: Final = "pending"
DISCOVERY_STATUS_RUNNING: Final = "running"
DISCOVERY_STATUS_COMPLETED: Final = "completed"
DISCOVERY_STATUS_SAMPLE_COMPLETED: Final = "sample_completed"
DISCOVERY_STATUS_FAILED: Final = "failed"
DISCOVERY_STATUS_CANCELLED: Final = "cancelled"
DISCOVERY_STATUSES: Final[frozenset[str]] = frozenset(
    {
        DISCOVERY_STATUS_PENDING,
        DISCOVERY_STATUS_RUNNING,
        DISCOVERY_STATUS_COMPLETED,
        DISCOVERY_STATUS_SAMPLE_COMPLETED,
        DISCOVERY_STATUS_FAILED,
        DISCOVERY_STATUS_CANCELLED,
    }
)

# Analysis sub-state:
#   pending -> running ->
#     completed | partially_completed | failed | cancelled
ANALYSIS_STATUS_PENDING: Final = "pending"
ANALYSIS_STATUS_RUNNING: Final = "running"
ANALYSIS_STATUS_COMPLETED: Final = "completed"
ANALYSIS_STATUS_PARTIALLY_COMPLETED: Final = "partially_completed"
ANALYSIS_STATUS_FAILED: Final = "failed"
ANALYSIS_STATUS_CANCELLED: Final = "cancelled"
ANALYSIS_STATUSES: Final[frozenset[str]] = frozenset(
    {
        ANALYSIS_STATUS_PENDING,
        ANALYSIS_STATUS_RUNNING,
        ANALYSIS_STATUS_COMPLETED,
        ANALYSIS_STATUS_PARTIALLY_COMPLETED,
        ANALYSIS_STATUS_FAILED,
        ANALYSIS_STATUS_CANCELLED,
    }
)

# Per-page analysis row status (SitePageAnalysis).
PAGE_ANALYSIS_STATUS_PENDING: Final = "pending"
PAGE_ANALYSIS_STATUS_RUNNING: Final = "running"
PAGE_ANALYSIS_STATUS_COMPLETED: Final = "completed"
PAGE_ANALYSIS_STATUS_PARTIALLY_COMPLETED: Final = "partially_completed"
PAGE_ANALYSIS_STATUS_FAILED: Final = "failed"

# =========================================================================
# Task kinds (SiteCrawlTask.task_kind)
# =========================================================================
TASK_KIND_DISCOVER: Final = "discover"
TASK_KIND_ANALYZE: Final = "analyze"
TASK_KIND_LINK_CHECK: Final = "link_check"
SITE_TASK_KINDS: Final[frozenset[str]] = frozenset(
    {TASK_KIND_DISCOVER, TASK_KIND_ANALYZE, TASK_KIND_LINK_CHECK}
)

# Initial (first-generation) task/artifact identity. Remove/re-add and explicit
# rerun allocate the NEXT generation under lock so they never collide.
INITIAL_TASK_GENERATION: Final = 0

# =========================================================================
# URL observation source kinds + link reference / fetch purpose kinds
# =========================================================================
OBSERVATION_SOURCE_ROOT: Final = "root"
OBSERVATION_SOURCE_LINK: Final = "link"
OBSERVATION_SOURCE_SITEMAP: Final = "sitemap"
OBSERVATION_SOURCE_REDIRECT: Final = "redirect"
OBSERVATION_SOURCES: Final[frozenset[str]] = frozenset(
    {
        OBSERVATION_SOURCE_ROOT,
        OBSERVATION_SOURCE_LINK,
        OBSERVATION_SOURCE_SITEMAP,
        OBSERVATION_SOURCE_REDIRECT,
    }
)

LINK_KIND_ANCHOR: Final = "anchor"
LINK_KIND_IMAGE: Final = "image"
LINK_KIND_SCRIPT: Final = "script"
LINK_KIND_STYLESHEET: Final = "stylesheet"
LINK_KINDS: Final[frozenset[str]] = frozenset(
    {LINK_KIND_ANCHOR, LINK_KIND_IMAGE, LINK_KIND_SCRIPT, LINK_KIND_STYLESHEET}
)

FETCH_PURPOSE_DISCOVER: Final = "discover"
FETCH_PURPOSE_ANALYZE: Final = "analyze"
FETCH_PURPOSE_LINK_CHECK: Final = "link_check"
FETCH_PURPOSE_ROBOTS: Final = "robots"
FETCH_PURPOSE_SITEMAP: Final = "sitemap"

# =========================================================================
# Rule dimensions / outcomes / severities / categories
# =========================================================================
DIMENSION_TECHNICAL: Final = "technical"
DIMENSION_AEO: Final = "aeo"
RULE_DIMENSIONS: Final[frozenset[str]] = frozenset(
    {DIMENSION_TECHNICAL, DIMENSION_AEO}
)

RULE_OUTCOME_PASS: Final = "pass"
RULE_OUTCOME_FAIL: Final = "fail"
RULE_OUTCOME_NOT_APPLICABLE: Final = "not_applicable"
RULE_OUTCOME_ERROR: Final = "error"
RULE_OUTCOMES: Final[frozenset[str]] = frozenset(
    {
        RULE_OUTCOME_PASS,
        RULE_OUTCOME_FAIL,
        RULE_OUTCOME_NOT_APPLICABLE,
        RULE_OUTCOME_ERROR,
    }
)

SEVERITY_CRITICAL: Final = "critical"
SEVERITY_HIGH: Final = "high"
SEVERITY_MEDIUM: Final = "medium"
SEVERITY_LOW: Final = "low"
SEVERITY_INFO: Final = "info"
RULE_SEVERITIES: Final[frozenset[str]] = frozenset(
    {
        SEVERITY_CRITICAL,
        SEVERITY_HIGH,
        SEVERITY_MEDIUM,
        SEVERITY_LOW,
        SEVERITY_INFO,
    }
)

CATEGORY_INDEXABILITY: Final = "indexability"
CATEGORY_METADATA: Final = "metadata"
CATEGORY_CONTENT: Final = "content"
CATEGORY_STRUCTURED_DATA: Final = "structured_data"
CATEGORY_PERFORMANCE: Final = "performance"
CATEGORY_LINKS: Final = "links"
CATEGORY_SECURITY: Final = "security"

# =========================================================================
# Safe per-task error tokens (never persist raw bodies/sensitive headers)
# =========================================================================
ERROR_ROBOTS_DENIED: Final = "robots_denied"
ERROR_DNS_RESOLUTION_FAILED: Final = "dns_resolution_failed"
ERROR_SSRF_BLOCKED: Final = "ssrf_blocked"
ERROR_REDIRECT_LIMIT: Final = "redirect_limit"
ERROR_RESPONSE_TOO_LARGE: Final = "response_too_large"
ERROR_UNSUPPORTED_CONTENT_TYPE: Final = "unsupported_content_type"
ERROR_TIMEOUT: Final = "timeout"
ERROR_HTTP_4XX: Final = "http_4xx"
ERROR_HTTP_5XX: Final = "http_5xx"
ERROR_CONNECTION_FAILED: Final = "connection_failed"
ERROR_MALFORMED_RESPONSE: Final = "malformed_response"
SITE_FETCH_ERROR_TOKENS: Final[frozenset[str]] = frozenset(
    {
        ERROR_ROBOTS_DENIED,
        ERROR_DNS_RESOLUTION_FAILED,
        ERROR_SSRF_BLOCKED,
        ERROR_REDIRECT_LIMIT,
        ERROR_RESPONSE_TOO_LARGE,
        ERROR_UNSUPPORTED_CONTENT_TYPE,
        ERROR_TIMEOUT,
        ERROR_HTTP_4XX,
        ERROR_HTTP_5XX,
        ERROR_CONNECTION_FAILED,
        ERROR_MALFORMED_RESPONSE,
    }
)

# Policy-denial/blocking codes: when the latest analyze task ended under one of
# these, the page's presentation status is `blocked` (rather than the generic
# `error`). All other terminal-unsuccessful analysis maps to `error`.
POLICY_BLOCKING_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        ERROR_ROBOTS_DENIED,
        ERROR_SSRF_BLOCKED,
    }
)

# =========================================================================
# Coded API failures (stable tokens returned to the client)
# =========================================================================
CODE_STARTER_REQUIRED: Final = "starter_required"
CODE_QUOTA_EXCEEDED: Final = "site_health_quota_exceeded"
CODE_STALE_SELECTION_VERSION: Final = "stale_selection_version"
CODE_CRAWL_ALREADY_ACTIVE: Final = "crawl_already_active"

# =========================================================================
# Crawl lifecycle event types (safe SSE payloads; Free excludes totals)
# =========================================================================
EVENT_CRAWL_CREATED: Final = "crawl.created"
EVENT_CRAWL_QUEUED: Final = "crawl.queued"
EVENT_CRAWL_RUNNING: Final = "crawl.running"
EVENT_DISCOVERY_PROGRESS: Final = "discovery.progress"
EVENT_ANALYSIS_PROGRESS: Final = "analysis.progress"
EVENT_CRAWL_STATUS: Final = "crawl.status"
EVENT_CRAWL_COMPLETED: Final = "crawl.completed"
EVENT_CRAWL_CANCELLED: Final = "crawl.cancelled"

# =========================================================================
# Versions (extractor / analyzer / rule catalog / scoring)
# =========================================================================
# Bumped whenever the deterministic extraction/rule/scoring logic changes so
# every derived row (facts, evaluations, issues, scores) is traceable to the
# exact rules that produced it (invariant 4).
EXTRACTOR_VERSION: Final = "sh-extractor-1"
ANALYZER_VERSION: Final = "sh-analyzer-1"
RULE_CATALOG_VERSION: Final = "sh-rules-1"
SCORING_VERSION: Final = "sh-scoring-1"

# =========================================================================
# Deterministic scoring weights (config-owned)
# =========================================================================
# Overall score = config-owned weighted mean of the available Technical + AEO
# dimension scores (50/50 by product contract).
DIMENSION_WEIGHT_TECHNICAL: Final = 0.5
DIMENSION_WEIGHT_AEO: Final = 0.5
# Round every dimension/overall score once to this many decimals.
SCORE_ROUNDING_DECIMALS: Final = 1


class SiteHealthRule:
    """One deterministic Site Health rule (frozen catalog entry).

    Every rule carries a stable ``rule_id`` + ``rule_version`` + dimension +
    category + severity + weight + applicability-predicate key + description +
    remediation. The evaluator applies these; it never invents rule metadata
    inline (invariant 1).
    """

    __slots__ = (
        "rule_id",
        "rule_version",
        "dimension",
        "category",
        "severity",
        "weight",
        "applicability_key",
        "description",
        "remediation",
        "display_label",
    )

    def __init__(
        self,
        *,
        rule_id: str,
        rule_version: str,
        dimension: str,
        category: str,
        severity: str,
        weight: float,
        applicability_key: str,
        description: str,
        remediation: str,
        display_label: str = "",
    ) -> None:
        self.rule_id = rule_id
        self.rule_version = rule_version
        self.dimension = dimension
        self.category = category
        self.severity = severity
        self.weight = weight
        self.applicability_key = applicability_key
        self.description = description
        self.remediation = remediation
        # Current human-facing catalog title (mockup 710/711). The persisted
        # issue/evaluation rows never store this; the API reads it live so a
        # relabel takes effect immediately. Empty falls back to ``rule_id``.
        self.display_label = display_label or rule_id


# The rule catalog. Task 5 evaluates these; defined here so the catalog has one
# owner and a stable version. This is an initial representative set covering
# both dimensions; later tasks extend it (bumping ``RULE_CATALOG_VERSION``).
SITE_HEALTH_RULES: Final[tuple[SiteHealthRule, ...]] = (
    SiteHealthRule(
        rule_id="technical.title_present",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_METADATA,
        severity=SEVERITY_HIGH,
        weight=3.0,
        applicability_key="always",
        description="Page has a non-empty <title>.",
        remediation="Add a concise, descriptive <title> element to the page.",
        display_label="Missing page title",
    ),
    SiteHealthRule(
        rule_id="technical.meta_description_present",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_METADATA,
        severity=SEVERITY_MEDIUM,
        weight=2.0,
        applicability_key="always",
        description="Page has a non-empty meta description.",
        remediation="Add a meta description summarizing the page content.",
        display_label="Missing meta description",
    ),
    SiteHealthRule(
        rule_id="technical.canonical_present",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_INDEXABILITY,
        severity=SEVERITY_MEDIUM,
        weight=2.0,
        applicability_key="always",
        description="Page declares a canonical URL.",
        remediation="Add a <link rel=\"canonical\"> pointing at the preferred URL.",
        display_label="Missing canonical URL",
    ),
    SiteHealthRule(
        rule_id="technical.indexable",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_INDEXABILITY,
        severity=SEVERITY_CRITICAL,
        weight=4.0,
        applicability_key="always",
        description="Page is not blocked from indexing by a robots meta noindex.",
        remediation="Remove the noindex directive if the page should be indexed.",
        display_label="Page blocked from indexing",
    ),
    SiteHealthRule(
        rule_id="technical.https",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_SECURITY,
        severity=SEVERITY_HIGH,
        weight=3.0,
        applicability_key="always",
        description="Final URL is served over HTTPS.",
        remediation="Serve the page over HTTPS and redirect HTTP to HTTPS.",
        display_label="Not served over HTTPS",
    ),
    SiteHealthRule(
        rule_id="technical.single_h1",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_CONTENT,
        severity=SEVERITY_LOW,
        weight=1.0,
        applicability_key="has_html",
        description="Page has exactly one <h1> heading.",
        remediation="Use a single <h1> that describes the page's primary topic.",
        display_label="Multiple or missing H1",
    ),
    SiteHealthRule(
        rule_id="aeo.structured_data_present",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_STRUCTURED_DATA,
        severity=SEVERITY_MEDIUM,
        weight=3.0,
        applicability_key="has_html",
        description="Page includes JSON-LD or microdata structured data.",
        remediation="Add schema.org structured data (JSON-LD preferred).",
        display_label="Missing structured data",
    ),
    SiteHealthRule(
        rule_id="aeo.open_graph_present",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_METADATA,
        severity=SEVERITY_LOW,
        weight=1.0,
        applicability_key="has_html",
        description="Page declares Open Graph title/description metadata.",
        remediation="Add og:title and og:description meta tags.",
        display_label="Missing Open Graph metadata",
    ),
    SiteHealthRule(
        rule_id="aeo.sufficient_text",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_CONTENT,
        severity=SEVERITY_MEDIUM,
        weight=2.0,
        applicability_key="has_html",
        description="Page has enough extractable body text to answer queries.",
        remediation="Add substantive, answer-oriented body content to the page.",
        display_label="Insufficient page text",
    ),
)

# Fast lookup by rule id.
SITE_HEALTH_RULES_BY_ID: Final[dict[str, SiteHealthRule]] = {
    rule.rule_id: rule for rule in SITE_HEALTH_RULES
}

# =========================================================================
# Structured-data required-property maps (bundled, deterministic)
# =========================================================================
# The schema.org types the AEO analysis recognizes and the properties each
# should carry to be considered complete. Bounded + config-owned so extraction
# is deterministic (invariant 9).
STRUCTURED_DATA_REQUIRED_PROPERTIES: Final[dict[str, tuple[str, ...]]] = {
    "Organization": ("name", "url"),
    "WebSite": ("name", "url"),
    "WebPage": ("name",),
    "Article": ("headline", "author", "datePublished"),
    "Product": ("name", "offers"),
    "FAQPage": ("mainEntity",),
    "BreadcrumbList": ("itemListElement",),
}

# =========================================================================
# Query-normalization: tracking parameters stripped during canonicalization
# =========================================================================
TRACKING_QUERY_PARAMS: Final[frozenset[str]] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "_hsenc",
        "_hsmi",
        "ref",
        "ref_src",
    }
)

# Response header allowlist: only these are persisted (redacted set); everything
# else (cookies, auth, etc.) is dropped so no sensitive header is ever stored.
PERSISTED_RESPONSE_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "content-type",
        "content-length",
        "content-encoding",
        "cache-control",
        "etag",
        "last-modified",
        "expires",
        "vary",
        "server",
        "x-content-type-options",
        "strict-transport-security",
        "content-security-policy",
        "x-frame-options",
        "referrer-policy",
    }
)

# Content types the crawler will fetch + parse as HTML.
HTML_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {"text/html", "application/xhtml+xml"}
)
# Allowed non-HTML sitemap content types.
SITEMAP_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/xml",
        "text/xml",
        "application/gzip",
        "application/x-gzip",
    }
)
# Only these URL schemes and ports are ever fetched.
ALLOWED_URL_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
ALLOWED_URL_PORTS: Final[frozenset[int]] = frozenset({80, 443})


class SiteHealthSettings(BaseSettings):
    """Env-overridable Site Health crawler/queue guardrails.

    Every operational bound the secure fetcher, frontier, robots/sitemap
    parser, worker, and queue read. Frozen into ``SiteCrawl.configuration`` at
    creation so a live change never alters an in-flight run (invariant 9). All
    knobs use the ``SITE_HEALTH_`` env prefix (no service literals — invariant
    1).
    """

    model_config = SettingsConfigDict(
        env_prefix="SITE_HEALTH_", extra="ignore"
    )

    # --- Frontier / discovery bounds ---
    # Absolute frontier ceiling for a FULL (Starter) crawl to bound memory/time.
    max_frontier_urls: int = 50000
    # Max discovery depth from the root.
    max_crawl_depth: int = 20
    # Batch size for progressive inventory admission (INSERT ... ON CONFLICT).
    admission_batch_size: int = 200

    # --- Concurrency / politeness ---
    # Global in-process concurrent fetch ceiling for the Site Health worker.
    global_concurrency: int = 8
    # Per-host concurrent fetch ceiling.
    per_host_concurrency: int = 2
    # Minimum delay between requests to the same host (politeness); robots
    # crawl-delay overrides upward.
    per_host_delay_seconds: float = 0.5
    # Default crawl delay applied when robots does not specify one.
    default_crawl_delay_seconds: float = 0.0
    # Cap on any robots-declared crawl delay we will honor.
    max_crawl_delay_seconds: float = 30.0

    # --- Fetch limits ---
    # Per-request wall-clock timeout.
    request_timeout_seconds: float = 20.0
    # Max redirect hops manually followed (each re-validated for SSRF/scope).
    max_redirects: int = 5
    # Wire-byte (raw network) cap per response.
    max_response_wire_bytes: int = 5_000_000
    # Decoded-byte cap per response (guards decompression bombs).
    max_response_decoded_bytes: int = 20_000_000
    # HTML size cap fed to the parser.
    max_html_bytes: int = 5_000_000

    # --- Sitemap limits ---
    max_sitemap_index_depth: int = 3
    max_sitemap_urls: int = 50000
    max_sitemap_decoded_bytes: int = 50_000_000

    # --- Parser bounds (bounded, deterministic extraction) ---
    max_links_per_page: int = 2000
    max_structured_data_blocks: int = 100
    max_text_chars: int = 200_000

    # --- Queue / lease / retry ---
    lease_ttl_seconds: float = 120.0
    heartbeat_interval_seconds: float = 30.0
    max_attempts: int = 4
    retry_base_delay_seconds: float = 2.0
    retry_max_delay_seconds: float = 60.0
    retry_jitter_seconds: float = 1.5
    worker_concurrency: int = 8
    poll_interval_seconds: float = 1.0
    # Deterministic bound on how many expired leases the sweeper reclaims in
    # ONE transaction. A mass expiry across a large frontier (e.g. 50,000
    # URLs) would otherwise lock and update every expired row in a single
    # long-running transaction and stall live claims; the sweeper instead
    # drains the remainder across subsequent polls.
    lease_reclaim_batch_size: int = 500

    # --- Link checking ---
    max_link_checks_per_page: int = 200
    link_check_timeout_seconds: float = 10.0

    # --- Export ---
    # Bounds how many rows ``_export_items`` materializes into memory for a
    # single CSV/Markdown export before it truncates, so a very large Starter
    # inventory can never exhaust memory on one request.
    max_export_items: int = 20_000

    # --- SSE / events ---
    sse_poll_interval_seconds: float = 2.0
    sse_max_duration_seconds: float = 300.0

    @model_validator(mode="after")
    def _validate_lease_and_heartbeat(self) -> SiteHealthSettings:
        """Enforce positive lease/heartbeat values and heartbeat < lease TTL.

        A heartbeat interval that is not strictly less than the lease TTL
        would let the sweeper reclaim a still-live task before it ever gets a
        chance to send its first heartbeat.
        """
        if self.lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        if self.heartbeat_interval_seconds >= self.lease_ttl_seconds:
            raise ValueError(
                "heartbeat_interval_seconds must be strictly less than "
                "lease_ttl_seconds"
            )
        if self.lease_reclaim_batch_size <= 0:
            raise ValueError("lease_reclaim_batch_size must be positive")
        return self

    def retry_delay(
        self, attempt: int, retry_after_seconds: float | None = None
    ) -> float:
        """Seconds to wait before the next attempt.

        Prefers a server-advised ``Retry-After`` (clamped); else exponential
        backoff capped at the max, plus deterministic jitter (derived from the
        attempt number, not RNG, so it stays reproducible — invariant 9).
        """
        cap = self.retry_max_delay_seconds
        if retry_after_seconds is not None:
            return min(retry_after_seconds, cap)
        base = self.retry_base_delay_seconds * (2**attempt)
        jitter = (attempt * 0.37) % 1.0 * self.retry_jitter_seconds
        return min(base, cap) + jitter


site_health_settings = SiteHealthSettings()


def _site_crawl_task_model() -> type:
    # Lazy import: this config module must never import a model at import time
    # (would create a config <-> models circular import).
    from app.models.site_health import SiteCrawlTask

    return SiteCrawlTask


def _site_task_claim_order(model: type) -> tuple:
    # Deterministic claim order: priority, then FIFO by availability, then the
    # frozen randomized frontier position, then a stable id tiebreak.
    return (
        model.priority.desc(),
        model.available_at.asc(),
        model.randomized_position.asc(),
        model.id.asc(),
    )


# The Site Health queue spec: parameterizes the generic ``PostgresTaskQueue``
# over ``SiteCrawlTask`` with the Site Health lease TTL + claim order. Reuses
# the identical FOR UPDATE SKIP LOCKED implementation as the audit queue.
SITE_CRAWL_QUEUE_SPEC: Final[PostgresQueueSpec] = PostgresQueueSpec(
    model_ref=_site_crawl_task_model,
    lease_ttl=lambda: site_health_settings.lease_ttl_seconds,
    claim_order=_site_task_claim_order,
    max_attempts_error=ERROR_MAX_ATTEMPTS,
)
