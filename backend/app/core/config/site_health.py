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

from pathlib import Path
from typing import TYPE_CHECKING, Final

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.task_queue import (
    ERROR_MAX_ATTEMPTS,
    PostgresQueueSpec,
)

# Pure extractor/classifier bounds.  Keep these alongside the other Site
# Health knobs so parser code has no embedded operational limits.
SITE_HEALTH_MAX_TITLE_CHARS: Final = 2048
SITE_HEALTH_MAX_META_CHARS: Final = 4096
SITE_HEALTH_MAX_HEADING_CHARS: Final = 512
SITE_HEALTH_MAX_HEADINGS_KEPT: Final = 50
SITE_HEALTH_MAX_URL_CHARS: Final = 2048
SITE_HEALTH_MAX_ANCHOR_TEXT_CHARS: Final = 512
SITE_HEALTH_MAX_AUTHOR_CHARS: Final = 256
SITE_HEALTH_MAX_DATE_CHARS: Final = 64
SITE_HEALTH_MAX_OUTBOUND_DOMAINS: Final = 100
SITE_HEALTH_MAX_DOMAIN_CHARS: Final = 255
SITE_HEALTH_MAX_HREFLANG_ALTERNATES: Final = 50
SITE_HEALTH_MAX_HREFLANG_CHARS: Final = 35
# Industry-role classifier facts (reference.py ``_COLLECTION_LIMITS``). The
# pack classifier scores conversion and journey roles from call-to-action
# wording, form-field labels, and internal-link context; without these an
# admissions-enquiry page and a prospectus page look identical on path and
# headings alone. Bounds match the classifier's own caps so the extractor
# never produces more than it can consume.
SITE_HEALTH_MAX_CTA_TEXTS: Final = 32
SITE_HEALTH_MAX_CTA_TEXT_CHARS: Final = 256
SITE_HEALTH_MAX_FORM_FIELDS: Final = 32
SITE_HEALTH_MAX_FORM_FIELD_CHARS: Final = 128
SITE_HEALTH_MAX_LINK_CONTEXT: Final = 64
SITE_HEALTH_MAX_LINK_CONTEXT_CHARS: Final = 512
# Elements whose visible text is a call to action. Buttons and submit inputs
# are unambiguous; an anchor only counts when it carries a button-ish role or
# class, so ordinary navigation links do not drown the real CTAs.
CTA_BUTTON_ROLE_TOKENS: Final[frozenset[str]] = frozenset(
    {"button", "btn", "cta", "apply", "enquire", "enquiry", "submit"}
)
SITE_HEALTH_MAX_FIRST_ANSWER_CHARS: Final = 512
SITE_HEALTH_MAX_INLINE_SCRIPT_CHARS: Final = 500_000
# Visible knowledge evidence (sh-extractor-4). Contact points come from
# ``mailto:``/``tel:`` hrefs rather than a text scan: an href is an explicit
# authored declaration, where a regex over body text also matches an email in a
# testimonial, a sample form value, or another organization's address.
SITE_HEALTH_MAX_CONTACT_POINTS: Final = 16
SITE_HEALTH_MAX_CONTACT_VALUE_CHARS: Final = 256
# Money mentions carry their currency or they are not recorded: a bare number
# is not a price, and a report that renders one beside the wrong symbol is worse
# than one that reports the fact as missing.
SITE_HEALTH_MAX_MONEY_MENTIONS: Final = 24
SITE_HEALTH_MAX_MONEY_CONTEXT_CHARS: Final = 160
# Money evidence is sized independently of the contact-point bound: the two are
# unrelated fields, and sharing a constant would silently retune one when the
# other is adjusted.
SITE_HEALTH_MAX_MONEY_RAW_CHARS: Final = 64
# ISO codes and symbols recognized in visible copy. Deliberately a short,
# explicit list: an unrecognized currency yields no assertion rather than a
# guessed one.
MONEY_CURRENCY_SYMBOLS: Final[dict[str, str]] = {
    "₹": "INR",
    "$": "USD",
    "£": "GBP",
    "€": "EUR",
    "¥": "JPY",
    "₦": "NGN",
    "₨": "PKR",
    "AED": "AED",
    "INR": "INR",
    "USD": "USD",
    "GBP": "GBP",
    "EUR": "EUR",
    # "Rs"/"Rs." are deliberately ABSENT. The abbreviation is used for the
    # Indian, Pakistani, Sri Lankan, and Nepalese rupee alike, so resolving it
    # to INR publishes a guess as an observed fact — exactly what the note above
    # forbids. "₨" stays because it is unambiguous.
}
SITE_HEALTH_MAX_PATH_CHARS: Final = 512
SITE_HEALTH_MAX_SIGNAL_DETAIL_CHARS: Final = 256
SITE_HEALTH_MAX_EVIDENCE_URLS: Final = 10

# JSON-LD enrichment bounds.
SITE_HEALTH_MAX_JSONLD_DEPTH: Final = 12
SITE_HEALTH_MAX_NAME_CHARS: Final = 256
SITE_HEALTH_MAX_SAME_AS_ENTRIES: Final = 8
SITE_HEALTH_MAX_SAME_AS_CHARS: Final = 256

# backend/app/core/config/site_health.py -> parents[3] == backend/
_BASE_DIR = Path(__file__).resolve().parents[3]
# Repo root (CiteLadder/) holds the shared .env used by docker + local dev.
_PROJECT_ROOT = _BASE_DIR.parent

if TYPE_CHECKING:
    # Type-only: config never imports a model at runtime (circular import).
    from app.models.site_health import SiteCrawlTask

# =========================================================================
# Neutral Site Health runtime policy (no commercial capability vocabulary)
# =========================================================================
# A workspace's Site Health behavior is a RUNTIME PROJECTION of the resolved
# ``monitored_urls`` entitlement allowance (see domain/entitlements). This
# module owns only the neutral mapping knobs (invariant 1); it never stores or
# matches a plan display name or a commercial capability key.
#
# Mapping (frozen plan):
#   - zero / no allowance -> SAMPLE discovery capped at the neutral sample
#     limit, zero selectable monitored URLs, no count disclosure;
#   - positive allowance  -> FULL progressive discovery, that exact monitored
#     URL limit, count disclosure enabled.
DISCOVERY_MODE_SAMPLE: Final = "sample"
DISCOVERY_MODE_FULL: Final = "full"

# Internal frozen-configuration key for full-inventory continuity. A fresh
# analysis/recrawl remains a distinct evidence run, but its dashboard may read
# the admitted URL sets from these earlier full-discovery crawls so discovered
# URLs do not disappear while the new crawl is still re-discovering them.
INVENTORY_SOURCE_CRAWL_IDS_KEY: Final = "inventory_source_crawl_ids"

# Marks a product-owned onboarding crawl. The discovery pipeline uses the
# frozen value to auto-monitor and analyze the first bounded set of pages.
AUTOMATIC_MONITOR_LIMIT_KEY: Final = "automatic_monitor_limit"

# Neutral sample cap DEFAULT. The operative value is env-overridable via
# ``SiteHealthSettings.sample_url_limit`` (``SITE_HEALTH_SAMPLE_URL_LIMIT``) so
# development can lift it without a code change; ``runtime_policy_for_allowance``
# always reads the live settings. The constant remains as the settings default
# and as the static column default on the runtime model.
SAMPLE_URL_LIMIT: Final = 10

# Sample-mode INVENTORY cap — deliberately decoupled from the analysis budget
# above.
#
# These used to be the same number, which made "how many URLs do we know about"
# and "how many URLs do we deep-analyze" one decision: discovery stopped dead at
# 10 because admitting a URL and monitoring it for analysis were the same act.
# The inventory is cheap (an identity row + an observation row, no fetch), the
# analysis is not, so the crawl now keeps mapping the site up to this soft cap
# while only ``sample_url_limit`` URLs are ever analyzed. "Soft" is accurate:
# admission happens in batches, so a batch that straddles the cap lands slightly
# over it rather than being split mid-batch.
SAMPLE_DISCOVERY_URL_CAP: Final = 200

# =========================================================================
# Value-aware URL admission (frozen per crawl)
# =========================================================================
# These are deliberately URL-only rules: they run before a queue row or a
# transport request exists.  Reason codes are safe to expose in previews and
# events; no rule includes a URL, credential, or provider detail.
URL_ADMISSION_POLICY_VERSION: Final = "sh-url-admission-1"
INPUT_MODE_AUTO: Final = "auto"
INPUT_MODE_EXACT_URLS: Final = "exact_urls"
INPUT_MODE_DISCOVERY_SEEDS: Final = "discovery_seeds"
INPUT_MODES: Final[frozenset[str]] = frozenset(
    {INPUT_MODE_AUTO, INPUT_MODE_EXACT_URLS, INPUT_MODE_DISCOVERY_SEEDS}
)
URL_EXCLUSION_HARD_PATH: Final = "hard_excluded_path"
URL_EXCLUSION_HARD_ASSET: Final = "hard_excluded_asset"
URL_EXCLUSION_HARD_QUERY: Final = "hard_excluded_query"
URL_EXCLUSION_OUT_OF_SCOPE: Final = "out_of_scope"
URL_EXCLUSION_NARROWED: Final = "narrowed"
URL_EXCLUSION_INVALID: Final = "invalid_url"
URL_EXCLUSION_DUPLICATE: Final = "duplicate"
URL_EXCLUSION_PAGE_KIND: Final = "page_kind_filtered"
URL_EXCLUSION_TRACKING: Final = "tracking_url"

# --- Corpus disposition (Site Intelligence §4) ---------------------------
# Every discovered URL gets a versioned disposition. These are DISTINCT
# states, not a confidence gradient: ``inventory_only`` means "known and
# counted, deliberately not deep-analyzed", which is what keeps a document or
# a utility page visible in coverage without paying analysis cost for it.
# ``exclude`` means confidently irrelevant/unsafe. An UNCERTAIN URL is never
# silently discarded — it stays ``inventory_only``.
CORPUS_DISPOSITION_ANALYZE: Final = "analyze"
CORPUS_DISPOSITION_INVENTORY_ONLY: Final = "inventory_only"
CORPUS_DISPOSITION_EXCLUDE: Final = "exclude"
CORPUS_DISPOSITIONS: Final[frozenset[str]] = frozenset(
    {
        CORPUS_DISPOSITION_ANALYZE,
        CORPUS_DISPOSITION_INVENTORY_ONLY,
        CORPUS_DISPOSITION_EXCLUDE,
    }
)
DISPOSITION_REASON_HTML_CONTENT: Final = "html_content"
DISPOSITION_REASON_DOCUMENT: Final = "document"
DISPOSITION_REASON_UNSUPPORTED_MEDIA: Final = "unsupported_media"
CORPUS_DISPOSITION_VERSION: Final = "sh-disposition-1"

# Crawl-configuration key holding the exact frozen industry-pack manifest
# (catalog version, pack id/version, content hash, classifier version). Frozen
# once at crawl creation and never re-resolved from live project settings.
INDUSTRY_PACK_MANIFEST_KEY: Final = "industry_pack_manifest"

# Corpus item kinds (kernel spec ``CorpusItem.item_kind``).
ITEM_KIND_HTML_PAGE: Final = "html_page"
ITEM_KIND_DOCUMENT: Final = "document"
ITEM_KIND_OTHER: Final = "other"

# Temporal state of an item's evidence. ``unknown`` is a real state, never a
# stand-in for ``current``: historical evidence must not silently overwrite a
# current assertion just because it carries a value.
TEMPORAL_STATE_CURRENT: Final = "current"
TEMPORAL_STATE_HISTORICAL: Final = "historical"
TEMPORAL_STATE_FUTURE: Final = "future"
TEMPORAL_STATE_UNKNOWN: Final = "unknown"
TEMPORAL_STATES: Final[frozenset[str]] = frozenset(
    {
        TEMPORAL_STATE_CURRENT,
        TEMPORAL_STATE_HISTORICAL,
        TEMPORAL_STATE_FUTURE,
        TEMPORAL_STATE_UNKNOWN,
    }
)
URL_HARD_EXCLUSION_PATH_PATTERNS: Final[tuple[str, ...]] = (
    r"(?:^|/)(?:login|log-in|signin|sign-in|register|signup|sign-up)(?:/|$)",
    r"(?:^|/)(?:account|profile|admin|wp-admin|dashboard)(?:/|$)",
    r"(?:^|/)(?:cart|basket|checkout|payment|payments|order|orders|wishlist)(?:/|$)",
    r"(?:^|/)(?:search|tag|tags|author|authors|feed)(?:/|$)",
    r"(?:^|/)(?:preview|print|share)(?:/|$)",
)
URL_HARD_EXCLUSION_QUERY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "q",
        "query",
        "s",
        "search",
        "filter",
        "filters",
        "facet",
        "sort",
        "page",
        "paged",
        "preview",
    }
)
# Documents that carry real business knowledge (prospectuses, fee schedules,
# policies, disclosures). These are NOT hard exclusions: they are admitted to
# the corpus INVENTORY as ``item_kind=document`` so coverage and history stay
# truthful, even though the HTML analyzer never runs on them. Extraction is a
# separate, bounded decision — see ``DOCUMENT_MEDIA_TYPES``.
INVENTORY_DOCUMENT_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
    }
)
DOCUMENT_MEDIA_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)
# Genuinely unsafe or contentless assets. A document extension deliberately
# does NOT appear here: excluding a prospectus from the inventory would drop
# the very evidence an education/commerce pack needs to answer fee, policy,
# and curriculum questions.
URL_HARD_EXCLUSION_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".zip",
        ".gz",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".svg",
        ".ico",
        ".css",
        ".js",
        ".mjs",
        ".xml",
        ".json",
        ".csv",
        ".txt",
        ".mp3",
        ".mp4",
        ".webm",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        # Remaining archives and installers. Without these an ``.exe`` or
        # ``.tar.gz`` link was admitted as an ordinary page, and the crawler
        # spent an analysis slot fetching a binary the HTML analyzer can never
        # read — a scheduled, guaranteed failure.
        ".tar",
        ".bz2",
        ".7z",
        ".rar",
        ".exe",
        ".msi",
        ".dmg",
        ".pkg",
        ".apk",
        ".deb",
        ".rpm",
    }
)
# Higher values are more valuable.  Used only for deterministic frontier
# ordering/preview grouping; it never changes an already-frozen crawl.
URL_VALUE_PRIORITIES: Final[dict[str, int]] = {
    "root": 100,
    "product": 90,
    "comparison": 85,
    "service": 80,
    "local": 80,
    "category": 70,
    "pricing": 70,
    "article": 60,
    "guide": 60,
    "faq": 60,
    "docs": 60,
    "trust": 40,
    "other": 20,
}

# Development-only resumable phase controls.  The values are operational
# ceilings, not product-tier entitlements, and are frozen into each crawl.
PHASE_DISCOVERY: Final = "discovery"
PHASE_ANALYSIS: Final = "analysis"
PHASES: Final[frozenset[str]] = frozenset({PHASE_DISCOVERY, PHASE_ANALYSIS})
PHASE_RUN_RUNNING: Final = "running"
PHASE_RUN_STOPPED: Final = "stopped"
PHASE_RUN_COMPLETED: Final = "completed"
PHASE_RUN_FAILED: Final = "failed"
PHASE_RUN_STATUSES: Final[frozenset[str]] = frozenset(
    {PHASE_RUN_RUNNING, PHASE_RUN_STOPPED, PHASE_RUN_COMPLETED, PHASE_RUN_FAILED}
)
FRONTIER_PENDING: Final = "pending"
FRONTIER_ADMITTED: Final = "admitted"


class SiteHealthRuntimePolicy:
    """The neutral crawl policy projected from a resolved allowance.

    Immutable, value-typed record of exactly how a workspace crawls. Built by
    ``runtime_policy_for_allowance`` so there is one owner for the
    allowance-to-policy mapping. ``count_disclosure`` gates whether
    total/frontier/overflow counts may ever leave the backend (zero allowance
    = never; positive = yes).
    """

    __slots__ = (
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
        discovery_mode: str,
        discovery_url_cap: int | None,
        sample_url_limit: int,
        monitored_url_limit: int,
        allows_user_selection: bool,
        count_disclosure: bool,
    ) -> None:
        self.discovery_mode = discovery_mode
        # None means "no hard discovery cap" (full inventory). Sample mode caps
        # at the sample limit.
        self.discovery_url_cap = discovery_url_cap
        self.sample_url_limit = sample_url_limit
        self.monitored_url_limit = monitored_url_limit
        self.allows_user_selection = allows_user_selection
        self.count_disclosure = count_disclosure


def runtime_policy_for_allowance(
    monitored_urls_allowance: int,
) -> SiteHealthRuntimePolicy:
    """Map a resolved ``monitored_urls`` allowance to the crawl policy.

    Fail-closed: a zero/negative allowance yields the sample policy with zero
    selectable monitored URLs and no count disclosure. Limits reflect the LIVE
    ``SITE_HEALTH_*`` settings. The resolved policy is frozen onto the runtime
    row (updated in place — it is a projection, never a commercial source of
    truth) and onto ``SiteCrawl.configuration`` at creation (invariant 9).

    Sample mode's inventory cap is deliberately DECOUPLED from its analysis
    budget: discovery keeps mapping the site up to
    ``sample_discovery_url_cap`` while only ``sample_url_limit`` URLs ever get a
    monitored membership and an analyze task (see SAMPLE_DISCOVERY_URL_CAP).
    """
    settings = site_health_settings
    if monitored_urls_allowance > 0:
        return SiteHealthRuntimePolicy(
            discovery_mode=DISCOVERY_MODE_FULL,
            discovery_url_cap=None,
            sample_url_limit=0,
            monitored_url_limit=monitored_urls_allowance,
            allows_user_selection=True,
            count_disclosure=True,
        )
    return SiteHealthRuntimePolicy(
        discovery_mode=DISCOVERY_MODE_SAMPLE,
        # Inventory cap, NOT the analysis budget — see SAMPLE_DISCOVERY_URL_CAP.
        discovery_url_cap=settings.sample_discovery_url_cap,
        sample_url_limit=settings.sample_url_limit,
        monitored_url_limit=0,
        allows_user_selection=False,
        count_disclosure=False,
    )


# Selection source: a monitored row is either user-managed or a system-managed
# Free sample membership.
SELECTION_SOURCE_USER: Final = "user"
SELECTION_SOURCE_FREE_SAMPLE: Final = "free_sample"
SELECTION_SOURCE_BOOTSTRAP: Final = "bootstrap"
SELECTION_SOURCES: Final[frozenset[str]] = frozenset(
    {SELECTION_SOURCE_USER, SELECTION_SOURCE_FREE_SAMPLE, SELECTION_SOURCE_BOOTSTRAP}
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
CRAWL_STATUS_PAUSED: Final = "paused"
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
        CRAWL_STATUS_PAUSED,
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
        CRAWL_STATUS_PAUSED,
    }
)

# Discovery sub-state:
#   pending -> running ->
#     completed | sample_completed | failed | cancelled
DISCOVERY_STATUS_PENDING: Final = "pending"
DISCOVERY_STATUS_RUNNING: Final = "running"
DISCOVERY_STATUS_STOPPED: Final = "stopped"
DISCOVERY_STATUS_COMPLETED: Final = "completed"
DISCOVERY_STATUS_SAMPLE_COMPLETED: Final = "sample_completed"
DISCOVERY_STATUS_FAILED: Final = "failed"
DISCOVERY_STATUS_CANCELLED: Final = "cancelled"
DISCOVERY_STATUSES: Final[frozenset[str]] = frozenset(
    {
        DISCOVERY_STATUS_PENDING,
        DISCOVERY_STATUS_RUNNING,
        DISCOVERY_STATUS_STOPPED,
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
ANALYSIS_STATUS_STOPPED: Final = "stopped"
ANALYSIS_STATUS_COMPLETED: Final = "completed"
ANALYSIS_STATUS_PARTIALLY_COMPLETED: Final = "partially_completed"
ANALYSIS_STATUS_FAILED: Final = "failed"
ANALYSIS_STATUS_CANCELLED: Final = "cancelled"
ANALYSIS_STATUSES: Final[frozenset[str]] = frozenset(
    {
        ANALYSIS_STATUS_PENDING,
        ANALYSIS_STATUS_RUNNING,
        ANALYSIS_STATUS_STOPPED,
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
# v2 P2: the llms.txt probe at the site root (site_root rules / site_facts).
FETCH_PURPOSE_LLMS: Final = "llms"

# These narrowly identify crawler infrastructure documents that must be read
# even though their extensions are never admissible as customer-facing pages.
# URL policy applies this exception only when the matching internal fetch
# purpose is supplied; it does not loosen user seeds or page admission.
INFRASTRUCTURE_FETCH_EXACT_PATHS: Final[dict[str, frozenset[str]]] = {
    FETCH_PURPOSE_ROBOTS: frozenset({"/robots.txt"}),
    FETCH_PURPOSE_LLMS: frozenset({"/llms.txt"}),
    FETCH_PURPOSE_SITEMAP: frozenset(),
}
INFRASTRUCTURE_FETCH_PATH_SUFFIXES: Final[dict[str, tuple[str, ...]]] = {
    FETCH_PURPOSE_ROBOTS: (),
    FETCH_PURPOSE_LLMS: (),
    FETCH_PURPOSE_SITEMAP: (".xml", ".xml.gz"),
}

# =========================================================================
# Rule dimensions / outcomes / severities / categories
# =========================================================================
DIMENSION_TECHNICAL: Final = "technical"
DIMENSION_AEO: Final = "aeo"
RULE_DIMENSIONS: Final[frozenset[str]] = frozenset({DIMENSION_TECHNICAL, DIMENSION_AEO})

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
# v2 P2 (spec §5.3): the one new rule category — citability signals (author /
# dates / outbound citations / organization identity).
CATEGORY_CITABILITY: Final = "citability"

# =========================================================================
# Rule applicability scope tokens (v2 P2 — spec §5.2/§5.3)
# =========================================================================
# ``always`` / ``has_html`` / ``page_kind:<type>`` stay per-page. ``site_root``
# rules evaluate exactly once per crawl inside the ROOT URL's own analysis
# (applicable only when the worker injected ``facts["site"]`` from the crawl's
# ``site_facts``). ``crawl_finalize`` rules are never applicable in the
# per-page pass; the finalize-writer in ``_reconcile_crawl_status`` owns their
# evaluation rows (single-writer per rule scope).
APPLICABILITY_SITE_ROOT: Final = "site_root"
APPLICABILITY_CRAWL_FINALIZE: Final = "crawl_finalize"
# ``observed_content``: like ``has_html``, but ALSO requires that the server
# actually delivered the page's content. The crawler is HTTP-only, so a
# client-rendered shell arrives as markup with an empty body — and every rule
# that reads body text, headings or in-content links then "fails" on content
# that was never observed. One JS-shell page was reporting missing H1, thin
# content, no question headings, no outbound citations, no author and no date
# as six independent findings, each taking its own bite out of the score, when
# the single true finding is the one ``aeo.server_rendered_content`` already
# reports at HIGH.
#
# Rules about the SERVED MARKUP (title/meta/canonical/OG/JSON-LD presence,
# transport headers) deliberately keep ``has_html``: their subject is exactly
# what a non-rendering crawler receives, which is the product's whole thesis.
# Only rules whose subject is CONTENT WE COULD NOT SEE move here.
APPLICABILITY_OBSERVED_CONTENT: Final = "observed_content"

# =========================================================================
# Site setup fetch targets + AI-crawler stance (v2 P2 — spec §5.3)
# =========================================================================
# The crawler's own user-agent (also the SecureFetcher default) — robots.txt
# policy is evaluated for this identity.
SITE_HEALTH_USER_AGENT: Final = "CiteLadderSiteHealthBot/1.0 (+https://citeladder)"
# Well-known paths probed once per crawl during the root discover task.
ROBOTS_TXT_PATH: Final = "/robots.txt"
LLMS_TXT_PATH: Final = "/llms.txt"
# Default sitemap probe paths when robots.txt declares no Sitemap directive.
SITEMAP_DEFAULT_PATHS: Final[tuple[str, ...]] = ("/sitemap.xml",)
# The AI crawlers whose robots.txt stance the ``technical.ai_crawler_access``
# rule reports on (spec §5.3; Cloudflare made AI-bot blocking the default for
# new domains in July 2025, so an explicit allow matters).
AI_CRAWLER_BOTS: Final[tuple[str, ...]] = (
    "GPTBot",
    "ClaudeBot",
    "PerplexityBot",
    "Google-Extended",
)
# Stance tokens recorded per bot in ``site_facts.robots.ai_crawlers``.
AI_CRAWLER_STANCE_ALLOW: Final = "allow"
AI_CRAWLER_STANCE_BLOCK: Final = "block"
# robots.txt fetch classification recorded in ``site_facts.robots.status``
# (SH-1/B2): a 404 means the site simply HAS no robots.txt — crawling
# proceeds fail-open and the AI-crawler stance defaults to allow — which is a
# different finding than robots.txt being unreachable (network error / 5xx).
ROBOTS_FETCH_STATUS_FETCHED: Final = "fetched"
ROBOTS_FETCH_STATUS_NOT_FOUND: Final = "not_found"
ROBOTS_FETCH_STATUS_FETCH_FAILED: Final = "fetch_failed"

# =========================================================================
# Bot-block signatures (spec §5.4)
# =========================================================================
ACQUISITION_TRANSPORT_HTTPX: Final = "httpx"
ACQUISITION_TRANSPORT_CURL_CFFI: Final = "curl_cffi"
ACQUISITION_TRANSPORT_BROWSER: Final = "patchright"
ACQUISITION_TRIGGER_INITIAL: Final = "initial"
ACQUISITION_TRIGGER_CHALLENGE: Final = "challenge"
ACQUISITION_TRIGGER_BLOCK_STATUS: Final = "block_status"
ACQUISITION_TRIGGER_LOW_CONTENT: Final = "low_content"
# A 2xx HTML response whose BYTES are ample but whose readable TEXT is not: a
# client-rendered shell. Deliberately distinct from ``low_content`` — one says
# the server sent almost nothing, the other says the server sent a page whose
# content is not in the response. Collapsing them would hide which of the two
# an operator is looking at.
ACQUISITION_TRIGGER_JS_SHELL: Final = "js_shell"
ACQUISITION_TRIGGER_CURL_UNAVAILABLE: Final = "curl_unavailable"
ACQUISITION_TRIGGER_CURL_UNUSABLE: Final = "curl_unusable"
# The initial crawler request is an honestly-identified HTTP request. A frozen,
# server-owned acquisition ladder may use an impersonating fallback only after
# these configured signatures provide evidence that the initial request was
# challenged, blocked, or implausibly thin. The signatures remain deliberately
# distinctive so ordinary pages never trigger an expensive fallback.
#
# A block is signalled by a challenge/block body marker within the first
# BOT_BLOCK_MARKER_SCAN_BYTES of the DECODED body. Markers are matched
# case-folded and are deliberately distinctive challenge-platform strings —
# never generic words like "captcha" that ordinary pages legitimately contain,
# so a normal page cannot be misreported as blocked.
#
# Status codes are deliberately NOT a signal: 401/403/503 used to be the cheap
# trigger for an impersonated retry, and only a second blocked response proved
# a block. With no retry, a status-only rule would relabel every members-only
# 401 as bot protection, so those keep their http_4xx/http_5xx classification.
BOT_BLOCK_BODY_MARKERS: Final[tuple[str, ...]] = (
    "cf-chl",
    "challenge-platform",
    # Distinctive Cloudflare interstitial title, verbatim including the
    # ellipsis — the bare phrase "just a moment" is ordinary English and would
    # misreport a healthy page whose copy contains it as blocked.
    "just a moment...",
    # NOTE: "attention required" was removed — it is plain English, not a
    # distinctive challenge-platform string, so it could false-positive a
    # healthy page into ERROR_BOT_BLOCKED (no artifact, no analysis).
    "px-captcha",
    "perimeterx",
    "datadome",
    "incapsula",
    "distil_r",
)
BOT_BLOCK_MARKER_SCAN_BYTES: Final = 8192

# =========================================================================
# Safe per-task error tokens (never persist raw bodies/sensitive headers)
# =========================================================================
ERROR_ROBOTS_DENIED: Final = "robots_denied"
# robots.txt answered 5xx: RFC 9309 treats this as a complete (temporary)
# disallow. Distinct from a real disallow so the UI can say "robots
# unavailable" instead of "blocked by robots rules".
ERROR_ROBOTS_UNAVAILABLE: Final = "robots_unavailable"
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
ERROR_URL_ADMISSION_REJECTED: Final = "url_admission_rejected"
ERROR_ACQUISITION_UNAVAILABLE: Final = "acquisition_unavailable"
# The fetch came back carrying a challenge-platform marker from
# ``BOT_BLOCK_BODY_MARKERS`` — the body is the ONLY signal (status codes are
# deliberately not one; see that table). Distinct from the generic ``http_4xx``
# so a blocked page presents as ``blocked``, while a plain 401/403/503 with no
# challenge marker keeps its ``http_4xx``/``http_5xx`` classification. The
# blocked response is retained in the per-call trace only; it never becomes an
# analyzable artifact.
ERROR_BOT_BLOCKED: Final = "bot_blocked"
# Per-network-call outcome tokens persisted on ``SiteFetchAttempt.outcome``
# (one row per real call; the writer and the read projections share them).
FETCH_ATTEMPT_OUTCOME_SUCCESS: Final = "success"
FETCH_ATTEMPT_OUTCOME_ERROR: Final = "error"
SITE_FETCH_ERROR_TOKENS: Final[frozenset[str]] = frozenset(
    {
        ERROR_ROBOTS_DENIED,
        ERROR_ROBOTS_UNAVAILABLE,
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
        ERROR_URL_ADMISSION_REJECTED,
        ERROR_ACQUISITION_UNAVAILABLE,
        ERROR_BOT_BLOCKED,
    }
)

# Policy-denial/blocking codes: when the latest analyze task ended under one of
# these, the page's presentation status is `blocked` (rather than the generic
# `error`). All other terminal-unsuccessful analysis maps to `error`.
POLICY_BLOCKING_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        ERROR_ROBOTS_DENIED,
        ERROR_ROBOTS_UNAVAILABLE,
        ERROR_SSRF_BLOCKED,
        ERROR_BOT_BLOCKED,
    }
)

# =========================================================================
# Coded API failures (stable tokens returned to the client)
# =========================================================================
CODE_MONITORING_NOT_ALLOWED: Final = "monitoring_not_allowed"
CODE_QUOTA_EXCEEDED: Final = "site_health_quota_exceeded"
CODE_STALE_SELECTION_VERSION: Final = "stale_selection_version"
CODE_CRAWL_ALREADY_ACTIVE: Final = "crawl_already_active"
CODE_DISCOVERY_LIMIT_EXCEEDED: Final = "site_health_discovery_limit_exceeded"
CODE_ANALYSIS_LIMIT_EXCEEDED: Final = "site_health_analysis_limit_exceeded"
CODE_PHASE_ALREADY_RUNNING: Final = "site_health_phase_already_running"
CODE_PHASE_NOT_RESUMABLE: Final = "site_health_phase_not_resumable"
CODE_ADVANCED_CONTROLS_UNAVAILABLE: Final = "advanced_controls_unavailable"

# Bound one resume operation so a large cancelled frontier is cloned across
# multiple explicit discovery batches instead of one oversized transaction.
CANCELLED_DISCOVERY_TASK_CLONE_LIMIT: Final = 32

# =========================================================================
# Crawl lifecycle event types (safe SSE payloads; Free excludes totals)
# =========================================================================
EVENT_CRAWL_CREATED: Final = "crawl.created"
EVENT_CRAWL_QUEUED: Final = "crawl.queued"
EVENT_CRAWL_RUNNING: Final = "crawl.running"
EVENT_DISCOVERY_PROGRESS: Final = "discovery.progress"
EVENT_ANALYSIS_PROGRESS: Final = "analysis.progress"
EVENT_DISCOVERY_STARTED: Final = "discovery.started"
EVENT_DISCOVERY_STOPPED: Final = "discovery.stopped"
EVENT_ANALYSIS_STARTED: Final = "analysis.started"
EVENT_ANALYSIS_STOPPED: Final = "analysis.stopped"
EVENT_CRAWL_STATUS: Final = "crawl.status"
EVENT_CRAWL_COMPLETED: Final = "crawl.completed"
# Emitted INSTEAD of ``crawl.completed`` when the crawl terminalizes as
# fully-failed (SH-2/B1): SSE/replay consumers never see a misleading
# "completed" event for a failed run. The payload carries the failure summary
# (code + human message + attempts/status_code/target_url).
EVENT_CRAWL_FAILED: Final = "crawl.failed"
EVENT_CRAWL_CANCELLED: Final = "crawl.cancelled"

# =========================================================================
# Versions (extractor / analyzer / rule catalog / scoring / classifier)
# =========================================================================
# Bumped whenever the deterministic extraction/rule/scoring logic changes so
# every derived row (facts, evaluations, issues, scores) is traceable to the
# exact rules that produced it (invariant 4). Each version is bumped by
# exactly one v2 phase (docs/roadmap/site-health-v2-page-aware.md §6): P1
# bumped ANALYZER/SCORING and introduced CLASSIFIER; P2 bumps EXTRACTOR (new
# bounded fact fields: author/dates/outbound_domains/landmarks/
# question_heading_ratio/expand_gated_ratio/hreflang_alternates/
# first_answer_text/inline_script_chars/h3 texts, plus wider structured-data
# recognition) and RULE_CATALOG (the expanded 33-rule sh-rules-2 catalog);
# SCORING stays sh-scoring-2 (formula unchanged; weight-0 rules score through
# the existing formula) and CLASSIFIER stays sh-classifier-1.
# sh-extractor-3 adds the industry-role classifier facts (cta_text /
# form_fields / link_context) to the bounded page facts. Existing fields are
# unchanged, so an sh-extractor-2 artifact stays readable; only pages parsed at
# 3+ can produce a conversion/journey role from CTA or form evidence.
# sh-extractor-4 adds ``contact_points`` and ``money_mentions`` — the visible
# evidence the knowledge layer needs for the ``contact_point`` and
# ``price_or_fee`` core predicates. Both are read from VISIBLE content on
# purpose: the first acceptance corpus publishes zero structured data, so a
# knowledge layer that could only read JSON-LD would find nothing on a real
# site and report an empty knowledge model as if it were an empty business.
EXTRACTOR_VERSION: Final = "sh-extractor-4"
ANALYZER_VERSION: Final = "sh-analyzer-2"
RULE_CATALOG_VERSION: Final = "sh-rules-2"
SCORING_VERSION: Final = "sh-scoring-2"
CLASSIFIER_VERSION: Final = "sh-classifier-2"

# =========================================================================
# Page-type classification (v2 P1 — spec §5.1)
# =========================================================================
# The standard taxonomy every analyzed page is classified into by the pure
# ``analysis/site_health/page_kinds.py`` classifier (no I/O, no ORM, no LLM —
# invariant 9). Every pattern table, threshold, and weight below is
# config-owned (invariant 1); the classifier only reads them.
PAGE_KIND_HOMEPAGE: Final = "homepage"
PAGE_KIND_ARTICLE: Final = "article"
PAGE_KIND_PRODUCT: Final = "product"
PAGE_KIND_CATEGORY: Final = "category"
PAGE_KIND_PRICING: Final = "pricing"
PAGE_KIND_DOCS: Final = "docs"
PAGE_KIND_FAQ: Final = "faq"
PAGE_KIND_ABOUT_CONTACT: Final = "about_contact"
PAGE_KIND_OTHER: Final = "other"
PAGE_KIND_SERVICE: Final = "service"
PAGE_KIND_LOCAL: Final = "local"
PAGE_KIND_GUIDE: Final = "guide"
PAGE_KIND_COMPARISON: Final = "comparison"
PAGE_KIND_CASE_STUDY_REVIEW: Final = "case_study_review"
PAGE_KIND_TRUST_POLICY: Final = "trust_policy"
PAGE_KINDS: Final[tuple[str, ...]] = (
    PAGE_KIND_HOMEPAGE,
    PAGE_KIND_ARTICLE,
    PAGE_KIND_PRODUCT,
    PAGE_KIND_CATEGORY,
    PAGE_KIND_PRICING,
    PAGE_KIND_DOCS,
    PAGE_KIND_FAQ,
    PAGE_KIND_ABOUT_CONTACT,
    PAGE_KIND_SERVICE,
    PAGE_KIND_LOCAL,
    PAGE_KIND_GUIDE,
    PAGE_KIND_COMPARISON,
    PAGE_KIND_CASE_STUDY_REVIEW,
    PAGE_KIND_TRUST_POLICY,
    PAGE_KIND_OTHER,
)

# Signal 1 (highest priority): the root path is a deterministic homepage
# special case. Paths are matched NORMALIZED (lowercase, trailing slashes
# stripped, so "/" and "" both normalize to the empty root). Locale roots
# ("/en", "/en-us", ...) and index variants resolve through this equivalents
# table; anything NOT listed deliberately falls through to the lower-priority
# signals (an unlisted "/uk/" root is not assumed to be a homepage).
HOMEPAGE_PATH_EQUIVALENTS: Final[frozenset[str]] = frozenset(
    {
        "",  # the root path itself ("/" or empty)
        "/index",
        "/index.html",
        "/index.htm",
        "/index.php",
        "/index.asp",
        "/index.aspx",
        "/home",
        "/home.html",
        "/default.html",
        "/default.aspx",
        # Locale roots (bounded, curated).
        "/en",
        "/en-us",
        "/en-gb",
        "/en-au",
        "/en-ca",
        "/fr",
        "/fr-fr",
        "/fr-ca",
        "/de",
        "/de-de",
        "/es",
        "/es-es",
        "/es-mx",
        "/pt",
        "/pt-br",
        "/it",
        "/it-it",
        "/nl",
        "/nl-nl",
        "/ja",
        "/ja-jp",
        "/ko",
        "/ko-kr",
        "/zh",
        "/zh-cn",
        "/zh-tw",
        "/ru",
        "/pl",
        "/sv",
        "/da",
        "/fi",
        "/no",
        "/tr",
        "/ar",
        "/hi",
        "/id",
        "/th",
        "/vi",
    }
)

# Signal 2: ordered URL path patterns — FIRST MATCH WINS. Each entry is
# (page_kind, regex) matched with re.match against the normalized path
# (lowercase, trailing slashes stripped). Initial table per spec §5.1.
PAGE_KIND_PATH_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    # ``guides`` is deliberately NOT here: first match wins, so listing it made
    # /guides an article and left PAGE_KIND_GUIDE's own pattern unreachable for
    # the plural form while /guide classified correctly.
    (PAGE_KIND_ARTICLE, r"^/(blog|news)(/|$)"),
    (PAGE_KIND_PRODUCT, r"^/(products?|p|shop)(/|$)"),
    (PAGE_KIND_CATEGORY, r"^/(category|collections)(/|$)"),
    (PAGE_KIND_SERVICE, r"^/(services?|solutions?)(/|$)"),
    (PAGE_KIND_LOCAL, r"^/(locations?|stores?|offices?)(/|$)"),
    (PAGE_KIND_GUIDE, r"^/(guides?|how-to)(/|$)"),
    (PAGE_KIND_COMPARISON, r"^/(compare|comparison|vs)(/|$)"),
    (PAGE_KIND_PRICING, r"^/pricing(/|$)"),
    (PAGE_KIND_DOCS, r"^/(docs|reference)(/|$)"),
    (PAGE_KIND_FAQ, r"^/(faq|help)(/|$)"),
    (PAGE_KIND_ABOUT_CONTACT, r"^/(about|contact)(/|$)"),
    (PAGE_KIND_CASE_STUDY_REVIEW, r"^/(case-studies|reviews?|testimonials?)(/|$)"),
    (PAGE_KIND_TRUST_POLICY, r"^/(privacy|terms|security|trust|policies?)(/|$)"),
)

# Signal 3: content/heading heuristics. Evaluated in a fixed sub-order
# (faq -> product -> article, per the spec table); first matched heuristic
# wins. All token tables, patterns, and thresholds live here.
#
# FAQ: question-form heading ratio over the bounded h2 + h3 texts (spec
# §5.1; h3 texts are extracted since sh-extractor-2). A heading is
# question-form when it ends with "?" or starts with a question word.
PAGE_KIND_QUESTION_WORDS: Final[frozenset[str]] = frozenset(
    {
        "who",
        "what",
        "when",
        "where",
        "why",
        "how",
        "which",
        "whose",
        "whom",
        "can",
        "could",
        "should",
        "would",
        "will",
        "is",
        "are",
        "do",
        "does",
        "did",
    }
)
# Minimum headings required before a ratio is meaningful (a single question
# heading out of one is not a FAQ page) and the required question ratio.
PAGE_KIND_FAQ_MIN_HEADINGS: Final = 3
PAGE_KIND_FAQ_QUESTION_RATIO: Final = 0.6
# Product: a price token AND a cart marker in the bounded body text.
PAGE_KIND_PRICE_PATTERN: Final = (
    r"(?:[$€£¥]\s?\d+(?:[.,]\d{1,2})?"
    r"|\b(?:USD|EUR|GBP|AUD|CAD|JPY|INR)\s?\d+(?:[.,]\d{1,2})?)"
)
PAGE_KIND_CART_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "add to cart",
        "add-to-cart",
        "add to bag",
        "add-to-bag",
        "add to basket",
        "add-to-basket",
        "buy now",
    }
)
# Article: author byline + date within a bounded prefix of the body text
# (the sh-extractor-2 author/date facts exist too, but this heuristic keeps
# reading the visible body text — byline+date co-location is the signal).
PAGE_KIND_ARTICLE_SCAN_CHARS: Final = 2000
PAGE_KIND_BYLINE_PATTERN: Final = r"\b[Bb]y\s+[A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+){1,2}\b"
PAGE_KIND_DATE_PATTERN: Final = (
    r"(?:\b\d{4}-\d{2}-\d{2}\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b)"
)

# Signal 4 (lowest priority): structured-data types from
# facts["structured_data"]["types"] mapped to a page type. DELIBERATE
# SEMANTICS (spec §5.1): signals 1-3 OUTRANK this signal on conflict — the
# URL/content type wins and the schema-suggested type is recorded in the
# evidence instead, so the schema's claim about the page can never decide
# the page's type (which would make type-expected-schema rules circular).
# NOTE: the sh-extractor-2 parser recognizes the full
# STRUCTURED_DATA_RECOGNIZED_TYPES set into
# facts["structured_data"]["types"], so every type below can fire.
PAGE_KIND_SCHEMA_TYPE_MAP: Final[dict[str, str]] = {
    "Article": PAGE_KIND_ARTICLE,
    "BlogPosting": PAGE_KIND_ARTICLE,
    "NewsArticle": PAGE_KIND_ARTICLE,
    "Product": PAGE_KIND_PRODUCT,
    "FAQPage": PAGE_KIND_FAQ,
    "TechArticle": PAGE_KIND_DOCS,
    "Service": PAGE_KIND_SERVICE,
    "LocalBusiness": PAGE_KIND_LOCAL,
    "HowTo": PAGE_KIND_GUIDE,
    "Review": PAGE_KIND_CASE_STUDY_REVIEW,
}

# Signal names (recorded as bounded evidence: classified_by + signals).
PAGE_KIND_SIGNAL_ROOT_PATH: Final = "root_path"
PAGE_KIND_SIGNAL_PATH_PATTERN: Final = "path_pattern"
PAGE_KIND_SIGNAL_CONTENT_HEURISTIC: Final = "content_heuristic"
PAGE_KIND_SIGNAL_STRUCTURED_DATA: Final = "structured_data"
PAGE_KIND_SIGNAL_NONE: Final = "none"

# Each matched signal contributes its weight once; confidence is the SUM.
# Below the threshold the page classifies as "other". With these weights any
# single matched signal classifies; the threshold guards future weight
# retunes and keeps "no evidence" (0.0) firmly at "other".
PAGE_KIND_SIGNAL_WEIGHTS: Final[dict[str, float]] = {
    PAGE_KIND_SIGNAL_ROOT_PATH: 1.0,
    PAGE_KIND_SIGNAL_PATH_PATTERN: 0.8,
    PAGE_KIND_SIGNAL_CONTENT_HEURISTIC: 0.6,
    PAGE_KIND_SIGNAL_STRUCTURED_DATA: 0.5,
}
PAGE_KIND_CONFIDENCE_THRESHOLD: Final = 0.5

# Applicability token prefix for page-type-scoped rules (spec §5.2):
# ``page_kind:<type>`` resolves against ``facts["page_kind"]``.
PAGE_KIND_APPLICABILITY_PREFIX: Final = "page_kind:"


class PageKindProfile:
    """Per-page-type rule-tuning profile (frozen, config-owned).

    The profile key doubles as the rule ``applicability_key`` token this
    page type answers to (``page_kind:<type>`` — unknown tokens stay
    fail-closed in the evaluator). ``min_sufficient_words`` is the per-type
    thin-content minimum read by ``technical.thin_content`` (the v1 global
    ``MIN_SUFFICIENT_WORDS`` analysis constant moved here in v2 — invariant
    1; the check itself moved from ``aeo.sufficient_text`` to
    ``technical.thin_content`` in the sh-rules-2 catalog, spec §5.3).
    ``rule_weight_overrides`` maps ``rule_id -> weight`` resolved at
    evaluation time; sparse by design.
    """

    __slots__ = (
        "page_kind",
        "min_sufficient_words",
        "rule_weight_overrides",
    )

    def __init__(
        self,
        *,
        page_kind: str,
        min_sufficient_words: int,
        rule_weight_overrides: dict[str, float] | None = None,
    ) -> None:
        self.page_kind = page_kind
        self.min_sufficient_words = min_sufficient_words
        self.rule_weight_overrides = dict(rule_weight_overrides or {})


# Per-type profiles (spec §5.2). The ``other`` minimum preserves the v1
# global default (100 words) so unclassified pages score exactly as before;
# classified pages get type-appropriate thin-content minimums. Weight
# overrides start sparse (the mechanism is exercised at evaluation time).
PAGE_KIND_PROFILES: Final[dict[str, PageKindProfile]] = {
    # Homepages are naturally link-heavy/thin; a lower minimum and a
    # reduced thin-content weight keep them from reading as thin.
    PAGE_KIND_HOMEPAGE: PageKindProfile(
        page_kind=PAGE_KIND_HOMEPAGE,
        min_sufficient_words=40,
        rule_weight_overrides={"technical.thin_content": 1.0},
    ),
    PAGE_KIND_ARTICLE: PageKindProfile(
        page_kind=PAGE_KIND_ARTICLE, min_sufficient_words=300
    ),
    PAGE_KIND_PRODUCT: PageKindProfile(
        page_kind=PAGE_KIND_PRODUCT, min_sufficient_words=80
    ),
    PAGE_KIND_CATEGORY: PageKindProfile(
        page_kind=PAGE_KIND_CATEGORY, min_sufficient_words=60
    ),
    PAGE_KIND_PRICING: PageKindProfile(
        page_kind=PAGE_KIND_PRICING, min_sufficient_words=80
    ),
    PAGE_KIND_DOCS: PageKindProfile(page_kind=PAGE_KIND_DOCS, min_sufficient_words=150),
    PAGE_KIND_FAQ: PageKindProfile(page_kind=PAGE_KIND_FAQ, min_sufficient_words=120),
    PAGE_KIND_ABOUT_CONTACT: PageKindProfile(
        page_kind=PAGE_KIND_ABOUT_CONTACT, min_sufficient_words=60
    ),
    PAGE_KIND_SERVICE: PageKindProfile(
        page_kind=PAGE_KIND_SERVICE, min_sufficient_words=100
    ),
    PAGE_KIND_LOCAL: PageKindProfile(
        page_kind=PAGE_KIND_LOCAL, min_sufficient_words=80
    ),
    PAGE_KIND_GUIDE: PageKindProfile(
        page_kind=PAGE_KIND_GUIDE, min_sufficient_words=200
    ),
    PAGE_KIND_COMPARISON: PageKindProfile(
        page_kind=PAGE_KIND_COMPARISON, min_sufficient_words=150
    ),
    PAGE_KIND_CASE_STUDY_REVIEW: PageKindProfile(
        page_kind=PAGE_KIND_CASE_STUDY_REVIEW, min_sufficient_words=150
    ),
    PAGE_KIND_TRUST_POLICY: PageKindProfile(
        page_kind=PAGE_KIND_TRUST_POLICY, min_sufficient_words=80
    ),
    PAGE_KIND_OTHER: PageKindProfile(
        page_kind=PAGE_KIND_OTHER, min_sufficient_words=100
    ),
}


class PageKindSchemaExpectation:
    """Per-page-type expected structured-data contract (frozen, config-owned).

    ``expected_types`` are the schema.org types a page of this type should
    carry (any one of them satisfies ``aeo.schema_expected_for_type``);
    ``required_properties`` / ``recommended_properties`` are the property
    paths (dotted for one-level nesting, e.g. ``offers.price``) validated by
    ``aeo.schema_required_valid`` / ``aeo.schema_recommended_present``
    (spec §5.2). Property presence is checked against the extractor's
    bounded ``props_present`` per structured-data block.
    """

    __slots__ = (
        "page_kind",
        "expected_types",
        "required_properties",
        "recommended_properties",
    )

    def __init__(
        self,
        *,
        page_kind: str,
        expected_types: tuple[str, ...],
        required_properties: tuple[str, ...],
        recommended_properties: tuple[str, ...],
    ) -> None:
        self.page_kind = page_kind
        self.expected_types = expected_types
        self.required_properties = required_properties
        self.recommended_properties = recommended_properties


# Per-type expected schema.org types + required/recommended property splits
# (spec §5.2 table, verbatim). Extends — never replaces — the v1
# presence-only ``STRUCTURED_DATA_REQUIRED_PROPERTIES`` map below.
PAGE_KIND_EXPECTED_SCHEMA: Final[dict[str, PageKindSchemaExpectation]] = {
    PAGE_KIND_HOMEPAGE: PageKindSchemaExpectation(
        page_kind=PAGE_KIND_HOMEPAGE,
        expected_types=("Organization", "WebSite"),
        required_properties=("name", "url"),
        recommended_properties=("sameAs", "logo"),
    ),
    PAGE_KIND_ARTICLE: PageKindSchemaExpectation(
        page_kind=PAGE_KIND_ARTICLE,
        expected_types=("Article", "BlogPosting", "NewsArticle"),
        required_properties=("headline", "author", "datePublished"),
        recommended_properties=("image", "dateModified"),
    ),
    PAGE_KIND_PRODUCT: PageKindSchemaExpectation(
        page_kind=PAGE_KIND_PRODUCT,
        expected_types=("Product",),
        required_properties=("name", "offers"),
        recommended_properties=(
            "offers.price",
            "offers.priceCurrency",
            "aggregateRating",
        ),
    ),
    PAGE_KIND_CATEGORY: PageKindSchemaExpectation(
        page_kind=PAGE_KIND_CATEGORY,
        expected_types=("BreadcrumbList", "CollectionPage", "ItemList"),
        required_properties=("itemListElement",),
        recommended_properties=(),
    ),
    PAGE_KIND_PRICING: PageKindSchemaExpectation(
        page_kind=PAGE_KIND_PRICING,
        expected_types=("Product", "Service"),
        required_properties=("offers",),
        # Nested paths: price and currency live on the Offer, not on the
        # Product/Service itself. Bare names never matched, so a correctly
        # marked-up pricing page was reported as missing both.
        recommended_properties=("offers.price", "offers.priceCurrency"),
    ),
    PAGE_KIND_DOCS: PageKindSchemaExpectation(
        page_kind=PAGE_KIND_DOCS,
        expected_types=("TechArticle",),
        required_properties=("headline",),
        recommended_properties=("author", "dateModified"),
    ),
    PAGE_KIND_FAQ: PageKindSchemaExpectation(
        page_kind=PAGE_KIND_FAQ,
        expected_types=("FAQPage",),
        required_properties=("mainEntity",),
        recommended_properties=(),
    ),
    PAGE_KIND_ABOUT_CONTACT: PageKindSchemaExpectation(
        page_kind=PAGE_KIND_ABOUT_CONTACT,
        expected_types=("Organization", "LocalBusiness", "ContactPage"),
        required_properties=("name",),
        recommended_properties=("contactPoint", "address"),
    ),
    PAGE_KIND_SERVICE: PageKindSchemaExpectation(
        page_kind=PAGE_KIND_SERVICE,
        expected_types=("Service",),
        required_properties=("name",),
        recommended_properties=("provider", "areaServed"),
    ),
    PAGE_KIND_LOCAL: PageKindSchemaExpectation(
        page_kind=PAGE_KIND_LOCAL,
        expected_types=("LocalBusiness",),
        required_properties=("name", "address"),
        recommended_properties=("telephone", "geo"),
    ),
    PAGE_KIND_GUIDE: PageKindSchemaExpectation(
        page_kind=PAGE_KIND_GUIDE,
        expected_types=("HowTo", "Article"),
        required_properties=("name",),
        recommended_properties=("step", "image"),
    ),
    PAGE_KIND_COMPARISON: PageKindSchemaExpectation(
        page_kind=PAGE_KIND_COMPARISON,
        expected_types=("Article", "ItemList"),
        required_properties=("name",),
        recommended_properties=("itemListElement",),
    ),
    PAGE_KIND_CASE_STUDY_REVIEW: PageKindSchemaExpectation(
        page_kind=PAGE_KIND_CASE_STUDY_REVIEW,
        expected_types=("Article", "Review"),
        required_properties=("name",),
        recommended_properties=("author", "datePublished"),
    ),
    PAGE_KIND_TRUST_POLICY: PageKindSchemaExpectation(
        page_kind=PAGE_KIND_TRUST_POLICY,
        expected_types=("WebPage",),
        required_properties=("name",),
        recommended_properties=("dateModified",),
    ),
    PAGE_KIND_OTHER: PageKindSchemaExpectation(
        page_kind=PAGE_KIND_OTHER,
        expected_types=("WebPage",),
        required_properties=("name",),
        recommended_properties=(),
    ),
}

# Every property path (incl. dotted one-level paths like ``offers.price``)
# any expectation references. The extractor records per-block presence of
# exactly this bounded set as ``props_present``, so the schema rules never
# re-walk raw JSON-LD at evaluation time.
SCHEMA_PROPERTY_PATHS: Final[frozenset[str]] = frozenset(
    path
    for expectation in PAGE_KIND_EXPECTED_SCHEMA.values()
    for path in (expectation.required_properties + expectation.recommended_properties)
)

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
        "display_label_variants",
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
        display_label_variants: dict[str, str] | None = None,
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
        # Optional per-outcome titles for a rule whose ONE condition covers
        # opposite failures. ``technical.single_h1`` fails on ``h1_count != 1``,
        # so its single title had to read "Multiple or missing H1" — which tells
        # a reader neither which one happened nor what to do. Keyed by a token
        # the projection derives from the persisted evidence; an unmatched token
        # falls back to ``display_label``, so a rule without variants (all but
        # one today) is unaffected.
        self.display_label_variants = dict(display_label_variants or {})


# The rule catalog (sh-rules-2 — v2 P2, spec §5.3). Defined here so the
# catalog has one owner and a stable version (invariant 1). The v1 set is kept
# with one deliberate rename: ``aeo.sufficient_text`` became
# ``technical.thin_content`` — the per-type-minimum word-count check belongs
# to the technical/content row of the spec table, and keeping both ids would
# double-penalize one signal in two dimensions. New in sh-rules-2:
# site_root scope (AI-crawler access, llms.txt), per-type schema validity,
# citability, extractability, and hygiene rules, plus the weight-0
# crawl_finalize trio (broken internal links, sitemap orphans, hreflang
# reciprocity) whose rows the finalize-writer owns.
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
        remediation='Add a <link rel="canonical"> pointing at the preferred URL.',
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
        applicability_key=APPLICABILITY_OBSERVED_CONTENT,
        description="Page has exactly one <h1> heading.",
        remediation="Use a single <h1> that describes the page's primary topic.",
        display_label="Missing or duplicate H1",
        display_label_variants={
            "none": "Missing H1 heading",
            "multiple": "More than one H1 heading",
        },
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
    # --- v2 P2: hygiene (per-page) ----------------------------------------
    SiteHealthRule(
        rule_id="technical.thin_content",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_CONTENT,
        severity=SEVERITY_MEDIUM,
        weight=2.0,
        applicability_key=APPLICABILITY_OBSERVED_CONTENT,
        description=(
            "Word count is below the per-page-kind minimum (PAGE_KIND_PROFILES)."
        ),
        remediation="Add substantive, answer-oriented body content to the page.",
        display_label="Thin content",
    ),
    SiteHealthRule(
        rule_id="technical.canonical_conflict",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_INDEXABILITY,
        severity=SEVERITY_MEDIUM,
        weight=1.5,
        applicability_key="has_html",
        description="Declared canonical URL differs from the final fetched URL.",
        remediation=(
            "Point the canonical at the page's final URL (or redirect the "
            "canonical target consistently)."
        ),
        display_label="Canonical URL conflict",
    ),
    SiteHealthRule(
        rule_id="technical.title_length_band",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_METADATA,
        severity=SEVERITY_LOW,
        weight=1.0,
        applicability_key="has_html",
        description="Title length falls inside the recommended band (30-60 chars).",
        remediation="Rewrite the <title> to roughly 30-60 characters.",
        display_label="Title length outside recommended band",
    ),
    SiteHealthRule(
        rule_id="technical.meta_description_length_band",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_METADATA,
        severity=SEVERITY_LOW,
        weight=1.0,
        applicability_key="has_html",
        description=(
            "Meta description length falls inside the recommended band (70-160 chars)."
        ),
        remediation="Rewrite the meta description to roughly 70-160 characters.",
        display_label="Meta description length outside recommended band",
    ),
    SiteHealthRule(
        rule_id="technical.hsts_present",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_SECURITY,
        severity=SEVERITY_LOW,
        weight=1.0,
        applicability_key="always",
        description="Response sends a Strict-Transport-Security header.",
        remediation=(
            "Serve Strict-Transport-Security on HTTPS responses to enforce "
            "secure transport."
        ),
        display_label="Missing HSTS header",
    ),
    SiteHealthRule(
        rule_id="technical.ttfb_band",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_PERFORMANCE,
        severity=SEVERITY_LOW,
        weight=1.0,
        applicability_key="always",
        description="Time to first byte is within the recommended band (<= 800 ms).",
        remediation="Reduce server response time (caching, CDN, faster origin).",
        display_label="Slow time to first byte",
    ),
    SiteHealthRule(
        rule_id="technical.uncompressed_html",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_PERFORMANCE,
        severity=SEVERITY_LOW,
        weight=1.0,
        applicability_key="always",
        description="HTML response is served compressed (gzip/deflate/br).",
        remediation="Enable gzip or brotli compression for HTML responses.",
        display_label="HTML served uncompressed",
    ),
    SiteHealthRule(
        rule_id="technical.render_blocking",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_PERFORMANCE,
        severity=SEVERITY_LOW,
        weight=1.0,
        applicability_key="has_html",
        description=(
            "Synchronous scripts + stylesheets stay under the render-blocking "
            "resource limit."
        ),
        remediation=(
            "Defer/async non-critical scripts and reduce render-blocking stylesheets."
        ),
        display_label="Too many render-blocking resources",
    ),
    # --- v2 P2: site_root scope (evaluated once per crawl, weight 0) -------
    SiteHealthRule(
        rule_id="technical.ai_crawler_access",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_INDEXABILITY,
        severity=SEVERITY_HIGH,
        weight=0.0,
        applicability_key=APPLICABILITY_SITE_ROOT,
        description=(
            "robots.txt does not block the major AI crawlers (GPTBot, "
            "ClaudeBot, PerplexityBot, Google-Extended)."
        ),
        remediation=(
            "Allow the AI crawlers you want citing your content in robots.txt "
            "(check CDN-managed default bot blocks)."
        ),
        display_label="AI crawlers blocked by robots.txt",
    ),
    SiteHealthRule(
        rule_id="aeo.llms_txt_present",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_CITABILITY,
        severity=SEVERITY_LOW,
        weight=0.0,
        applicability_key=APPLICABILITY_SITE_ROOT,
        description="Site serves an llms.txt file at the root.",
        remediation=("Publish /llms.txt summarizing the site for AI answer engines."),
        display_label="Missing llms.txt",
    ),
    # --- v2 P2: per-type schema validity (per-page) -------------------------
    SiteHealthRule(
        rule_id="aeo.schema_expected_for_type",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_STRUCTURED_DATA,
        severity=SEVERITY_HIGH,
        weight=3.0,
        applicability_key="has_html",
        description=(
            "Structured data includes a schema.org type expected for the "
            "classified page type."
        ),
        remediation=(
            "Add the expected schema.org type for this page type "
            "(PAGE_KIND_EXPECTED_SCHEMA)."
        ),
        display_label="Missing expected schema type for page type",
    ),
    SiteHealthRule(
        rule_id="aeo.schema_required_valid",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_STRUCTURED_DATA,
        severity=SEVERITY_HIGH,
        weight=3.0,
        applicability_key="has_html",
        description=(
            "Expected-type structured data carries every required property "
            "for the page type."
        ),
        remediation="Add the missing required properties to the schema markup.",
        display_label="Required schema properties missing",
    ),
    SiteHealthRule(
        rule_id="aeo.schema_recommended_present",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_STRUCTURED_DATA,
        severity=SEVERITY_LOW,
        weight=0.5,
        applicability_key="has_html",
        description=(
            "Expected-type structured data carries the recommended properties "
            "for the page type."
        ),
        remediation=("Add the recommended properties to strengthen the schema markup."),
        display_label="Recommended schema properties missing",
    ),
    SiteHealthRule(
        rule_id="aeo.schema_matches_content",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_STRUCTURED_DATA,
        severity=SEVERITY_MEDIUM,
        weight=1.5,
        applicability_key=APPLICABILITY_OBSERVED_CONTENT,
        description=(
            "Structured-data names match the visible <title>/h1 content "
            "(bounded cross-check)."
        ),
        remediation=(
            "Align schema name/headline values with the visible page content."
        ),
        display_label="Schema markup does not match visible content",
    ),
    # --- v2 P2: citability (per-page) ---------------------------------------
    SiteHealthRule(
        rule_id="aeo.author_present",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_CITABILITY,
        severity=SEVERITY_MEDIUM,
        weight=1.5,
        applicability_key=APPLICABILITY_OBSERVED_CONTENT,
        description="Page exposes an author byline (schema, meta, or article:author).",
        remediation="Add an author byline (JSON-LD author or meta name=author).",
        display_label="Missing author byline",
    ),
    SiteHealthRule(
        rule_id="aeo.date_present",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_CITABILITY,
        severity=SEVERITY_MEDIUM,
        weight=1.5,
        applicability_key=APPLICABILITY_OBSERVED_CONTENT,
        description="Page exposes a published or modified date.",
        remediation=(
            "Add machine-readable dates (JSON-LD datePublished/dateModified, "
            "article:published_time, or <time datetime>)."
        ),
        display_label="Missing published/modified date",
    ),
    SiteHealthRule(
        rule_id="aeo.outbound_citations",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_CITABILITY,
        severity=SEVERITY_LOW,
        weight=1.0,
        applicability_key=APPLICABILITY_OBSERVED_CONTENT,
        description="Page links out to at least one non-social external domain.",
        remediation="Cite authoritative external sources relevant to the content.",
        display_label="No outbound citations",
    ),
    SiteHealthRule(
        rule_id="aeo.organization_identity",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_CITABILITY,
        severity=SEVERITY_MEDIUM,
        weight=1.0,
        applicability_key=f"{PAGE_KIND_APPLICABILITY_PREFIX}{PAGE_KIND_HOMEPAGE}",
        description="Homepage Organization markup carries sameAs identity links.",
        remediation=(
            "Add sameAs links (official profiles) to the homepage Organization schema."
        ),
        display_label="Missing organization identity links",
    ),
    # --- v2 P2: extractability (per-page) -----------------------------------
    SiteHealthRule(
        rule_id="aeo.answer_first",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_CONTENT,
        severity=SEVERITY_MEDIUM,
        weight=2.0,
        applicability_key=APPLICABILITY_OBSERVED_CONTENT,
        description=(
            "The first block under the first heading is a substantive "
            "answer/definitional paragraph."
        ),
        remediation=("Open each section with a direct answer before elaborating."),
        display_label="No answer-first content structure",
    ),
    SiteHealthRule(
        rule_id="aeo.question_headings",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_CONTENT,
        severity=SEVERITY_LOW,
        weight=1.0,
        applicability_key=APPLICABILITY_OBSERVED_CONTENT,
        description="Page uses question-form h2/h3 headings.",
        remediation="Phrase section headings as the questions users ask.",
        display_label="No question-form headings",
    ),
    SiteHealthRule(
        rule_id="aeo.server_rendered_content",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_CONTENT,
        severity=SEVERITY_HIGH,
        weight=2.0,
        applicability_key="has_html",
        description=(
            "Key text is present in the server-rendered HTML (not a script-only shell)."
        ),
        remediation=(
            "Server-render or pre-render primary content so crawlers can "
            "extract it without executing JavaScript."
        ),
        display_label="Content not present in server HTML",
    ),
    SiteHealthRule(
        rule_id="aeo.no_expand_gating",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_AEO,
        category=CATEGORY_CONTENT,
        severity=SEVERITY_MEDIUM,
        weight=1.0,
        applicability_key=APPLICABILITY_OBSERVED_CONTENT,
        description=(
            "Most body text is not hidden behind click-to-expand elements "
            "(collapsed details / aria-expanded=false)."
        ),
        remediation=(
            "Keep primary content visible without interaction; avoid gating "
            "answers behind expandable sections."
        ),
        display_label="Content hidden behind expand controls",
    ),
    # --- v2 P2: crawl_finalize scope (weight 0; finalize-writer owned) ------
    SiteHealthRule(
        rule_id="technical.broken_internal_link",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_LINKS,
        severity=SEVERITY_HIGH,
        weight=0.0,
        applicability_key=APPLICABILITY_CRAWL_FINALIZE,
        description="Internal link targets probed by the crawl are reachable.",
        remediation="Fix or remove links to unreachable internal targets.",
        display_label="Broken internal links",
    ),
    SiteHealthRule(
        rule_id="technical.sitemap_orphan",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_INDEXABILITY,
        severity=SEVERITY_LOW,
        weight=0.0,
        applicability_key=APPLICABILITY_CRAWL_FINALIZE,
        description=(
            "Sitemap URLs are reachable through internal links (not "
            "sitemap-only orphans)."
        ),
        remediation=("Link sitemap-listed pages from crawlable internal navigation."),
        display_label="Sitemap orphan URLs",
    ),
    SiteHealthRule(
        rule_id="technical.hreflang_conflict",
        rule_version=RULE_CATALOG_VERSION,
        dimension=DIMENSION_TECHNICAL,
        category=CATEGORY_INDEXABILITY,
        severity=SEVERITY_MEDIUM,
        weight=0.0,
        applicability_key=APPLICABILITY_CRAWL_FINALIZE,
        description="Hreflang alternates carry reciprocal return tags.",
        remediation=(
            "Add return hreflang annotations on every alternate page so "
            "clusters are reciprocal."
        ),
        display_label="Hreflang return-tag conflict",
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

# v2 P2: every schema.org type the extractor RECOGNIZES into
# facts["structured_data"] — the v1 required-property map's types UNION every
# type any page-type expectation names (adds BlogPosting, NewsArticle,
# TechArticle, Service, LocalBusiness, ContactPage, CollectionPage, ItemList).
# Newly recognized types carry no v1 required-property contract: their blocks
# validate with ``required=()``, ``valid=True`` (the per-type expectation
# rules own required/recommended validation for them).
STRUCTURED_DATA_RECOGNIZED_TYPES: Final[frozenset[str]] = frozenset(
    STRUCTURED_DATA_REQUIRED_PROPERTIES
) | frozenset(
    schema_type
    for expectation in PAGE_KIND_EXPECTED_SCHEMA.values()
    for schema_type in expectation.expected_types
)

# =========================================================================
# Rule thresholds (v2 P2 — spec §5.3; config-owned, invariant 1)
# =========================================================================
# Length bands complementing the v1 presence rules (N/A when the field is
# empty — presence stays owned by the v1 rules).
TITLE_LENGTH_BAND: Final[tuple[int, int]] = (30, 60)
META_DESCRIPTION_LENGTH_BAND: Final[tuple[int, int]] = (70, 160)
# TTFB above this (ms) fails technical.ttfb_band.
TTFB_WARN_MS: Final = 800
# Synchronous scripts + stylesheets above this count fail
# technical.render_blocking.
RENDER_BLOCKING_MAX_RESOURCES: Final = 2
# aeo.answer_first: minimum words in the first block under the first heading.
ANSWER_FIRST_MIN_WORDS: Final = 10
# aeo.answer_first: element hops the extractor may walk PAST the first
# heading's parent when the heading is wrapped in its own container (e.g.
# <header><h1/></header><main><p>answer</p></main>) — the first non-empty
# block-level text within this many following elements is the answer.
ANSWER_FIRST_MAX_HOPS: Final = 8
# aeo.no_expand_gating: maximum fraction of body words behind click-to-expand.
EXPAND_GATED_MAX_RATIO: Final = 0.5
# aeo.server_rendered_content: below this body word count AND a script-dominated
# document, the page reads as a JS shell.
SERVER_RENDERED_MIN_WORDS: Final = 20
# aeo.server_rendered_content: <script type> values counted as JavaScript for
# the script-domination heuristic (an omitted type attribute is always JS per
# HTML spec; JSON-LD / importmap / template blocks are NOT JS and never count).
INLINE_SCRIPT_JAVASCRIPT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "module",
        "text/javascript",
        "application/javascript",
        "text/ecmascript",
        "application/ecmascript",
        "text/jscript",
    }
)
# aeo.question_headings passes when the question-form h2/h3 ratio exceeds this.
QUESTION_HEADINGS_MIN_RATIO: Final = 0.0
# Social/UGC hosts that do NOT count as outbound citations (spec §5.3:
# "outbound links to non-social external domains").
SOCIAL_DOMAINS: Final[frozenset[str]] = frozenset(
    {
        "facebook.com",
        "twitter.com",
        "x.com",
        "instagram.com",
        "linkedin.com",
        "youtube.com",
        "tiktok.com",
        "pinterest.com",
    }
)
# aeo.schema_matches_content: bounded candidate names cross-checked against
# the visible <title>/h1 text.
SCHEMA_CONTENT_MATCH_MAX_CANDIDATES: Final = 5

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
        env_prefix="SITE_HEALTH_",
        extra="ignore",
        # Same .env sources as the root Settings so SITE_HEALTH_* overrides in
        # the repo-root / backend-local .env work without exporting them.
        env_file=(str(_PROJECT_ROOT / ".env"), str(_BASE_DIR / ".env")),
        env_file_encoding="utf-8",
    )

    # --- Neutral sample policy (dev-tunable) ---
    # Production remains the intentionally small automatic crawl.  Local/dev
    # environments may opt into the guided controls explicitly; callers never
    # infer this from a request header or a plan name.
    advanced_controls_enabled: bool = False
    automatic_page_limit: int = SAMPLE_URL_LIMIT
    max_requested_page_limit: int = 500
    max_discovery_urls: int = 50_000
    max_analysis_urls: int = 50_000
    max_preview_rows: int = 500
    max_preview_input_bytes: int = 262_144
    max_seed_urls: int = 500
    max_narrowing_globs: int = 100
    max_glob_length: int = 512
    # Sample-mode crawl allowance used when the workspace's resolved
    # ``monitored_urls`` entitlement is zero: a deterministic automatic sample
    # of this many admitted URLs across the whole workspace; no user
    # selection; no count disclosure.
    sample_url_limit: int = SAMPLE_URL_LIMIT
    # Sample mode: how far discovery maps the site (inventory only — NOT
    # analyzed). Decoupled from the analysis budget above.
    sample_discovery_url_cap: int = SAMPLE_DISCOVERY_URL_CAP

    # --- Frontier / discovery bounds ---
    # Absolute frontier ceiling for a FULL (Starter) crawl to bound memory/time.
    max_frontier_urls: int = 50000
    # Max discovery depth from the root.
    max_crawl_depth: int = 20
    # Batch size for progressive inventory admission (INSERT ... ON CONFLICT).
    admission_batch_size: int = 200
    # Maximum number of prior full-discovery crawl inventories carried forward
    # into a Starter recrawl's dashboard scope. Bounds the frozen JSON config
    # and the UNION used by inventory/page queries.
    inventory_history_crawl_limit: int = 20

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

    # --- Server-owned acquisition ladder ---
    # Each crawl freezes these values in its configuration. They are kept here
    # (not in a connector) because acquisition behavior is an operational
    # policy, not application logic.
    acquisition_policy_version: str = "sh-acquisition-1"
    curl_cffi_enabled: bool = False
    curl_cffi_impersonation_profile: str = "chrome"
    # A successful but unusually small HTML document is commonly a challenge
    # shell. Zero disables this signal for installations that prefer only
    # explicit challenge/status evidence.
    curl_cffi_low_content_bytes: int = 512
    curl_cffi_trigger_statuses: tuple[int, ...] = (403, 429, 503)
    # Client-rendered-shell detection. ``curl_cffi_low_content_bytes`` measures
    # the whole RESPONSE, which is the wrong ruler for the case the browser rung
    # exists to fix: a real JS shell ships a full nav, footer, and bundle
    # reference, so its byte count is ample while its readable text is nearly
    # empty. Measured live against a public JS-shell page, the served document
    # was well over the low-content floor and never escalated — rung 3 was
    # unreachable for exactly the input it was built for.
    #
    # A response escalates as a shell only when ALL THREE hold, so an ordinary
    # short page (a brief contact page) never pays for a render:
    #   - readable text below ``js_shell_min_text_chars``;
    #   - total decoded bytes at/above ``curl_cffi_low_content_bytes`` (below
    #     that it is plain ``low_content``, a different fact);
    #   - the document actually loads script (``<script src>`` or a substantial
    #     inline script), i.e. content plausibly arrives client-side.
    # 0 disables the signal.
    js_shell_min_text_chars: int = 600
    js_shell_min_inline_script_chars: int = 1024
    # Bounded prefix of the decoded body the detector scans. Text-bearing markup
    # is front-loaded; scanning a whole multi-megabyte document to answer "is
    # this empty?" would be per-response work for no added signal.
    js_shell_scan_bytes: int = 262_144
    # Only these curl-rung failure tokens may advance to the browser rung.
    # Policy/cap/redirect failures must never be bypassed.
    browser_continue_error_codes: tuple[str, ...] = (
        ERROR_CONNECTION_FAILED,
        ERROR_TIMEOUT,
        ERROR_ACQUISITION_UNAVAILABLE,
    )
    # --- Rung 3: bundled headless browser (patchright) ---
    # The last rung of the frozen ladder. It renders a JS shell locally; there
    # is deliberately no paid acquisition vendor and no real-Chrome escalation.
    browser_enabled: bool = False
    browser_navigation_timeout_seconds: float = 20.0
    # How long readiness may wait for the DOM to settle after navigation.
    browser_readiness_timeout_seconds: float = 8.0
    # A rendered document below this size is still treated as a challenge/JS
    # shell rather than usable evidence.
    browser_low_content_bytes: int = 512
    # NOTE: the same-site JSON/XHR capture knobs that used to live here are
    # gone with the capture itself. Keeping tunables for a feature the transport
    # no longer has advertises a capability that does nothing.
    # Each pooled entry is a live browser process pinned to one resolved
    # address, so the pool is bounded and evicts least-recently-used. Contexts
    # are deliberately NOT pooled — one fresh context per fetch is what keeps
    # cookies and storage from leaking between crawled pages.
    browser_pool_max_browsers: int = 4
    # Chromium's sandbox contains code fetched from crawled sites. Disable it
    # ONLY on a platform that cannot grant the required kernel capability.
    browser_disable_sandbox: bool = False

    # --- Sitemap limits ---
    max_sitemap_index_depth: int = 3
    max_sitemap_urls: int = 50000
    max_sitemap_decoded_bytes: int = 50_000_000
    # v2 P2 site-setup ingestion (Starter crawls): how many sitemap DOCUMENTS
    # (index children included) one crawl fetches, and how many sitemap URLs
    # one crawl admits into the frontier (bounded, deterministic).
    max_sitemap_documents: int = 32
    max_sitemap_admitted_urls: int = 5000
    # --- Site setup fetch caps (v2 P2: robots.txt / llms.txt probes) ---
    # Decoded-byte caps for the well-known file fetches (much tighter than the
    # page-fetch cap: these files are small; anything larger is abuse/error).
    robots_max_decoded_bytes: int = 512_000
    llms_txt_max_decoded_bytes: int = 262_144
    # How long a cached per-authority robots policy stays fresh before the
    # worker re-fetches it (RFC 9309 caching guidance is ~24h).
    robots_cache_ttl_seconds: float = 86_400.0
    # Hard ceiling on cached authorities. The cache is NOT bounded by the
    # crawl's own domain: link checks resolve robots for arbitrary EXTERNAL
    # link targets, so a long-lived worker would otherwise retain one policy +
    # one lock per host it ever probed. Expired entries are dropped first;
    # beyond the cap, the oldest go. 0 disables the cap.
    robots_cache_max_authorities: int = 2048

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
    # Backstop for crawl terminalization. A crawl normally goes terminal from a
    # task's finalize; any path that drains the last non-terminal task without
    # running one (a sweeper reclaim at max attempts, a killed process between
    # the queue ack and the finalize) would strand it in an active status
    # forever. The worker force-reconciles active crawls that have no
    # outstanding tasks and have not been touched for this long. Defaults to
    # 2x the lease TTL so a crawl merely between tasks is never swept up. Set
    # to 0 to disable.
    stalled_crawl_reconcile_seconds: float = 240.0
    # Bound on how many stalled crawls one sweep reconciles, keeping the
    # backstop's cost per loop iteration flat.
    stalled_crawl_reconcile_batch: int = 50

    # --- Link checking ---
    max_link_checks_per_page: int = 200
    link_check_timeout_seconds: float = 10.0
    # How many of ONE page's link probes may be in flight at once. Probes used
    # to run strictly serially, so a page's links cost (links x host delay) and
    # the crawl sat visibly "finished" for ~10s per page while they drained.
    # The per-host gate + crawl-delay still serialize same-host requests
    # underneath this; the ceiling keeps a 200-link page from queueing 200
    # simultaneous probes.
    link_check_concurrency: int = 8

    # --- Export ---
    # Bounds how many rows ``_export_items`` materializes into memory for a
    # single CSV/Markdown export before it truncates, so a very large Starter
    # inventory can never exhaust memory on one request.
    max_export_items: int = 20_000

    # --- SSE / events ---
    sse_poll_interval_seconds: float = 2.0
    sse_max_duration_seconds: float = 300.0

    @model_validator(mode="after")
    def _validate_sample_limits(self) -> SiteHealthSettings:
        """Reject a negative sample limit from env overrides.

        The limit feeds quota arithmetic and SQL ``LIMIT`` clauses; a negative
        value would silently break sampling. Zero stays allowed (an
        intentional "no sample" configuration).
        """
        for name in ("sample_url_limit", "sample_discovery_url_cap"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.inventory_history_crawl_limit <= 0:
            raise ValueError("inventory_history_crawl_limit must be positive")
        for name in (
            "automatic_page_limit",
            "max_requested_page_limit",
            "max_discovery_urls",
            "max_analysis_urls",
            "max_preview_rows",
            "max_preview_input_bytes",
            "max_seed_urls",
            "max_narrowing_globs",
            "max_glob_length",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        return self

    @model_validator(mode="after")
    def _validate_acquisition_ladder(self) -> SiteHealthSettings:
        """Keep fallback behavior bounded, server-owned, and reproducible."""
        if not self.acquisition_policy_version.strip():
            raise ValueError("acquisition_policy_version must not be empty")
        if not self.curl_cffi_impersonation_profile.strip():
            raise ValueError("curl_cffi_impersonation_profile must not be empty")
        if self.curl_cffi_low_content_bytes < 0:
            raise ValueError("curl_cffi_low_content_bytes must not be negative")
        if any(
            status < 100 or status > 599 for status in self.curl_cffi_trigger_statuses
        ):
            raise ValueError("curl_cffi_trigger_statuses must be HTTP status codes")
        if self.browser_low_content_bytes < 0:
            raise ValueError("browser_low_content_bytes must not be negative")
        if self.browser_navigation_timeout_seconds <= 0:
            raise ValueError("browser_navigation_timeout_seconds must be positive")
        if self.browser_readiness_timeout_seconds <= 0:
            raise ValueError("browser_readiness_timeout_seconds must be positive")
        if self.browser_pool_max_browsers < 1:
            raise ValueError("browser_pool_max_browsers must be at least 1")
        # Negative bounds do not disable a signal, they invert it: a negative
        # scan window makes every body read as empty, and a negative text floor
        # makes every 2xx page a shell. Zero is the documented "off" value for
        # the two that gate the signal.
        for name in ("js_shell_min_text_chars", "js_shell_scan_bytes"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        # This one has no "off" meaning: zero would make every empty inline
        # <script> count as application code, so an ordinary analytics stub
        # would escalate a static page to a browser render.
        if self.js_shell_min_inline_script_chars < 1:
            raise ValueError("js_shell_min_inline_script_chars must be positive")
        return self

    @model_validator(mode="after")
    def _validate_discovery_cap(self) -> SiteHealthSettings:
        """Discovery must map a superset of what it analyzes.

        A cap below the sample budget would starve analysis of candidates it is
        entitled to fetch. Its own validator (rather than a branch bolted onto
        ``_validate_sample_limits``) keeps that method on its downward
        complexity ratchet.
        """
        if self.sample_discovery_url_cap < self.sample_url_limit:
            raise ValueError(
                "sample_discovery_url_cap must not be less than sample_url_limit"
            )
        return self

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
        if self.stalled_crawl_reconcile_batch <= 0:
            raise ValueError("stalled_crawl_reconcile_batch must be positive")
        if self.stalled_crawl_reconcile_seconds < 0:
            raise ValueError("stalled_crawl_reconcile_seconds must not be negative")
        if (
            0 < self.stalled_crawl_reconcile_seconds
            and self.stalled_crawl_reconcile_seconds <= self.lease_ttl_seconds
        ):
            # A threshold inside the lease window could force-reconcile a crawl
            # whose last task is still legitimately leased and about to write.
            raise ValueError(
                "stalled_crawl_reconcile_seconds must exceed lease_ttl_seconds "
                "(or be 0 to disable)"
            )
        for name in (
            "global_concurrency",
            "per_host_concurrency",
            "worker_concurrency",
            "link_check_concurrency",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.per_host_concurrency > self.global_concurrency:
            raise ValueError("per_host_concurrency must not exceed global_concurrency")
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


def _site_crawl_task_model() -> type[SiteCrawlTask]:
    # Lazy import: this config module must never import a model at import time
    # (would create a config <-> models circular import).
    from app.models.site_health import SiteCrawlTask

    return SiteCrawlTask


def _site_task_claim_order(model: type[SiteCrawlTask]) -> tuple:
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
SITE_CRAWL_QUEUE_SPEC: Final[PostgresQueueSpec[SiteCrawlTask]] = PostgresQueueSpec(
    model_ref=_site_crawl_task_model,
    lease_ttl=lambda: site_health_settings.lease_ttl_seconds,
    claim_order=_site_task_claim_order,
    max_attempts_error=ERROR_MAX_ATTEMPTS,
    # A crawl terminalizes only via the worker's reconcile, which runs in a
    # task's finalize. The sweeper failing a task at max attempts bypasses that
    # path entirely, so it must report the owning crawl for reconciliation —
    # otherwise a crawl whose LAST task the sweeper failed stays 'running'
    # forever (no snapshot, no completion event, endless client polling).
    parent_id_attr="crawl_id",
)
