# Structured logging, correlation ids, and optional Logfire instrumentation.
from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from contextvars import ContextVar, Token
from functools import lru_cache
from typing import Any, cast
from uuid import uuid4

structlog: Any | None = None
try:
    import structlog as _structlog

    structlog = _structlog
except ImportError:  # pragma: no cover - optional dependency fallback
    pass

__all__ = [
    "configure_logging",
    "generate_correlation_id",
    "get_correlation_id",
    "instrument_fastapi",
    "reset_correlation_id",
    "set_correlation_id",
]

_correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def _add_correlation_id(
    logger: object,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    del logger, method_name
    correlation_id = get_correlation_id()
    if correlation_id:
        event_dict["correlation_id"] = correlation_id
    return event_dict


@lru_cache(maxsize=1)
def configure_logging() -> None:
    """Configure JSON structured logging with correlation-id enrichment."""
    if structlog is None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            stream=sys.stdout,
        )
        return

    shared_processors = cast(
        "list[Any]",
        [
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            _add_correlation_id,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
        ],
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    if not any(
        isinstance(existing, logging.StreamHandler)
        and getattr(existing, "stream", None) is sys.stdout
        and isinstance(
            getattr(existing, "formatter", None),
            structlog.stdlib.ProcessorFormatter,
        )
        for existing in root_logger.handlers
    ):
        root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            _add_correlation_id,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def instrument_fastapi(app: Any) -> None:
    """Attach Logfire FastAPI instrumentation when enabled + available.

    Logfire is optional: absent token / disabled flag / missing package all
    degrade to a no-op so local dev and tests never require it.
    """
    from app.core.config import settings

    if not settings.logfire_enabled or not settings.logfire_token:
        return
    try:
        import logfire  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - optional dependency fallback
        logging.getLogger("app.core.telemetry").debug(
            "logfire not installed; skipping instrumentation"
        )
        return
    logfire.configure(
        token=settings.logfire_token,
        service_name=settings.logfire_service_name,
        environment=settings.logfire_environment or settings.app_env,
        send_to_logfire="if-token-present",
    )
    logfire.instrument_fastapi(app)


def generate_correlation_id() -> str:
    return uuid4().hex[:16]


def get_correlation_id() -> str | None:
    return _correlation_id_ctx.get()


def set_correlation_id(correlation_id: str | None) -> Token[str | None]:
    return _correlation_id_ctx.set(correlation_id)


def reset_correlation_id(token: Token[str | None]) -> None:
    _correlation_id_ctx.reset(token)
