"""B5 Postgres-queue audit execution tables

Creates the audit-execution vertical slice (B5):
  - ``audits`` (workspace-scoped benchmark run over a project; stored 64-bit
    ``random_seed`` as text; frozen ``configuration``; lifecycle ``status``);
  - ``audit_prompt_snapshots`` (immutable frozen prompt copies, invariant 3);
  - ``audit_engine_snapshots`` (frozen route + connection, invariants 3 + 10);
  - ``audit_tasks`` (queue+lease row + per-execution row; ``FOR UPDATE SKIP
    LOCKED`` claim; unique ``idempotency_key`` + unique
    ``(audit_id, prompt_index, repetition, logical_engine)`` slot);
  - ``raw_response_artifacts`` (immutable raw provider payload, invariant 3);
  - ``provider_attempts`` (append-only per-attempt log, invariants 3 + 10);
  - ``audit_events`` (append-only lifecycle log, the SSE source).

All PKs are UUIDs; every table is workspace-scoped through ``audits`` (or
directly). Hand-written (Alembic autogenerate is disabled in this repo) and
verified with ``alembic check`` + ``alembic upgrade head``.

Revision ID: 0005_audit_queue
Revises: 0004_provider_settings
Create Date: 2026-07-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_audit_queue"
down_revision = "0004_provider_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("benchmark_mode", sa.String(length=32), nullable=False),
        sa.Column("system_instruction", sa.Text(), nullable=False),
        sa.Column("repetitions", sa.Integer(), nullable=False),
        sa.Column("random_seed", sa.String(length=32), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=True),
        sa.Column("summary", postgresql.JSONB(), nullable=True),
        sa.Column("analyzer_version", sa.String(length=32), nullable=False),
        sa.Column("requested_count", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_audits_workspace_id"), "audits", ["workspace_id"]
    )
    op.create_index(op.f("ix_audits_project_id"), "audits", ["project_id"])
    op.create_index(op.f("ix_audits_status"), "audits", ["status"])

    op.create_table(
        "audit_prompt_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prompt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("prompt_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("theme", sa.String(length=255), nullable=False),
        sa.Column("intent", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["audits.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id"], ["prompts.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "audit_id", "prompt_index", name="uq_audit_prompt_snapshot_index"
        ),
    )
    op.create_index(
        op.f("ix_audit_prompt_snapshots_audit_id"),
        "audit_prompt_snapshots",
        ["audit_id"],
    )

    op.create_table(
        "audit_engine_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("logical_engine", sa.String(length=32), nullable=False),
        sa.Column("transport_provider", sa.String(length=32), nullable=False),
        sa.Column("transport_model", sa.String(length=255), nullable=False),
        sa.Column(
            "connection_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["audits.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["provider_connections.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "audit_id",
            "logical_engine",
            name="uq_audit_engine_snapshot_engine",
        ),
    )
    op.create_index(
        op.f("ix_audit_engine_snapshots_audit_id"),
        "audit_engine_snapshots",
        ["audit_id"],
    )

    # ``audit_tasks`` and ``raw_response_artifacts`` reference each other
    # (task.result_artifact_id <-> artifact.task_id). Create ``audit_tasks``
    # first without the artifact FK, then the artifact table, then add the
    # deferred FK, to break the cycle.
    op.create_table(
        "audit_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "prompt_snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "engine_snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("prompt_index", sa.Integer(), nullable=False),
        sa.Column("repetition", sa.Integer(), nullable=False),
        sa.Column("randomized_position", sa.Integer(), nullable=False),
        sa.Column("logical_engine", sa.String(length=32), nullable=False),
        sa.Column("transport_provider", sa.String(length=32), nullable=False),
        sa.Column("transport_model", sa.String(length=255), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column(
            "provider_route_snapshot", postgresql.JSONB(), nullable=True
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column(
            "lease_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "result_artifact_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("search_used", sa.Boolean(), nullable=False),
        sa.Column("search_events", postgresql.JSONB(), nullable=True),
        sa.Column("citations", postgresql.JSONB(), nullable=True),
        sa.Column("score", postgresql.JSONB(), nullable=True),
        sa.Column("request_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("provider_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=32), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["audits.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["prompt_snapshot_id"],
            ["audit_prompt_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["engine_snapshot_id"],
            ["audit_engine_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_audit_task_idempotency_key"
        ),
        sa.UniqueConstraint(
            "audit_id",
            "prompt_index",
            "repetition",
            "logical_engine",
            name="uq_audit_task_slot",
        ),
    )
    op.create_index(
        op.f("ix_audit_tasks_audit_id"), "audit_tasks", ["audit_id"]
    )
    op.create_index(
        op.f("ix_audit_tasks_workspace_id"), "audit_tasks", ["workspace_id"]
    )
    op.create_index(
        op.f("ix_audit_tasks_status"), "audit_tasks", ["status"]
    )
    op.create_index(
        op.f("ix_audit_tasks_available_at"), "audit_tasks", ["available_at"]
    )

    op.create_table(
        "raw_response_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("logical_engine", sa.String(length=32), nullable=False),
        sa.Column("transport_provider", sa.String(length=32), nullable=False),
        sa.Column("transport_model", sa.String(length=255), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("search_used", sa.Boolean(), nullable=False),
        sa.Column("search_events", postgresql.JSONB(), nullable=True),
        sa.Column("citations", postgresql.JSONB(), nullable=True),
        sa.Column("provider_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["audits.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["audit_tasks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_raw_response_artifacts_audit_id"),
        "raw_response_artifacts",
        ["audit_id"],
    )
    op.create_index(
        op.f("ix_raw_response_artifacts_task_id"),
        "raw_response_artifacts",
        ["task_id"],
    )
    # Deferred FK: close the cycle now that both tables exist.
    op.create_foreign_key(
        "fk_audit_tasks_result_artifact_id",
        "audit_tasks",
        "raw_response_artifacts",
        ["result_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "provider_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("logical_engine", sa.String(length=32), nullable=False),
        sa.Column("transport_provider", sa.String(length=32), nullable=False),
        sa.Column("transport_model", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "artifact_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["audit_tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["audits.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["raw_response_artifacts.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_provider_attempts_task_id"),
        "provider_attempts",
        ["task_id"],
    )
    op.create_index(
        op.f("ix_provider_attempts_audit_id"),
        "provider_attempts",
        ["audit_id"],
    )

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["audits.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_audit_events_audit_id"), "audit_events", ["audit_id"]
    )
    op.create_index(
        op.f("ix_audit_events_created_at"), "audit_events", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_audit_events_created_at"), table_name="audit_events"
    )
    op.drop_index(
        op.f("ix_audit_events_audit_id"), table_name="audit_events"
    )
    op.drop_table("audit_events")

    op.drop_index(
        op.f("ix_provider_attempts_audit_id"),
        table_name="provider_attempts",
    )
    op.drop_index(
        op.f("ix_provider_attempts_task_id"),
        table_name="provider_attempts",
    )
    op.drop_table("provider_attempts")

    # Break the task<->artifact cycle before dropping either table.
    op.drop_constraint(
        "fk_audit_tasks_result_artifact_id",
        "audit_tasks",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_raw_response_artifacts_task_id"),
        table_name="raw_response_artifacts",
    )
    op.drop_index(
        op.f("ix_raw_response_artifacts_audit_id"),
        table_name="raw_response_artifacts",
    )
    op.drop_table("raw_response_artifacts")

    op.drop_index(
        op.f("ix_audit_tasks_available_at"), table_name="audit_tasks"
    )
    op.drop_index(op.f("ix_audit_tasks_status"), table_name="audit_tasks")
    op.drop_index(
        op.f("ix_audit_tasks_workspace_id"), table_name="audit_tasks"
    )
    op.drop_index(op.f("ix_audit_tasks_audit_id"), table_name="audit_tasks")
    op.drop_table("audit_tasks")

    op.drop_index(
        op.f("ix_audit_engine_snapshots_audit_id"),
        table_name="audit_engine_snapshots",
    )
    op.drop_table("audit_engine_snapshots")

    op.drop_index(
        op.f("ix_audit_prompt_snapshots_audit_id"),
        table_name="audit_prompt_snapshots",
    )
    op.drop_table("audit_prompt_snapshots")

    op.drop_index(op.f("ix_audits_status"), table_name="audits")
    op.drop_index(op.f("ix_audits_project_id"), table_name="audits")
    op.drop_index(op.f("ix_audits_workspace_id"), table_name="audits")
    op.drop_table("audits")
