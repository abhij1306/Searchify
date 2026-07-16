"""B3 projects/brand + prompts tables

Creates the projects/prompts vertical slice (B3):
  - ``projects`` (workspace-scoped brand-visibility project);
  - normalized brand identity (B-1): ``brands``, ``brand_aliases``,
    ``competitors``, ``owned_domains``, ``unintended_domains``;
  - dedicated prompt resource (Q3=A): ``prompt_sets``, ``prompts``.

All PKs are UUIDs. Every table is workspace-scoped — directly (``projects``
-> ``workspaces``) or transitively through its project. Hand-written (Alembic
autogenerate is disabled in this repo) and verified with ``alembic check``.

Revision ID: 0003_projects_prompts
Revises: 0002_auth_workspace
Create Date: 2026-07-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_projects_prompts"
down_revision = "0002_auth_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("brand_name", sa.String(length=255), nullable=False),
        sa.Column("website_url", sa.String(length=1024), nullable=False),
        sa.Column("country_code", sa.String(length=8), nullable=False),
        sa.Column("language_code", sa.String(length=16), nullable=False),
        sa.Column("benchmark_mode", sa.String(length=32), nullable=False),
        sa.Column("default_repetitions", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_projects_workspace_id"), "projects", ["workspace_id"]
    )

    op.create_table(
        "brands",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", name="uq_brand_project"),
    )
    op.create_index(op.f("ix_brands_project_id"), "brands", ["project_id"])

    op.create_table(
        "brand_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["brands.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_brand_aliases_brand_id"), "brand_aliases", ["brand_id"]
    )

    op.create_table(
        "competitors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("aliases", postgresql.JSONB(), nullable=False),
        sa.Column("domains", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_competitors_project_id"), "competitors", ["project_id"]
    )

    op.create_table(
        "owned_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_owned_domains_project_id"), "owned_domains", ["project_id"]
    )

    op.create_table(
        "unintended_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_unintended_domains_project_id"),
        "unintended_domains",
        ["project_id"],
    )

    op.create_table(
        "prompt_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_prompt_sets_project_id"), "prompt_sets", ["project_id"]
    )

    op.create_table(
        "prompts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "prompt_set_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("theme", sa.String(length=255), nullable=False),
        sa.Column("intent", sa.String(length=32), nullable=False),
        sa.Column("branded", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("generation_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["prompt_set_id"], ["prompt_sets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_prompts_prompt_set_id"), "prompts", ["prompt_set_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_prompts_prompt_set_id"), table_name="prompts")
    op.drop_table("prompts")
    op.drop_index(
        op.f("ix_prompt_sets_project_id"), table_name="prompt_sets"
    )
    op.drop_table("prompt_sets")
    op.drop_index(
        op.f("ix_unintended_domains_project_id"),
        table_name="unintended_domains",
    )
    op.drop_table("unintended_domains")
    op.drop_index(
        op.f("ix_owned_domains_project_id"), table_name="owned_domains"
    )
    op.drop_table("owned_domains")
    op.drop_index(
        op.f("ix_competitors_project_id"), table_name="competitors"
    )
    op.drop_table("competitors")
    op.drop_index(
        op.f("ix_brand_aliases_brand_id"), table_name="brand_aliases"
    )
    op.drop_table("brand_aliases")
    op.drop_index(op.f("ix_brands_project_id"), table_name="brands")
    op.drop_table("brands")
    op.drop_index(op.f("ix_projects_workspace_id"), table_name="projects")
    op.drop_table("projects")
