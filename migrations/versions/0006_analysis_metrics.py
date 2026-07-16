"""B6 deterministic-analysis + metrics tables

Creates the analysis/metrics vertical slice (B6):
  - ``response_analyses`` (one deterministic per-execution analysis of a raw
    response; unique per ``task_id``; carries the flat headline signals + the
    full ``score`` dict + provenance triple + versions);
  - ``brand_mentions`` / ``competitor_mentions`` (recorded alias matches, one
    row per mention, each with raw-artifact provenance);
  - ``citations`` (classified source citations: owned/unintended/competitor/
    third-party);
  - ``metric_snapshots`` (one run-level aggregate projection per audit; unique
    per ``audit_id``; carries the headline Visibility Score + full ``metrics``).

Every derived row carries provenance (invariant 4): the ``artifact_id`` of the
``RawResponseArtifact`` it was computed from (SET NULL so pruning an artifact
keeps the analysis) + the ``analyzer_version`` (+ ``scoring_rule_version``
where a formula applies). Everything is workspace-scoped (invariant 5).

Hand-written (Alembic autogenerate is disabled in this repo) and verified with
``alembic check`` + ``alembic upgrade head``.

Revision ID: 0006_analysis_metrics
Revises: 0005_audit_queue
Create Date: 2026-07-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_analysis_metrics"
down_revision = "0005_audit_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "response_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "artifact_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("analyzer_version", sa.String(length=32), nullable=False),
        sa.Column(
            "scoring_rule_version", sa.String(length=32), nullable=False
        ),
        sa.Column("logical_engine", sa.String(length=32), nullable=False),
        sa.Column("transport_provider", sa.String(length=32), nullable=False),
        sa.Column("transport_model", sa.String(length=255), nullable=False),
        sa.Column("prompt_index", sa.Integer(), nullable=False),
        sa.Column("repetition", sa.Integer(), nullable=False),
        sa.Column("prompt_class", sa.String(length=32), nullable=False),
        sa.Column("brand_mentioned", sa.Boolean(), nullable=False),
        sa.Column("brand_first_offset", sa.Integer(), nullable=True),
        sa.Column("owned_domain_cited", sa.Boolean(), nullable=False),
        sa.Column("owned_citation_count", sa.Integer(), nullable=False),
        sa.Column("unintended_domain_cited", sa.Boolean(), nullable=False),
        sa.Column("citation_count", sa.Integer(), nullable=False),
        sa.Column("search_used", sa.Boolean(), nullable=False),
        sa.Column("search_query_count", sa.Integer(), nullable=False),
        sa.Column("sentiment", sa.String(length=16), nullable=True),
        sa.Column("avg_position", sa.Float(), nullable=True),
        sa.Column("score", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["audits.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["audit_tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["raw_response_artifacts.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_response_analysis_task"),
    )
    op.create_index(
        op.f("ix_response_analyses_workspace_id"),
        "response_analyses",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_response_analyses_audit_id"),
        "response_analyses",
        ["audit_id"],
    )
    op.create_index(
        op.f("ix_response_analyses_task_id"),
        "response_analyses",
        ["task_id"],
    )

    op.create_table(
        "brand_mentions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "analysis_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "artifact_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("analyzer_version", sa.String(length=32), nullable=False),
        sa.Column("brand_name", sa.String(length=255), nullable=False),
        sa.Column("first_offset", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["audits.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"], ["response_analyses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["raw_response_artifacts.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_brand_mentions_workspace_id"),
        "brand_mentions",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_brand_mentions_audit_id"), "brand_mentions", ["audit_id"]
    )
    op.create_index(
        op.f("ix_brand_mentions_analysis_id"),
        "brand_mentions",
        ["analysis_id"],
    )

    op.create_table(
        "competitor_mentions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "analysis_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "artifact_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("analyzer_version", sa.String(length=32), nullable=False),
        sa.Column("competitor_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["audits.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"], ["response_analyses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["raw_response_artifacts.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_competitor_mentions_workspace_id"),
        "competitor_mentions",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_competitor_mentions_audit_id"),
        "competitor_mentions",
        ["audit_id"],
    )
    op.create_index(
        op.f("ix_competitor_mentions_analysis_id"),
        "competitor_mentions",
        ["analysis_id"],
    )

    op.create_table(
        "citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "analysis_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "artifact_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("analyzer_version", sa.String(length=32), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("classification", sa.String(length=24), nullable=False),
        sa.Column("is_owned", sa.Boolean(), nullable=False),
        sa.Column("is_unintended", sa.Boolean(), nullable=False),
        sa.Column(
            "matched_competitor", sa.String(length=255), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["audits.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"], ["response_analyses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["raw_response_artifacts.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_citations_workspace_id"), "citations", ["workspace_id"]
    )
    op.create_index(op.f("ix_citations_audit_id"), "citations", ["audit_id"])
    op.create_index(
        op.f("ix_citations_analysis_id"), "citations", ["analysis_id"]
    )

    op.create_table(
        "metric_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("analyzer_version", sa.String(length=32), nullable=False),
        sa.Column(
            "scoring_rule_version", sa.String(length=32), nullable=False
        ),
        sa.Column("total_completed", sa.Integer(), nullable=False),
        sa.Column("total_failed", sa.Integer(), nullable=False),
        sa.Column("visibility_score", sa.Float(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["audits.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("audit_id", name="uq_metric_snapshot_audit"),
    )
    op.create_index(
        op.f("ix_metric_snapshots_workspace_id"),
        "metric_snapshots",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_metric_snapshots_audit_id"),
        "metric_snapshots",
        ["audit_id"],
    )
    op.create_index(
        op.f("ix_metric_snapshots_project_id"),
        "metric_snapshots",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_metric_snapshots_project_id"),
        table_name="metric_snapshots",
    )
    op.drop_index(
        op.f("ix_metric_snapshots_audit_id"), table_name="metric_snapshots"
    )
    op.drop_index(
        op.f("ix_metric_snapshots_workspace_id"),
        table_name="metric_snapshots",
    )
    op.drop_table("metric_snapshots")

    op.drop_index(op.f("ix_citations_analysis_id"), table_name="citations")
    op.drop_index(op.f("ix_citations_audit_id"), table_name="citations")
    op.drop_index(op.f("ix_citations_workspace_id"), table_name="citations")
    op.drop_table("citations")

    op.drop_index(
        op.f("ix_competitor_mentions_analysis_id"),
        table_name="competitor_mentions",
    )
    op.drop_index(
        op.f("ix_competitor_mentions_audit_id"),
        table_name="competitor_mentions",
    )
    op.drop_index(
        op.f("ix_competitor_mentions_workspace_id"),
        table_name="competitor_mentions",
    )
    op.drop_table("competitor_mentions")

    op.drop_index(
        op.f("ix_brand_mentions_analysis_id"), table_name="brand_mentions"
    )
    op.drop_index(
        op.f("ix_brand_mentions_audit_id"), table_name="brand_mentions"
    )
    op.drop_index(
        op.f("ix_brand_mentions_workspace_id"), table_name="brand_mentions"
    )
    op.drop_table("brand_mentions")

    op.drop_index(
        op.f("ix_response_analyses_task_id"), table_name="response_analyses"
    )
    op.drop_index(
        op.f("ix_response_analyses_audit_id"),
        table_name="response_analyses",
    )
    op.drop_index(
        op.f("ix_response_analyses_workspace_id"),
        table_name="response_analyses",
    )
    op.drop_table("response_analyses")
