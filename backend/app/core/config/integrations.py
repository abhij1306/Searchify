# GSC / GA4 / Bing integrations configuration (invariant 1: config lives here).
#
# Owns every tunable + vocabulary token for the first-party data integrations
# surface (docs/roadmap/integrations.md): the provider/transport vocabulary and
# their compatibility map, the per-transport OAuth endpoints + minimal scopes,
# the grant/mapping status + sync-kind + lifecycle-event + error tokens, the
# dataset -> dimensions/metrics query templates (cross-workstream contract C1 —
# pinned so the analytics/traffic workstreams consume these ids unchanged), the
# sync worker knobs (``IntegrationSettings``, ``INTEGRATION_`` env prefix), the
# approved-endpoint allow-list (SSRF policy), and the ``PostgresQueueSpec``
# that parameterizes the shared generic queue over ``IntegrationSyncRun`` rows.
#
# Service/worker/connector code READS these values; it never hard-codes them.
# The OAuth client id/secret are env-injected deployment secrets declared on
# the central ``Settings`` (app/core/config/__init__.py) — never literals in
# this file and never logged (invariant 6).
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.task_queue import ERROR_MAX_ATTEMPTS, PostgresQueueSpec

if TYPE_CHECKING:
    # Type-only: config never imports a model at runtime (circular import).
    from app.models.integrations import IntegrationSyncRun

# --- Providers + OAuth transports -------------------------------------------
INTEGRATION_PROVIDER_GSC: Final = "gsc"
INTEGRATION_PROVIDER_GA4: Final = "ga4"
INTEGRATION_PROVIDER_BING: Final = "bing"
INTEGRATION_PROVIDERS: Final[frozenset[str]] = frozenset(
    {INTEGRATION_PROVIDER_GSC, INTEGRATION_PROVIDER_GA4, INTEGRATION_PROVIDER_BING}
)

INTEGRATION_TRANSPORT_GOOGLE: Final = "google_oauth"
INTEGRATION_TRANSPORT_MICROSOFT: Final = "microsoft_oauth"
INTEGRATION_TRANSPORTS: Final[frozenset[str]] = frozenset(
    {INTEGRATION_TRANSPORT_GOOGLE, INTEGRATION_TRANSPORT_MICROSOFT}
)

# Provider -> transport compatibility map (spec section 3): a connection's
# provider must be compatible with its grant's transport. GSC + GA4 share ONE
# Google consent (one grant carries both connections); Bing rides a Microsoft
# grant.
INTEGRATION_PROVIDER_TRANSPORT: Final[dict[str, str]] = {
    INTEGRATION_PROVIDER_GSC: INTEGRATION_TRANSPORT_GOOGLE,
    INTEGRATION_PROVIDER_GA4: INTEGRATION_TRANSPORT_GOOGLE,
    INTEGRATION_PROVIDER_BING: INTEGRATION_TRANSPORT_MICROSOFT,
}

# --- OAuth endpoints (per transport) + redirect ------------------------------
INTEGRATION_OAUTH_AUTHORIZE_URLS: Final[dict[str, str]] = {
    INTEGRATION_TRANSPORT_GOOGLE: "https://accounts.google.com/o/oauth2/v2/auth",
    INTEGRATION_TRANSPORT_MICROSOFT: (
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    ),
}
INTEGRATION_OAUTH_TOKEN_URLS: Final[dict[str, str]] = {
    INTEGRATION_TRANSPORT_GOOGLE: "https://oauth2.googleapis.com/token",
    INTEGRATION_TRANSPORT_MICROSOFT: (
        "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    ),
}
# The Microsoft identity platform exposes no OAuth grant-revocation endpoint,
# so its entry is intentionally empty: remote revoke is Google-only and a
# Microsoft grant disconnects locally (spec section 5).
INTEGRATION_OAUTH_REVOKE_URLS: Final[dict[str, str]] = {
    INTEGRATION_TRANSPORT_GOOGLE: "https://oauth2.googleapis.com/revoke",
    INTEGRATION_TRANSPORT_MICROSOFT: "",
}
# Minimal scope set per transport (spec section 7). The ONE Google grant
# combines the GSC + GA4 read scopes so a single consent yields both
# connections (spec section 2/3). The Bing Webmaster read-scope literal is
# pinned from Microsoft docs at task I12 (plan R3); ``offline_access`` keeps
# the Microsoft grant refreshable.
INTEGRATION_OAUTH_SCOPES: Final[dict[str, tuple[str, ...]]] = {
    INTEGRATION_TRANSPORT_GOOGLE: (
        "https://www.googleapis.com/auth/webmasters.readonly",
        "https://www.googleapis.com/auth/analytics.readonly",
    ),
    INTEGRATION_TRANSPORT_MICROSOFT: ("offline_access",),
}

# OAuth callback path (the provider-registered redirect target; ``provider``
# is interpolated by the connect flow) and the frontend landing the callback
# 302s to (contract C2).
INTEGRATION_OAUTH_CALLBACK_PATH: Final = (
    "/api/v1/integrations/oauth/{provider}/callback"
)
INTEGRATION_OAUTH_LANDING_PATH: Final = "/settings?tab=integrations"

# --- Provider data API endpoints (read-only) ---------------------------------
GSC_API_BASE_URL: Final = "https://www.googleapis.com"
GSC_SEARCH_ANALYTICS_PATH: Final = (
    "/webmasters/v3/sites/{property_ref}/searchAnalytics/query"
)
GA4_API_BASE_URL: Final = "https://analyticsdata.googleapis.com"
GA4_RUN_REPORT_PATH: Final = "/v1beta/properties/{property_ref}:runReport"
# Bing Webmaster API host/path literals are pinned from Microsoft docs at I12
# (plan R3).

# Approved-endpoint allow-list (SSRF policy, master plan section 15):
# integration clients must reject any URL whose host is not in this set. The
# Bing API host is added when its literal is pinned at I12.
INTEGRATION_APPROVED_ENDPOINT_HOSTS: Final[frozenset[str]] = frozenset(
    {
        "accounts.google.com",
        "oauth2.googleapis.com",
        "www.googleapis.com",
        "analyticsdata.googleapis.com",
        "login.microsoftonline.com",
    }
)

# --- Grant + mapping statuses -------------------------------------------------
GRANT_STATUS_CONNECTED: Final = "connected"
GRANT_STATUS_NEEDS_REAUTH: Final = "needs_reauth"
GRANT_STATUS_PENDING_REVOCATION: Final = "pending_revocation"
GRANT_STATUS_REVOKED: Final = "revoked"
GRANT_STATUS_ERROR: Final = "error"
INTEGRATION_GRANT_STATUSES: Final[frozenset[str]] = frozenset(
    {
        GRANT_STATUS_CONNECTED,
        GRANT_STATUS_NEEDS_REAUTH,
        GRANT_STATUS_PENDING_REVOCATION,
        GRANT_STATUS_REVOKED,
        GRANT_STATUS_ERROR,
    }
)

MAPPING_STATUS_ACTIVE: Final = "active"
MAPPING_STATUS_DISABLED: Final = "disabled"
INTEGRATION_MAPPING_STATUSES: Final[frozenset[str]] = frozenset(
    {MAPPING_STATUS_ACTIVE, MAPPING_STATUS_DISABLED}
)

# --- Sync kinds ----------------------------------------------------------------
SYNC_KIND_SCHEDULED: Final = "scheduled"
SYNC_KIND_ON_DEMAND: Final = "on_demand"
SYNC_KIND_BACKFILL: Final = "backfill"
INTEGRATION_SYNC_KINDS: Final[frozenset[str]] = frozenset(
    {SYNC_KIND_SCHEDULED, SYNC_KIND_ON_DEMAND, SYNC_KIND_BACKFILL}
)

# --- Lifecycle events (IntegrationEvent.event_type; AuditEvent convention) ----
EVENT_INTEGRATION_CONNECTED: Final = "integration.connected"
EVENT_INTEGRATION_TESTED: Final = "integration.tested"
EVENT_INTEGRATION_SYNC_STARTED: Final = "integration.sync_started"
EVENT_INTEGRATION_SYNC_FINISHED: Final = "integration.sync_finished"
EVENT_INTEGRATION_REAUTH_REQUIRED: Final = "integration.reauth_required"
EVENT_INTEGRATION_REVOKED: Final = "integration.revoked"

# --- Error tokens --------------------------------------------------------------
# The queue-level ``max_attempts_exceeded`` token stays shared in
# config/task_queue.py; these are the integrations-specific codes stamped on
# runs/grants.
ERROR_UNMAPPED_PROPERTY: Final = "unmapped_property"
ERROR_TOKEN_REFRESH_FAILED: Final = "token_refresh_failed"
ERROR_PROVIDER_API: Final = "provider_api_error"
ERROR_RATE_LIMITED: Final = "rate_limited"
ERROR_UNAPPROVED_ENDPOINT: Final = "unapproved_endpoint"

# --- Versioning ----------------------------------------------------------------
# Versions the artifact -> IntegrationMetricRow transform code (NOT the data
# run — that identity is the run's ``resync_seq``).
INTEGRATION_IMPORTER_VERSION: Final = "integrations-importer-1"

# --- Dataset -> dimensions/metrics query templates (contract C1) ---------------
DIMENSION_KEY_SEPARATOR: Final = " | "

DATASET_GSC_PAGE_DAILY: Final = "gsc_page_daily"
DATASET_GSC_QUERY_DAILY: Final = "gsc_query_daily"
DATASET_GA4_CHANNEL_DAILY: Final = "ga4_channel_daily"
DATASET_GA4_SOURCE_MEDIUM_DAILY: Final = "ga4_source_medium_daily"
DATASET_GA4_REFERRER_DAILY: Final = "ga4_referrer_daily"
DATASET_GA4_LANDING_DAILY: Final = "ga4_landing_daily"

_GSC_SEARCH_ANALYTICS_METRICS: Final = ("clicks", "impressions", "ctr", "position")
_GA4_SESSION_METRICS: Final = ("sessions", "engagedSessions", "conversions")


@dataclass(frozen=True)
class IntegrationDatasetTemplate:
    """One provider dataset's config-owned query template (C1).

    ``dimensions`` is the DECLARED order: ``pack_dimension_key`` joins a row's
    dimension values in exactly this order, so the template is the single
    owner of ``dimension_key`` packing for both workstreams (integrations
    produces, analytics/traffic consumes).
    """

    dataset: str
    provider: str
    api_method: str
    dimensions: tuple[str, ...]
    metrics: tuple[str, ...]


# Pinned C1 dataset ids + shapes. Both workstreams code against these exact
# tuples; changing one is a cross-workstream contract change.
INTEGRATION_DATASET_TEMPLATES: Final[dict[str, IntegrationDatasetTemplate]] = {
    DATASET_GSC_PAGE_DAILY: IntegrationDatasetTemplate(
        dataset=DATASET_GSC_PAGE_DAILY,
        provider=INTEGRATION_PROVIDER_GSC,
        api_method="searchAnalytics.query",
        dimensions=("page", "date"),
        metrics=_GSC_SEARCH_ANALYTICS_METRICS,
    ),
    DATASET_GSC_QUERY_DAILY: IntegrationDatasetTemplate(
        dataset=DATASET_GSC_QUERY_DAILY,
        provider=INTEGRATION_PROVIDER_GSC,
        api_method="searchAnalytics.query",
        dimensions=("query", "date"),
        metrics=_GSC_SEARCH_ANALYTICS_METRICS,
    ),
    DATASET_GA4_CHANNEL_DAILY: IntegrationDatasetTemplate(
        dataset=DATASET_GA4_CHANNEL_DAILY,
        provider=INTEGRATION_PROVIDER_GA4,
        api_method="runReport",
        dimensions=("sessionDefaultChannelGroup", "date"),
        metrics=_GA4_SESSION_METRICS,
    ),
    DATASET_GA4_SOURCE_MEDIUM_DAILY: IntegrationDatasetTemplate(
        dataset=DATASET_GA4_SOURCE_MEDIUM_DAILY,
        provider=INTEGRATION_PROVIDER_GA4,
        api_method="runReport",
        dimensions=("sessionSource", "sessionMedium", "date"),
        metrics=_GA4_SESSION_METRICS,
    ),
    DATASET_GA4_REFERRER_DAILY: IntegrationDatasetTemplate(
        dataset=DATASET_GA4_REFERRER_DAILY,
        provider=INTEGRATION_PROVIDER_GA4,
        api_method="runReport",
        dimensions=("fullReferrer", "date"),
        metrics=_GA4_SESSION_METRICS,
    ),
    DATASET_GA4_LANDING_DAILY: IntegrationDatasetTemplate(
        dataset=DATASET_GA4_LANDING_DAILY,
        provider=INTEGRATION_PROVIDER_GA4,
        api_method="runReport",
        dimensions=("landingPage", "sessionSource", "sessionMedium", "date"),
        metrics=_GA4_SESSION_METRICS,
    ),
}


def pack_dimension_key(values: Sequence[str]) -> str:
    """Pack one row's dimension values into its ``dimension_key`` (C1).

    ``values`` MUST be in the dataset template's declared dimension order;
    multi-dimension rows join with ``" | "`` and a single-dimension row uses
    the bare value (``str.join`` of one element).
    """
    return DIMENSION_KEY_SEPARATOR.join(values)


class IntegrationSettings(BaseSettings):
    """Env-driven sync worker knobs (``INTEGRATION_`` env prefix).

    The OAuth client id/secret are deliberately NOT here: they are
    env-injected deployment secrets on the central ``Settings``, resolved
    only inside the OAuth exchange/refresh paths and never logged
    (invariant 6).
    """

    model_config = SettingsConfigDict(env_prefix="INTEGRATION_", extra="ignore")

    # --- Sync windows (GSC/GA4 data lags and is revised for ~2-3 days) -----
    sync_default_window_days: int = Field(default=3, gt=0)
    sync_backfill_max_days: int = Field(default=480, gt=0)
    # Dispatcher tick (default daily).
    sync_cadence_seconds: float = Field(default=86400.0, gt=0)
    # Recent trailing window re-synced (with a bumped resync_seq) to pick up
    # late provider revisions.
    sync_late_data_revision_days: int = Field(default=3, ge=0)

    # --- Provider paging + request budget ------------------------------------
    sync_page_size: int = Field(default=25000, gt=0)
    sync_request_timeout_seconds: float = Field(default=60.0, gt=0)
    sync_max_attempts: int = Field(default=4, gt=0)

    # --- Queue lease/heartbeat -------------------------------------------------
    lease_ttl_seconds: float = Field(default=120.0, gt=0)
    heartbeat_interval_seconds: float = Field(default=30.0, gt=0)

    # --- OAuth state nonce lifetime --------------------------------------------
    state_ttl_seconds: int = Field(default=600, gt=0)

    # --- Import payload cap ------------------------------------------------------
    # Payloads are inline JSONB this pass (S3 offload keyed by payload_hash is
    # roadmap); over-cap payloads are rejected rather than truncated.
    max_inline_payload_bytes: int = Field(default=1_000_000, gt=0)

    # --- Per-provider rate limits (requests/minute) ------------------------------
    gsc_requests_per_minute: int = Field(default=200, gt=0)
    ga4_requests_per_minute: int = Field(default=60, gt=0)
    bing_requests_per_minute: int = Field(default=30, gt=0)

    @model_validator(mode="after")
    def _check_operational_bounds(self) -> IntegrationSettings:
        # Fail at startup, not mid-run: a heartbeat slower than the lease TTL
        # guarantees lease expiry during healthy work (same guard as content).
        if self.heartbeat_interval_seconds >= self.lease_ttl_seconds:
            raise ValueError(
                "heartbeat_interval_seconds must be shorter than lease_ttl_seconds"
            )
        if self.sync_default_window_days > self.sync_backfill_max_days:
            raise ValueError(
                "sync_default_window_days must not exceed sync_backfill_max_days"
            )
        if self.sync_late_data_revision_days > self.sync_backfill_max_days:
            raise ValueError(
                "sync_late_data_revision_days must not exceed "
                "sync_backfill_max_days"
            )
        return self

    def requests_per_minute(self, provider: str) -> int:
        """Per-provider request budget; an unknown provider fails loud."""
        if provider not in INTEGRATION_PROVIDERS:
            raise ValueError(f"unknown integration provider: {provider!r}")
        return getattr(self, f"{provider}_requests_per_minute")


integration_settings = IntegrationSettings()


def _integration_sync_run_model() -> type[IntegrationSyncRun]:
    # Imported lazily so this config module never imports a model at import
    # time (would create a config <-> models circular import).
    from app.models.integrations import IntegrationSyncRun

    return IntegrationSyncRun


def _integration_claim_order(model: type[IntegrationSyncRun]) -> tuple:
    # Deterministic claim order mirroring ``CONTENT_QUEUE_SPEC`` exactly:
    # priority, then FIFO by availability, then the randomized position.
    return (
        model.priority.desc(),
        model.available_at.asc(),
        model.randomized_position.asc(),
    )


# Parameterizes the one generic ``PostgresTaskQueue`` over
# ``IntegrationSyncRun`` rows with the integrations lease TTL + claim order.
INTEGRATION_QUEUE_SPEC: Final[PostgresQueueSpec[IntegrationSyncRun]] = (
    PostgresQueueSpec(
        model_ref=_integration_sync_run_model,
        lease_ttl=lambda: integration_settings.lease_ttl_seconds,
        claim_order=_integration_claim_order,
        max_attempts_error=ERROR_MAX_ATTEMPTS,
    )
)
