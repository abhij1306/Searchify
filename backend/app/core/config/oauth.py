# OAuth provider catalog + settings for third-party sign-in (invariant 1:
# config lives in core/config, never inline in service/router code).
#
# Owns the approved OAuth provider set (google | github | apple), the
# per-provider authorize/token endpoint defaults and scopes, and the
# env-driven client credentials + enablement flags. Routers READ these
# values; they never hard-code them. Client secrets are never logged or
# returned to callers (invariant 6).
from __future__ import annotations

from typing import Final

from pydantic_settings import BaseSettings, SettingsConfigDict

# --- OAuth providers (third-party sign-in surface) ------------------------
OAUTH_GOOGLE: Final = "google"
OAUTH_GITHUB: Final = "github"
OAUTH_APPLE: Final = "apple"
OAUTH_PROVIDERS: Final[frozenset[str]] = frozenset(
    {OAUTH_GOOGLE, OAUTH_GITHUB, OAUTH_APPLE}
)

# Human-facing labels for UI buttons / provider listings, in catalog order.
OAUTH_PROVIDER_LABELS: Final[dict[str, str]] = {
    OAUTH_GOOGLE: "Google",
    OAUTH_GITHUB: "GitHub",
    OAUTH_APPLE: "Apple",
}

# --- Per-provider endpoint + scope defaults -------------------------------
# Token URLs are cataloged now so the callback token exchange (lands when
# real credentials exist) reads them from here instead of hard-coding them.
OAUTH_AUTHORIZE_URLS: Final[dict[str, str]] = {
    OAUTH_GOOGLE: "https://accounts.google.com/o/oauth2/v2/auth",
    OAUTH_GITHUB: "https://github.com/login/oauth/authorize",
    OAUTH_APPLE: "https://appleid.apple.com/auth/authorize",
}
OAUTH_TOKEN_URLS: Final[dict[str, str]] = {
    OAUTH_GOOGLE: "https://oauth2.googleapis.com/token",
    OAUTH_GITHUB: "https://github.com/login/oauth/access_token",
    OAUTH_APPLE: "https://appleid.apple.com/auth/token",
}
OAUTH_SCOPES: Final[dict[str, str]] = {
    OAUTH_GOOGLE: "openid email profile",
    OAUTH_GITHUB: "read:user user:email",
    OAUTH_APPLE: "name email",
}


def is_oauth_provider(provider: str) -> bool:
    """True when ``provider`` names a cataloged OAuth provider."""
    return provider in OAUTH_PROVIDERS


class OAuthSettings(BaseSettings):
    """OAuth client credentials + enablement flags (env-overridable).

    Every provider ships disabled with empty credentials: a provider is only
    usable when explicitly enabled AND fully configured. Values are read from
    the process environment (``OAUTH_`` prefix); they are never logged or
    returned to clients (invariant 6).
    """

    model_config = SettingsConfigDict(env_prefix="OAUTH_", extra="ignore")

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    google_enabled: bool = False

    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = ""
    github_enabled: bool = False

    apple_client_id: str = ""
    apple_client_secret: str = ""
    apple_redirect_uri: str = ""
    apple_enabled: bool = False

    # Lifetime of the signed, stateless OAuth state/nonce token.
    state_ttl_seconds: int = 600


oauth_settings = OAuthSettings()


def oauth_provider_configured(provider: str) -> bool:
    """True only when the provider is enabled AND fully credentialed.

    Requires the enablement flag plus a non-empty client id, client secret,
    and redirect URI. Never logs the underlying values (invariant 6).
    """
    if not is_oauth_provider(provider):
        return False
    return bool(
        getattr(oauth_settings, f"{provider}_enabled")
        and getattr(oauth_settings, f"{provider}_client_id")
        and getattr(oauth_settings, f"{provider}_client_secret")
        and getattr(oauth_settings, f"{provider}_redirect_uri")
    )
