# LLM Analytics / AI-referral configuration (invariant 1: config lives here).
#
# Owns every tunable knob + vocabulary token for the LLM Analytics surface
# (docs/roadmap/llm-analytics.md section 8): the VERSIONED deterministic
# AI-referral rule tables (host / UTM / user-agent — data, not code) and
# their ``AI_REFERRAL_RULE_VERSION``, the ``ai_source`` vocabulary and its
# mapping onto the audited logical-engine ids (invariant 10), the snapshot
# window/granularity/TTL knobs, the correlation minimum-sample floor, the
# referral sanitization contract (``REFERRAL_SANITIZE_VERSION`` + the raw and
# URL-param allowlists + retention, invariant 6 privacy), and the analytics
# worker lease TTL.
#
# Classification is DETERMINISTIC RULES ONLY — no LLM may be introduced to
# "guess" a source (invariant 9). Service/domain code READS these values; it
# never hard-codes the literals inline.
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The audited logical-engine vocabulary (chatgpt|gemini|claude, invariant 10)
# is OWNED by the provider catalog — imported, never re-literalized
# (invariant 2).
from app.core.config.provider_catalog import (
    ENGINE_CHATGPT,
    ENGINE_CLAUDE,
    ENGINE_GEMINI,
)
from app.core.config.task_queue import ERROR_MAX_ATTEMPTS, PostgresQueueSpec

if TYPE_CHECKING:
    # Type-only: config never imports a model at runtime (circular import).
    from app.models.analytics import AnalyticsTask

# The day|week|month snapshot-bucket vocabulary is shared with the Traffic
# projection (same concept) and OWNED by config/traffic.py — aliased here,
# never forked (invariant 2).
from app.core.config.traffic import (
    TRAFFIC_GRANULARITY_DAY,
    TRAFFIC_SNAPSHOT_GRANULARITIES,
)

# --- Provenance versions (invariant 4) ---------------------------------------
# Stamped onto every ``ReferralClassification.rule_version`` so a derived row
# traces to the exact rule table that produced it. Bumped whenever a rule is
# added/changed/removed.
AI_REFERRAL_RULE_VERSION: Final = "ai-referral-rules-1"
# Versions the deterministic redaction pass over every ``ReferralEvent``
# (applied BEFORE the immutable write, invariant 3/6). Stamped by the CALLER
# (the ingest projection) onto ``ReferralEvent.sanitize_version``.
REFERRAL_SANITIZE_VERSION: Final = "referral-sanitize-1"

# --- AI-source vocabulary + logical-engine mapping (invariant 10) ------------
AI_SOURCE_CHATGPT: Final = "chatgpt"
AI_SOURCE_GEMINI: Final = "gemini"
AI_SOURCE_CLAUDE: Final = "claude"
AI_SOURCE_PERPLEXITY: Final = "perplexity"
AI_SOURCE_COPILOT: Final = "copilot"
AI_SOURCE_GOOGLE_AI_OVERVIEW: Final = "google_ai_overview"
# Bucket for unmatched events (is_ai_referral=false) — never a guess.
AI_SOURCE_OTHER: Final = "other"
AI_SOURCES: Final[frozenset[str]] = frozenset(
    {
        AI_SOURCE_CHATGPT,
        AI_SOURCE_GEMINI,
        AI_SOURCE_CLAUDE,
        AI_SOURCE_PERPLEXITY,
        AI_SOURCE_COPILOT,
        AI_SOURCE_GOOGLE_AI_OVERVIEW,
        AI_SOURCE_OTHER,
    }
)

# ``ai_source`` -> audited ``logical_engine`` where one exists (invariant 10),
# so referral + visibility analytics share a join key. Sources outside the
# audited three deliberately have NO entry -> ``logical_engine`` stays null.
AI_SOURCE_TO_LOGICAL_ENGINE: Final[dict[str, str]] = {
    AI_SOURCE_CHATGPT: ENGINE_CHATGPT,
    AI_SOURCE_GEMINI: ENGINE_GEMINI,
    AI_SOURCE_CLAUDE: ENGINE_CLAUDE,
}

# --- Classification signal + confidence vocabulary ----------------------------
# Which signal tier fired the matched rule (fixed referrer -> utm ->
# user_agent priority, invariant 9 determinism).
MATCH_SIGNAL_REFERRER: Final = "referrer"
MATCH_SIGNAL_UTM: Final = "utm"
MATCH_SIGNAL_USER_AGENT: Final = "user_agent"
MATCH_SIGNALS: Final[frozenset[str]] = frozenset(
    {MATCH_SIGNAL_REFERRER, MATCH_SIGNAL_UTM, MATCH_SIGNAL_USER_AGENT}
)

# Deterministic confidence buckets. ``exact`` = the signal is an explicit
# platform identifier (known host / platform-set UTM tag); ``heuristic`` = a
# user-agent substring inference.
CONFIDENCE_EXACT: Final = "exact"
CONFIDENCE_HEURISTIC: Final = "heuristic"
CONFIDENCE_BUCKETS: Final[frozenset[str]] = frozenset(
    {CONFIDENCE_EXACT, CONFIDENCE_HEURISTIC}
)


# --- Deterministic rule tables (llm-analytics.md section 4) -------------------
@dataclass(frozen=True)
class AiReferralHostRule:
    """One known AI-referrer hostname -> ``ai_source`` (suffix-safe match).

    Matching reuses ``analysis/normalization.domain_matches``: the candidate
    host must EQUAL ``host`` or be a subdomain of it, so
    ``notchatgpt.com`` never matches ``chatgpt.com``.
    """

    rule_id: str
    host: str  # normalized bare host (lowercase, no scheme/www)
    ai_source: str
    confidence: str = CONFIDENCE_EXACT


@dataclass(frozen=True)
class AiReferralUtmRule:
    """One UTM equality rule for platforms that tag outbound links.

    Every non-``None`` constraint must equal the corresponding signal after
    normalization (strip + casefold). A rule with NO constraints is a config
    error and fails loud at import time.
    """

    rule_id: str
    ai_source: str
    utm_source: str | None = None  # casefolded exact-match literal
    utm_medium: str | None = None  # casefolded exact-match literal
    confidence: str = CONFIDENCE_EXACT

    def __post_init__(self) -> None:
        if self.utm_source is None and self.utm_medium is None:
            raise ValueError(
                f"AiReferralUtmRule {self.rule_id!r} constrains nothing"
            )


@dataclass(frozen=True)
class AiReferralUaRule:
    """One verified AI-ASSISTANT user-agent substring (casefolded).

    For server-log ingest only; assistant/user-triggered fetcher tokens.
    Verified CRAWLER identification (GPTBot & co.) is Release 1.3 server/edge
    log ingestion — cross-referenced, deliberately NOT matched here.
    """

    rule_id: str
    ai_source: str
    substring: str  # casefolded containment literal
    confidence: str = CONFIDENCE_HEURISTIC


# Referrer-host allow-map (highest priority tier). Bare hosts only;
# subdomains of each listed host match via the suffix-safe comparison.
AI_REFERRAL_HOST_RULES: Final[tuple[AiReferralHostRule, ...]] = (
    AiReferralHostRule("host-chatgpt-com", "chatgpt.com", AI_SOURCE_CHATGPT),
    AiReferralHostRule("host-chat-openai-com", "chat.openai.com", AI_SOURCE_CHATGPT),
    AiReferralHostRule(
        "host-gemini-google-com", "gemini.google.com", AI_SOURCE_GEMINI
    ),
    AiReferralHostRule("host-claude-ai", "claude.ai", AI_SOURCE_CLAUDE),
    AiReferralHostRule(
        "host-perplexity-ai", "perplexity.ai", AI_SOURCE_PERPLEXITY
    ),
    AiReferralHostRule(
        "host-copilot-microsoft-com", "copilot.microsoft.com", AI_SOURCE_COPILOT
    ),
)

# UTM equality rules (second priority tier).
AI_REFERRAL_UTM_RULES: Final[tuple[AiReferralUtmRule, ...]] = (
    AiReferralUtmRule(
        "utm-source-chatgpt-com", AI_SOURCE_CHATGPT, utm_source="chatgpt.com"
    ),
    AiReferralUtmRule(
        "utm-source-chat-openai-com",
        AI_SOURCE_CHATGPT,
        utm_source="chat.openai.com",
    ),
    AiReferralUtmRule(
        "utm-source-chatgpt", AI_SOURCE_CHATGPT, utm_source="chatgpt"
    ),
    AiReferralUtmRule(
        "utm-source-openai", AI_SOURCE_CHATGPT, utm_source="openai"
    ),
    AiReferralUtmRule(
        "utm-source-gemini-google-com",
        AI_SOURCE_GEMINI,
        utm_source="gemini.google.com",
    ),
    AiReferralUtmRule(
        "utm-source-gemini", AI_SOURCE_GEMINI, utm_source="gemini"
    ),
    AiReferralUtmRule(
        "utm-source-claude-ai", AI_SOURCE_CLAUDE, utm_source="claude.ai"
    ),
    AiReferralUtmRule(
        "utm-source-claude", AI_SOURCE_CLAUDE, utm_source="claude"
    ),
    AiReferralUtmRule(
        "utm-source-perplexity-ai",
        AI_SOURCE_PERPLEXITY,
        utm_source="perplexity.ai",
    ),
    AiReferralUtmRule(
        "utm-source-perplexity", AI_SOURCE_PERPLEXITY, utm_source="perplexity"
    ),
    AiReferralUtmRule(
        "utm-source-copilot-microsoft-com",
        AI_SOURCE_COPILOT,
        utm_source="copilot.microsoft.com",
    ),
    AiReferralUtmRule(
        "utm-source-copilot", AI_SOURCE_COPILOT, utm_source="copilot"
    ),
    AiReferralUtmRule(
        "utm-source-google-ai-overview",
        AI_SOURCE_GOOGLE_AI_OVERVIEW,
        utm_source="google_ai_overview",
    ),
)

# AI-assistant UA substrings (lowest priority tier; server-log ingest).
AI_REFERRAL_UA_RULES: Final[tuple[AiReferralUaRule, ...]] = (
    AiReferralUaRule("ua-chatgpt-user", AI_SOURCE_CHATGPT, "chatgpt-user"),
    AiReferralUaRule("ua-claude-user", AI_SOURCE_CLAUDE, "claude-user"),
    AiReferralUaRule(
        "ua-perplexity-user", AI_SOURCE_PERPLEXITY, "perplexity-user"
    ),
)

# --- Snapshot projection knobs -------------------------------------------------
ANALYTICS_SNAPSHOT_GRANULARITIES: Final[frozenset[str]] = (
    TRAFFIC_SNAPSHOT_GRANULARITIES
)
ANALYTICS_DEFAULT_GRANULARITY: Final = TRAFFIC_GRANULARITY_DAY
ANALYTICS_MAX_WINDOW_DAYS: Final = 480
# Minimum age before a persisted AnalyticsSnapshot is rebuilt (rebuild
# cadence), in seconds.
ANALYTICS_SNAPSHOT_TTL_S: Final = 3600

# Below this many aligned day-buckets the visibility<->referral correlation
# reports ``insufficient_data`` — NEVER a fabricated coefficient
# (invariant 9; llm-analytics.md section 1).
CORRELATION_MIN_SAMPLE: Final = 8

# --- Referral sanitization contract (invariant 6 privacy) ---------------------
# Persisted ``ReferralEvent.raw`` is an ALLOWLISTED, redacted payload — only
# these keys survive the pre-write redaction pass; everything else (IPs,
# device ids, emails, free-form provider fields) is dropped.
REFERRAL_RAW_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "dataset",
        "date",
        "dimension_key",
        "referrer_host",
        "landing_path",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "ref",
    }
)
# ``landing_url`` / ``referrer_url`` query strings are stripped to this
# allowlist of non-PII marketing params: any ``utm_*`` param (prefix rule)
# plus the exact names below. Fragments and embedded credentials
# (``user:pass@``) are always dropped.
REFERRAL_URL_PARAM_ALLOWLIST: Final[frozenset[str]] = frozenset({"ref"})
REFERRAL_URL_PARAM_ALLOWLIST_PREFIXES: Final[tuple[str, ...]] = ("utm_",)

# Hex length of the persisted ``session_id_hash``: the truncated HMAC-SHA256
# of the raw session id keyed with ``Settings.referral_hash_salt`` (the raw
# id is used transiently and discarded — never persisted).
REFERRAL_SESSION_HASH_HEX_LENGTH: Final = 32

# Persisted referral data (events + their classifications) is hard-deleted
# past this horizon by the retention sweep.
REFERRAL_RETENTION_DAYS: Final = 90

# --- Analytics task-kind vocabulary (A3 queue spine) --------------------------
# The five analytics queue-row kinds. A3 lands the queue spine only (model +
# queue spec + worker skeleton); the per-kind executors are registered in the
# worker dispatch table by A5 (ingest_referrals), A6 (classify_referrals,
# referral_retention_sweep), A7 (traffic_snapshot_refresh) and A8
# (analytics_snapshot_refresh).
ANALYTICS_TASK_KIND_INGEST_REFERRALS: Final = "ingest_referrals"
ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS: Final = "classify_referrals"
ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH: Final = "traffic_snapshot_refresh"
ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH: Final = "analytics_snapshot_refresh"
ANALYTICS_TASK_KIND_REFERRAL_RETENTION_SWEEP: Final = "referral_retention_sweep"
ANALYTICS_TASK_KINDS: Final[frozenset[str]] = frozenset(
    {
        ANALYTICS_TASK_KIND_INGEST_REFERRALS,
        ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS,
        ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH,
        ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH,
        ANALYTICS_TASK_KIND_REFERRAL_RETENTION_SWEEP,
    }
)

# Error token stamped when a claimed kind has no registered executor — a
# permanent-until-deploy condition, so the worker never retries it.
ERROR_EXECUTOR_NOT_WIRED: Final = "executor_not_wired"


class AnalyticsSettings(BaseSettings):
    """Env-driven analytics worker knobs (``ANALYTICS_`` env prefix).

    The referral hash salt is deliberately NOT here: it is an env-injected
    deployment secret on the central ``Settings``
    (``Settings.referral_hash_salt``), resolved only inside the sanitization
    pass and never logged (invariant 6).
    """

    model_config = SettingsConfigDict(env_prefix="ANALYTICS_", extra="ignore")

    # Queue lease TTL for the analytics worker (A3's ANALYTICS_QUEUE_SPEC
    # reads this).
    lease_ttl_seconds: float = Field(default=120.0, gt=0)
    # Heartbeat cadence while an executor runs (must be < the lease TTL).
    heartbeat_interval_seconds: float = Field(default=30.0, gt=0)
    # Attempt budget per analytics queue row before terminal failure.
    task_max_attempts: int = Field(default=3, gt=0)
    # Idle poll interval of the worker loop.
    poll_interval_seconds: float = Field(default=1.0, gt=0)
    # Fixed retry delay after a failed attempt. Executors are DB-only
    # projections (no provider call), so no Retry-After channel exists.
    retry_delay_seconds: float = Field(default=30.0, ge=0)

    @model_validator(mode="after")
    def _check_operational_bounds(self) -> AnalyticsSettings:
        # Fail at startup, not mid-run: a heartbeat slower than the lease TTL
        # guarantees lease expiry during healthy work (same guard as the
        # content / integrations workers).
        if self.heartbeat_interval_seconds >= self.lease_ttl_seconds:
            raise ValueError(
                "heartbeat_interval_seconds must be shorter than lease_ttl_seconds"
            )
        return self


analytics_settings = AnalyticsSettings()


def _analytics_task_model() -> type[AnalyticsTask]:
    # Imported lazily so this config module never imports a model at import
    # time (would create a config <-> models circular import).
    from app.models.analytics import AnalyticsTask

    return AnalyticsTask


def _analytics_claim_order(model: type[AnalyticsTask]) -> tuple:
    # Deterministic claim order mirroring ``CONTENT_QUEUE_SPEC`` exactly:
    # priority, then FIFO by availability, then the randomized position.
    return (
        model.priority.desc(),
        model.available_at.asc(),
        model.randomized_position.asc(),
    )


# Parameterizes the one generic ``PostgresTaskQueue`` over ``AnalyticsTask``
# rows with the analytics lease TTL + claim order.
ANALYTICS_QUEUE_SPEC: Final[PostgresQueueSpec[AnalyticsTask]] = PostgresQueueSpec(
    model_ref=_analytics_task_model,
    lease_ttl=lambda: analytics_settings.lease_ttl_seconds,
    claim_order=_analytics_claim_order,
    max_attempts_error=ERROR_MAX_ATTEMPTS,
)
