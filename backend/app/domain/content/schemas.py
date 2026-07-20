# Content-generation request/response DTOs (workspace-scoped, invariant 5).
#
# Wire contract for `/content/generations`. The list item is bounded (no
# ``output_text``); the detail is the full record. Neither ever carries the
# provider API key or a raw request body containing it (invariant 6) — the
# only provider fields exposed are provenance (requested/returned model,
# finish reason, usage, latency).
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.config.content import (
    CONTENT_DEFAULT_OUTPUT_TYPE,
    CONTENT_HISTORY_TITLE_MAX_LEN,
    CONTENT_OUTPUT_TYPES,
    CONTENT_PROMPT_MAX_LEN,
)


class ContentGenerationCreate(BaseModel):
    """`POST /content/generations` body (workspace resolved from session)."""

    project_id: uuid.UUID
    prompt: str
    output_type: str = CONTENT_DEFAULT_OUTPUT_TYPE
    website_context_enabled: bool = True

    @field_validator("prompt")
    @classmethod
    def _prompt_trimmed_bounded(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("prompt must not be empty")
        if len(trimmed) > CONTENT_PROMPT_MAX_LEN:
            raise ValueError(f"prompt exceeds {CONTENT_PROMPT_MAX_LEN} characters")
        return trimmed

    @field_validator("output_type")
    @classmethod
    def _output_type_known(cls, value: str) -> str:
        if value not in CONTENT_OUTPUT_TYPES:
            raise ValueError(f"unknown output_type: {value}")
        return value


def prompt_preview(prompt: str) -> str:
    """Deterministic history label: first line, trimmed to the config cap."""
    first_line = prompt.strip().splitlines()[0] if prompt.strip() else ""
    return first_line[:CONTENT_HISTORY_TITLE_MAX_LEN]


class ContentGenerationListItem(BaseModel):
    """Bounded history-list projection (never ``output_text``)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    output_type: str
    website_context_status: str
    requested_model: str
    returned_model: str | None = None
    provider: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    error_code: str = ""
    prompt_preview: str = ""


class WebsiteContextSummary(BaseModel):
    """Provenance for the frozen Website-context snapshot (which crawl,
    how fresh, which sources). Never page bodies, never the key."""

    crawl_id: str
    crawl_completed_at: str | None = None
    extractor_version: str = ""
    analyzer_version: str = ""
    page_count: int = 0
    char_count: int = 0
    site_url_ids: list[str] = []
    artifact_ids: list[str] = []
    content_hashes: list[str] = []


class ContentGenerationDetail(BaseModel):
    """Full projection of one generation (never the API key)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    output_type: str
    website_context_status: str
    requested_model: str
    returned_model: str | None = None
    provider: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    error_code: str = ""
    prompt_preview: str = ""
    prompt: str
    website_context_enabled: bool
    website_context_summary: WebsiteContextSummary | None = None
    finish_reason: str | None = None
    output_truncated: bool = False
    output_text: str | None = None
    usage: dict | None = None
    latency_ms: int | None = None
    error_detail: str = ""
    generator_version: str = ""
