# robots.txt parsing + politeness for the Site Health crawler (Task 3).
#
# Wraps ``protego`` (modern robots parser: wildcards, allow/disallow ordering,
# crawl-delay, sitemap directives) behind a small deterministic value type so
# the worker never touches the raw parser. A ``RobotsPolicy`` answers three
# questions for the frozen crawl user-agent:
#   - can_fetch(url): is this URL allowed?
#   - crawl_delay(): the per-host delay to honor (clamped to the config max,
#     falling back to the config default when robots specifies none).
#   - sitemaps(): the sitemap URLs robots declares (seed URLs for discovery).
#
# Fail-open vs fail-closed is explicit: an empty/failed robots fetch produces
# an ALLOW-ALL policy (fail-open — standard crawler behavior), while a policy
# built from a body that explicitly disallows still denies. The worker owns the
# fetch (through the SSRF-safe fetcher); this module only parses.
from __future__ import annotations

from protego import Protego

from app.core.config.site_health import site_health_settings


class RobotsPolicy:
    """A parsed robots policy for one host, evaluated for a fixed user-agent."""

    __slots__ = ("_parser", "_user_agent", "_allow_all")

    def __init__(
        self,
        parser: Protego | None,
        *,
        user_agent: str,
        allow_all: bool = False,
    ) -> None:
        self._parser = parser
        self._user_agent = user_agent
        self._allow_all = allow_all

    @classmethod
    def allow_all(cls, *, user_agent: str) -> RobotsPolicy:
        """A fail-open policy that permits every URL (no robots restrictions)."""
        return cls(None, user_agent=user_agent, allow_all=True)

    @classmethod
    def parse(cls, body: str | bytes, *, user_agent: str) -> RobotsPolicy:
        """Parse a robots.txt body. An empty body yields an allow-all policy."""
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        text = body or ""
        if not text.strip():
            return cls.allow_all(user_agent=user_agent)
        try:
            parser = Protego.parse(text)
        except Exception:
            # A malformed robots file must not crash discovery: fail open.
            return cls.allow_all(user_agent=user_agent)
        return cls(parser, user_agent=user_agent)

    def can_fetch(self, url: str) -> bool:
        if self._allow_all or self._parser is None:
            return True
        try:
            return bool(self._parser.can_fetch(url, self._user_agent))
        except Exception:
            return True

    def crawl_delay(self) -> float:
        """Per-host delay in seconds, clamped to the config max.

        Uses the robots-declared crawl-delay when present, else the config
        default. Never exceeds ``max_crawl_delay_seconds``.
        """
        settings = site_health_settings
        declared: float | None = None
        if not self._allow_all and self._parser is not None:
            try:
                value = self._parser.crawl_delay(self._user_agent)
                declared = float(value) if value is not None else None
            except Exception:
                declared = None
        delay = (
            declared if declared is not None else settings.default_crawl_delay_seconds
        )
        return max(0.0, min(delay, settings.max_crawl_delay_seconds))

    def sitemaps(self) -> list[str]:
        """The sitemap URLs robots declares (may be empty)."""
        if self._allow_all or self._parser is None:
            return []
        try:
            return list(self._parser.sitemaps or [])
        except Exception:
            return []
