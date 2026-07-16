"""B4 BYOK provider settings tables

Creates the provider-settings vertical slice (B4):
  - ``provider_connections`` (workspace-scoped BYOK credential; Fernet-encrypted
    secret column that is never serialized into a DTO);
  - ``provider_routes`` (logical_engine + transport_provider + transport_model
    + is_default — logical vs transport identity, invariant 10);
  - ``provider_connection_tests`` (append-only connectivity-test history);
  - ``discovery_model_configs`` (plumbing-only per decision B-4).

All PKs are UUIDs; every table is workspace-scoped. Hand-written (Alembic
autogenerate is disabled in this repo) and verified with ``alembic check``.

Revision ID: 0004_provider_settings
Revises: 0003_projects_prompts
Create Date: 2026-07-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_provider_settings"
down_revision = "0003_projects_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("transport_provider", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_provider_connections_workspace_id"),
        "provider_connections",
        ["workspace_id"],
    )

    op.create_table(
        "provider_routes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "connection_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("logical_engine", sa.String(length=32), nullable=False),
        sa.Column("transport_provider", sa.String(length=32), nullable=False),
        sa.Column("transport_model", sa.String(length=255), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["provider_connections.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_provider_routes_workspace_id"),
        "provider_routes",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_provider_routes_connection_id"),
        "provider_routes",
        ["connection_id"],
    )

    op.create_table(
        "provider_connection_tests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "connection_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.String(length=1024), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("logical_engine", sa.String(length=32), nullable=False),
        sa.Column("transport_provider", sa.String(length=32), nullable=False),
        sa.Column("transport_model", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["provider_connections.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_provider_connection_tests_workspace_id"),
        "provider_connection_tests",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_provider_connection_tests_connection_id"),
        "provider_connection_tests",
        ["connection_id"],
    )

    op.create_table(
        "discovery_model_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "connection_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("logical_engine", sa.String(length=32), nullable=False),
        sa.Column("transport_provider", sa.String(length=32), nullable=False),
        sa.Column("transport_model", sa.String(length=255), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["provider_connections.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_discovery_model_configs_workspace_id"),
        "discovery_model_configs",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_discovery_model_configs_connection_id"),
        "discovery_model_configs",
        ["connection_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_discovery_model_configs_connection_id"),
        table_name="discovery_model_configs",
    )
    op.drop_index(
        op.f("ix_discovery_model_configs_workspace_id"),
        table_name="discovery_model_configs",
    )
    op.drop_table("discovery_model_configs")
    op.drop_index(
        op.f("ix_provider_connection_tests_connection_id"),
        table_name="provider_connection_tests",
    )
    op.drop_index(
        op.f("ix_provider_connection_tests_workspace_id"),
        table_name="provider_connection_tests",
    )
    op.drop_table("provider_connection_tests")
    op.drop_index(
        op.f("ix_provider_routes_connection_id"),
        table_name="provider_routes",
    )
    op.drop_index(
        op.f("ix_provider_routes_workspace_id"),
        table_name="provider_routes",
    )
    op.drop_table("provider_routes")
    op.drop_index(
        op.f("ix_provider_connections_workspace_id"),
        table_name="provider_connections",
    )
    op.drop_table("provider_connections")
