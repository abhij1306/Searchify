# Audit request/response DTOs (string UUID ids; workspace-scoped, invariant 5).
#
# Mirrors the `POST /audits` contract in docs/backend-architecture.md §4. The
# request references a project + prompt source + logical engines; provider keys
# are NEVER carried here — the worker resolves the decrypted key from the
# workspace's ``ProviderConnection`` at execution time (invariant 6). Responses
# never expose secrets or the raw brand list.
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.config.projects import MAX_REPETITIONS, MIN_REPETITIONS

BenchmarkModeStr = str


class AuditCreate(BaseModel):
    """`POST /audits` body. The workspace is resolved from the session/header."""

    project_id: uuid.UUID
    # Prompt source: a whole set, or explicit prompt ids (at least one).
    prompt_set_id: uuid.UUID | None = None
    prompt_ids: list[uuid.UUID] = Field(default_factory=list)
    # Logical engines to measure (chatgpt|gemini|claude). Must have a workspace
    # provider route configured for each.
    engines: list[str] = Field(default_factory=list, min_length=1)
    repetitions: int | None = Field(
        default=None, ge=MIN_REPETITIONS, le=MAX_REPETITIONS
    )
    benchmark_mode: BenchmarkModeStr | None = None
    # Optional explicit 64-bit seed (decimal string). Generated + stored when
    # omitted so the slot shuffle is reproducible (invariant 9).
    random_seed: str | None = None


class AuditTaskResponse(BaseModel):
    """A single execution/queue row projection (never contains secrets)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    audit_id: uuid.UUID
    prompt_index: int
    repetition: int
    randomized_position: int
    logical_engine: str
    transport_provider: str
    transport_model: str
    status: str
    attempt_count: int
    max_attempts: int
    answer_text: str = ""
    search_used: bool = False
    error_code: str = ""
    error_detail: str = ""
    latency_ms: int | None = None
    created_at: datetime
    completed_at: datetime | None = None


class AuditEngineSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    logical_engine: str
    transport_provider: str
    transport_model: str


class AuditResponse(BaseModel):
    """Audit projection. Includes engine provenance but never the key."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    status: str
    benchmark_mode: str = ""
    repetitions: int
    random_seed: str = ""
    requested_count: int
    completed_count: int
    failed_count: int
    error_message: str = ""
    engine_snapshots: list[AuditEngineSnapshotResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    audit_id: uuid.UUID
    event_type: str
    message: str = ""
    payload: dict | None = None
    created_at: datetime
