"""Task 1 Site Health persistence graph

Creates the Site Health (HTTP-level technical/AEO crawler) persistence graph:
  - ``workspace_site_health_entitlements`` (workspace capability + quota lock);
  - ``site_health_profiles`` (project crawl root/scope + selection version);
  - ``site_crawls`` (one run; independent overall/discovery/analysis states);
  - ``site_urls`` (stable per-project URL identity; keyset inventory index);
  - ``site_crawl_tasks`` (queue+lease row; ``FOR UPDATE SKIP LOCKED`` claim;
    integer ``generation``; unique ``idempotency_key`` + unique
    ``(crawl_id, task_kind, url_hash, generation)`` slot);
  - ``site_fetch_artifacts`` (immutable delivery facts; unique ``task_id``;
    no raw body);
  - ``site_url_observations`` (immutable per-crawl discovery provenance);
  - ``monitored_site_urls`` (persistent selection; active workspace-count idx);
  - ``site_fetch_attempts`` (append-only per-attempt diagnostics);
  - ``site_page_analyses`` (artifact-derived scores; unique ``artifact_id``);
  - ``site_link_references`` (deduplicated links/assets);
  - ``site_rule_evaluations`` (per-rule outcome; unique ``(analysis_id,
    rule_id)``);
  - ``site_issues`` (failure projection; unique ``evaluation_id``; filter
    indexes);
  - ``site_health_snapshots`` (crawl aggregate; unique ``crawl_id``);
  - ``site_crawl_events`` (append-only safe lifecycle events).

``site_crawl_tasks`` and ``site_fetch_artifacts`` reference each other
(``site_crawl_tasks.result_artifact_id`` <-> ``site_fetch_artifacts.task_id``).
As in 0005, ``site_crawl_tasks`` is created first without the artifact FK, then
``site_fetch_artifacts``, then the deferred FK is added to break the cycle.

All PKs are UUIDs; every table is workspace-scoped (directly on query-heavy
rows, or through the parent project/crawl). Hand-written (Alembic autogenerate
is disabled in this repo) and verified with ``alembic check`` +
``alembic upgrade head``/``downgrade``.

Revision ID: 0008_site_health
Revises: 0007_snapshot_provenance
Create Date: 2026-07-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_site_health"
down_revision = "0007_snapshot_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- workspace_site_health_entitlements ------------------------------
    op.create_table(
        "workspace_site_health_entitlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("plan_key", sa.String(length=32), nullable=False),
        sa.Column("capability_revision", sa.Integer(), nullable=False),
        sa.Column("discovery_mode", sa.String(length=16), nullable=False),
        sa.Column("discovery_url_cap", sa.Integer(), nullable=True),
        sa.Column("sample_url_limit", sa.Integer(), nullable=False),
        sa.Column("monitored_url_limit", sa.Integer(), nullable=False),
        sa.Column("count_disclosure", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", name="uq_ws_site_health_entitlement_workspace"
        ),
    )
    op.create_index(
        op.f("ix_workspace_site_health_entitlements_workspace_id"),
        "workspace_site_health_entitlements",
        ["workspace_id"],
    )

    # --- site_health_profiles --------------------------------------------
    op.create_table(
        "site_health_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("root_url", sa.String(length=2048), nullable=False),
        sa.Column("root_host", sa.String(length=255), nullable=False),
        sa.Column(
            "registrable_domain", sa.String(length=255), nullable=False
        ),
        sa.Column("include_globs", postgresql.JSONB(), nullable=True),
        sa.Column("exclude_globs", postgresql.JSONB(), nullable=True),
        sa.Column("selection_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", name="uq_site_health_profile_project"
        ),
    )
    op.create_index(
        op.f("ix_site_health_profiles_workspace_id"),
        "site_health_profiles",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_site_health_profiles_project_id"),
        "site_health_profiles",
        ["project_id"],
    )

    # --- site_crawls -----------------------------------------------------
    op.create_table(
        "site_crawls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "profile_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("discovery_status", sa.String(length=24), nullable=False),
        sa.Column("analysis_status", sa.String(length=24), nullable=False),
        sa.Column("root_url", sa.String(length=2048), nullable=False),
        sa.Column("random_seed", sa.String(length=32), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=True),
        sa.Column("sample_mode", sa.Boolean(), nullable=False),
        sa.Column("admitted_url_count", sa.Integer(), nullable=False),
        sa.Column("discovered_url_count", sa.Integer(), nullable=False),
        sa.Column("analyzed_url_count", sa.Integer(), nullable=False),
        sa.Column("failed_url_count", sa.Integer(), nullable=False),
        sa.Column("inventory_complete", sa.Boolean(), nullable=False),
        sa.Column("score_summary", postgresql.JSONB(), nullable=True),
        sa.Column("extractor_version", sa.String(length=32), nullable=False),
        sa.Column("analyzer_version", sa.String(length=32), nullable=False),
        sa.Column(
            "rule_catalog_version", sa.String(length=32), nullable=False
        ),
        sa.Column("scoring_version", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["profile_id"], ["site_health_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_site_crawls_workspace_id"), "site_crawls", ["workspace_id"]
    )
    op.create_index(
        op.f("ix_site_crawls_project_id"), "site_crawls", ["project_id"]
    )
    op.create_index(
        op.f("ix_site_crawls_profile_id"), "site_crawls", ["profile_id"]
    )
    op.create_index(
        op.f("ix_site_crawls_status"), "site_crawls", ["status"]
    )

    # --- site_urls -------------------------------------------------------
    op.create_table(
        "site_urls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("normalized_url", sa.String(length=2048), nullable=False),
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column("display_url", sa.String(length=2048), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("discovery_status", sa.String(length=24), nullable=False),
        sa.Column("latest_source_kind", sa.String(length=16), nullable=False),
        sa.Column("latest_title", sa.String(length=1024), nullable=False),
        sa.Column(
            "latest_content_type", sa.String(length=128), nullable=False
        ),
        sa.Column(
            "first_seen_crawl_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "last_seen_crawl_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["first_seen_crawl_id"], ["site_crawls.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_crawl_id"], ["site_crawls.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "url_hash", name="uq_site_url_project_hash"
        ),
    )
    op.create_index(
        op.f("ix_site_urls_workspace_id"), "site_urls", ["workspace_id"]
    )
    op.create_index(
        op.f("ix_site_urls_project_id"), "site_urls", ["project_id"]
    )
    op.create_index(
        "ix_site_urls_project_keyset",
        "site_urls",
        ["project_id", "normalized_url", "id"],
    )

    # --- site_crawl_tasks (queue+lease; artifact FK deferred) ------------
    op.create_table(
        "site_crawl_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crawl_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "site_url_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("task_kind", sa.String(length=16), nullable=False),
        sa.Column("requested_url", sa.String(length=2048), nullable=False),
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "parent_site_url_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "source_task_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("randomized_position", sa.Integer(), nullable=False),
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
        sa.Column("error_code", sa.String(length=32), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["crawl_id"], ["site_crawls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["site_url_id"], ["site_urls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_site_url_id"], ["site_urls.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_task_id"], ["site_crawl_tasks.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_site_crawl_task_idempotency_key"
        ),
        sa.UniqueConstraint(
            "crawl_id",
            "task_kind",
            "url_hash",
            "generation",
            name="uq_site_crawl_task_slot",
        ),
    )
    op.create_index(
        op.f("ix_site_crawl_tasks_crawl_id"),
        "site_crawl_tasks",
        ["crawl_id"],
    )
    op.create_index(
        op.f("ix_site_crawl_tasks_workspace_id"),
        "site_crawl_tasks",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_site_crawl_tasks_site_url_id"),
        "site_crawl_tasks",
        ["site_url_id"],
    )
    op.create_index(
        op.f("ix_site_crawl_tasks_status"), "site_crawl_tasks", ["status"]
    )
    op.create_index(
        op.f("ix_site_crawl_tasks_available_at"),
        "site_crawl_tasks",
        ["available_at"],
    )
    op.create_index(
        "ix_site_crawl_tasks_claim",
        "site_crawl_tasks",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_site_crawl_tasks_lease",
        "site_crawl_tasks",
        ["status", "lease_expires_at"],
    )

    # --- site_fetch_artifacts (immutable evidence; no raw body) ----------
    op.create_table(
        "site_fetch_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crawl_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("fetch_purpose", sa.String(length=16), nullable=False),
        sa.Column("requested_url", sa.String(length=2048), nullable=False),
        sa.Column("final_url", sa.String(length=2048), nullable=False),
        sa.Column("redirect_chain", postgresql.JSONB(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("redacted_headers", postgresql.JSONB(), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("http_version", sa.String(length=16), nullable=False),
        sa.Column("ttfb_ms", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("wire_bytes", sa.Integer(), nullable=True),
        sa.Column("decoded_bytes", sa.Integer(), nullable=True),
        sa.Column("extractor_version", sa.String(length=32), nullable=False),
        sa.Column("normalized_facts", postgresql.JSONB(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["site_crawl_tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["crawl_id"], ["site_crawls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_site_fetch_artifact_task"),
    )
    op.create_index(
        op.f("ix_site_fetch_artifacts_task_id"),
        "site_fetch_artifacts",
        ["task_id"],
    )
    op.create_index(
        op.f("ix_site_fetch_artifacts_crawl_id"),
        "site_fetch_artifacts",
        ["crawl_id"],
    )
    op.create_index(
        op.f("ix_site_fetch_artifacts_workspace_id"),
        "site_fetch_artifacts",
        ["workspace_id"],
    )
    # Deferred FK: close the task<->artifact cycle now that both tables exist.
    op.create_foreign_key(
        "fk_site_crawl_tasks_result_artifact_id",
        "site_crawl_tasks",
        "site_fetch_artifacts",
        ["result_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- site_url_observations (immutable discovery provenance) ----------
    op.create_table(
        "site_url_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("crawl_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "site_url_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column(
            "parent_site_url_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "source_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("observed_url", sa.String(length=2048), nullable=False),
        sa.Column("final_url", sa.String(length=2048), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["crawl_id"], ["site_crawls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["site_url_id"], ["site_urls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_site_url_id"], ["site_urls.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"],
            ["site_fetch_artifacts.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "crawl_id", "site_url_id", name="uq_site_url_observation"
        ),
    )
    op.create_index(
        op.f("ix_site_url_observations_workspace_id"),
        "site_url_observations",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_site_url_observations_crawl_id"),
        "site_url_observations",
        ["crawl_id"],
    )
    op.create_index(
        op.f("ix_site_url_observations_site_url_id"),
        "site_url_observations",
        ["site_url_id"],
    )

    # --- monitored_site_urls (persistent selection projection) -----------
    op.create_table(
        "monitored_site_urls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "profile_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "site_url_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("selection_source", sa.String(length=16), nullable=False),
        sa.Column("selecting_membership_id", sa.Integer(), nullable=True),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "deselected_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["site_health_profiles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["site_url_id"], ["site_urls.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "site_url_id", name="uq_monitored_site_url"
        ),
    )
    op.create_index(
        op.f("ix_monitored_site_urls_workspace_id"),
        "monitored_site_urls",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_monitored_site_urls_project_id"),
        "monitored_site_urls",
        ["project_id"],
    )
    op.create_index(
        op.f("ix_monitored_site_urls_profile_id"),
        "monitored_site_urls",
        ["profile_id"],
    )
    op.create_index(
        op.f("ix_monitored_site_urls_site_url_id"),
        "monitored_site_urls",
        ["site_url_id"],
    )
    op.create_index(
        "ix_monitored_site_urls_ws_active",
        "monitored_site_urls",
        ["workspace_id", "active"],
    )

    # --- site_fetch_attempts (append-only diagnostics) -------------------
    op.create_table(
        "site_fetch_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crawl_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("target_host", sa.String(length=255), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("wire_bytes", sa.Integer(), nullable=True),
        sa.Column("decoded_bytes", sa.Integer(), nullable=True),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["site_crawl_tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["crawl_id"], ["site_crawls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["site_fetch_artifacts.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_site_fetch_attempts_task_id"),
        "site_fetch_attempts",
        ["task_id"],
    )
    op.create_index(
        op.f("ix_site_fetch_attempts_crawl_id"),
        "site_fetch_attempts",
        ["crawl_id"],
    )
    op.create_index(
        op.f("ix_site_fetch_attempts_workspace_id"),
        "site_fetch_attempts",
        ["workspace_id"],
    )

    # --- site_page_analyses (artifact-derived scores) --------------------
    op.create_table(
        "site_page_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("crawl_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "site_url_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "artifact_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("technical_score", sa.Float(), nullable=True),
        sa.Column("aeo_score", sa.Float(), nullable=True),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("analyzer_version", sa.String(length=32), nullable=False),
        sa.Column("scoring_version", sa.String(length=32), nullable=False),
        sa.Column(
            "source_evaluation_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
        sa.Column(
            "source_artifact_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["crawl_id"], ["site_crawls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["site_url_id"], ["site_urls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"], ["site_fetch_artifacts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "artifact_id", name="uq_site_page_analysis_artifact"
        ),
    )
    op.create_index(
        op.f("ix_site_page_analyses_workspace_id"),
        "site_page_analyses",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_site_page_analyses_project_id"),
        "site_page_analyses",
        ["project_id"],
    )
    op.create_index(
        op.f("ix_site_page_analyses_crawl_id"),
        "site_page_analyses",
        ["crawl_id"],
    )
    op.create_index(
        op.f("ix_site_page_analyses_site_url_id"),
        "site_page_analyses",
        ["site_url_id"],
    )
    op.create_index(
        op.f("ix_site_page_analyses_artifact_id"),
        "site_page_analyses",
        ["artifact_id"],
    )

    # --- site_link_references (deduplicated links/assets) ----------------
    op.create_table(
        "site_link_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "source_analysis_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "source_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("target_url", sa.String(length=2048), nullable=False),
        sa.Column("target_hash", sa.String(length=64), nullable=False),
        sa.Column("is_internal", sa.Boolean(), nullable=False),
        sa.Column("rel", sa.String(length=128), nullable=False),
        sa.Column("anchor_text", sa.String(length=1024), nullable=False),
        sa.Column(
            "evidence_fingerprint", sa.String(length=64), nullable=False
        ),
        sa.Column(
            "target_task_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "target_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("analyzer_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_analysis_id"],
            ["site_page_analyses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"],
            ["site_fetch_artifacts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_task_id"], ["site_crawl_tasks.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["target_artifact_id"],
            ["site_fetch_artifacts.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_artifact_id",
            "kind",
            "target_hash",
            "evidence_fingerprint",
            name="uq_site_link_reference_dedupe",
        ),
    )
    op.create_index(
        op.f("ix_site_link_references_workspace_id"),
        "site_link_references",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_site_link_references_source_analysis_id"),
        "site_link_references",
        ["source_analysis_id"],
    )
    op.create_index(
        op.f("ix_site_link_references_source_artifact_id"),
        "site_link_references",
        ["source_artifact_id"],
    )

    # --- site_rule_evaluations (per-rule outcome) ------------------------
    op.create_table(
        "site_rule_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "analysis_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "source_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("dimension", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column(
            "supporting_artifact_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
        sa.Column("extractor_version", sa.String(length=32), nullable=False),
        sa.Column("analyzer_version", sa.String(length=32), nullable=False),
        sa.Column("rule_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"], ["site_page_analyses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"],
            ["site_fetch_artifacts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_id", "rule_id", name="uq_site_rule_evaluation"
        ),
    )
    op.create_index(
        op.f("ix_site_rule_evaluations_workspace_id"),
        "site_rule_evaluations",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_site_rule_evaluations_analysis_id"),
        "site_rule_evaluations",
        ["analysis_id"],
    )
    op.create_index(
        op.f("ix_site_rule_evaluations_source_artifact_id"),
        "site_rule_evaluations",
        ["source_artifact_id"],
    )

    # --- site_issues (failure projection) --------------------------------
    op.create_table(
        "site_issues",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("crawl_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "site_url_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "analysis_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "evaluation_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "source_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("dimension", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("remediation", sa.Text(), nullable=False),
        sa.Column("analyzer_version", sa.String(length=32), nullable=False),
        sa.Column("rule_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["crawl_id"], ["site_crawls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["site_url_id"], ["site_urls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"], ["site_page_analyses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["site_rule_evaluations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"],
            ["site_fetch_artifacts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evaluation_id", name="uq_site_issue_evaluation"
        ),
    )
    op.create_index(
        op.f("ix_site_issues_workspace_id"), "site_issues", ["workspace_id"]
    )
    op.create_index(
        op.f("ix_site_issues_project_id"), "site_issues", ["project_id"]
    )
    op.create_index(
        op.f("ix_site_issues_crawl_id"), "site_issues", ["crawl_id"]
    )
    op.create_index(
        op.f("ix_site_issues_site_url_id"), "site_issues", ["site_url_id"]
    )
    op.create_index(
        op.f("ix_site_issues_analysis_id"), "site_issues", ["analysis_id"]
    )
    op.create_index(
        op.f("ix_site_issues_evaluation_id"),
        "site_issues",
        ["evaluation_id"],
    )
    op.create_index(
        "ix_site_issues_filter",
        "site_issues",
        ["crawl_id", "severity", "category", "rule_id"],
    )
    op.create_index(
        "ix_site_issues_url_created",
        "site_issues",
        ["site_url_id", "created_at"],
    )

    # --- site_health_snapshots (crawl aggregate) -------------------------
    op.create_table(
        "site_health_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("crawl_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selected_url_count", sa.Integer(), nullable=False),
        sa.Column("analyzed_url_count", sa.Integer(), nullable=False),
        sa.Column("technical_score", sa.Float(), nullable=True),
        sa.Column("aeo_score", sa.Float(), nullable=True),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("issue_count", sa.Integer(), nullable=False),
        sa.Column("severity_counts", postgresql.JSONB(), nullable=True),
        sa.Column("category_counts", postgresql.JSONB(), nullable=True),
        sa.Column(
            "source_analysis_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
        sa.Column(
            "source_artifact_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
        sa.Column(
            "source_evaluation_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
        sa.Column("analyzer_version", sa.String(length=32), nullable=False),
        sa.Column("scoring_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["crawl_id"], ["site_crawls.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "crawl_id", name="uq_site_health_snapshot_crawl"
        ),
    )
    op.create_index(
        op.f("ix_site_health_snapshots_workspace_id"),
        "site_health_snapshots",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_site_health_snapshots_project_id"),
        "site_health_snapshots",
        ["project_id"],
    )
    op.create_index(
        op.f("ix_site_health_snapshots_crawl_id"),
        "site_health_snapshots",
        ["crawl_id"],
    )

    # --- site_crawl_events (append-only safe events) ---------------------
    op.create_table(
        "site_crawl_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crawl_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["crawl_id"], ["site_crawls.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_site_crawl_events_crawl_id"),
        "site_crawl_events",
        ["crawl_id"],
    )
    op.create_index(
        op.f("ix_site_crawl_events_created_at"),
        "site_crawl_events",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_site_crawl_events_created_at"),
        table_name="site_crawl_events",
    )
    op.drop_index(
        op.f("ix_site_crawl_events_crawl_id"), table_name="site_crawl_events"
    )
    op.drop_table("site_crawl_events")

    op.drop_index(
        op.f("ix_site_health_snapshots_crawl_id"),
        table_name="site_health_snapshots",
    )
    op.drop_index(
        op.f("ix_site_health_snapshots_project_id"),
        table_name="site_health_snapshots",
    )
    op.drop_index(
        op.f("ix_site_health_snapshots_workspace_id"),
        table_name="site_health_snapshots",
    )
    op.drop_table("site_health_snapshots")

    op.drop_index(
        "ix_site_issues_url_created", table_name="site_issues"
    )
    op.drop_index("ix_site_issues_filter", table_name="site_issues")
    op.drop_index(
        op.f("ix_site_issues_evaluation_id"), table_name="site_issues"
    )
    op.drop_index(
        op.f("ix_site_issues_analysis_id"), table_name="site_issues"
    )
    op.drop_index(
        op.f("ix_site_issues_site_url_id"), table_name="site_issues"
    )
    op.drop_index(op.f("ix_site_issues_crawl_id"), table_name="site_issues")
    op.drop_index(
        op.f("ix_site_issues_project_id"), table_name="site_issues"
    )
    op.drop_index(
        op.f("ix_site_issues_workspace_id"), table_name="site_issues"
    )
    op.drop_table("site_issues")

    op.drop_index(
        op.f("ix_site_rule_evaluations_source_artifact_id"),
        table_name="site_rule_evaluations",
    )
    op.drop_index(
        op.f("ix_site_rule_evaluations_analysis_id"),
        table_name="site_rule_evaluations",
    )
    op.drop_index(
        op.f("ix_site_rule_evaluations_workspace_id"),
        table_name="site_rule_evaluations",
    )
    op.drop_table("site_rule_evaluations")

    op.drop_index(
        op.f("ix_site_link_references_source_artifact_id"),
        table_name="site_link_references",
    )
    op.drop_index(
        op.f("ix_site_link_references_source_analysis_id"),
        table_name="site_link_references",
    )
    op.drop_index(
        op.f("ix_site_link_references_workspace_id"),
        table_name="site_link_references",
    )
    op.drop_table("site_link_references")

    op.drop_index(
        op.f("ix_site_page_analyses_artifact_id"),
        table_name="site_page_analyses",
    )
    op.drop_index(
        op.f("ix_site_page_analyses_site_url_id"),
        table_name="site_page_analyses",
    )
    op.drop_index(
        op.f("ix_site_page_analyses_crawl_id"),
        table_name="site_page_analyses",
    )
    op.drop_index(
        op.f("ix_site_page_analyses_project_id"),
        table_name="site_page_analyses",
    )
    op.drop_index(
        op.f("ix_site_page_analyses_workspace_id"),
        table_name="site_page_analyses",
    )
    op.drop_table("site_page_analyses")

    op.drop_index(
        op.f("ix_site_fetch_attempts_workspace_id"),
        table_name="site_fetch_attempts",
    )
    op.drop_index(
        op.f("ix_site_fetch_attempts_crawl_id"),
        table_name="site_fetch_attempts",
    )
    op.drop_index(
        op.f("ix_site_fetch_attempts_task_id"),
        table_name="site_fetch_attempts",
    )
    op.drop_table("site_fetch_attempts")

    op.drop_index(
        "ix_monitored_site_urls_ws_active",
        table_name="monitored_site_urls",
    )
    op.drop_index(
        op.f("ix_monitored_site_urls_site_url_id"),
        table_name="monitored_site_urls",
    )
    op.drop_index(
        op.f("ix_monitored_site_urls_profile_id"),
        table_name="monitored_site_urls",
    )
    op.drop_index(
        op.f("ix_monitored_site_urls_project_id"),
        table_name="monitored_site_urls",
    )
    op.drop_index(
        op.f("ix_monitored_site_urls_workspace_id"),
        table_name="monitored_site_urls",
    )
    op.drop_table("monitored_site_urls")

    op.drop_index(
        op.f("ix_site_url_observations_site_url_id"),
        table_name="site_url_observations",
    )
    op.drop_index(
        op.f("ix_site_url_observations_crawl_id"),
        table_name="site_url_observations",
    )
    op.drop_index(
        op.f("ix_site_url_observations_workspace_id"),
        table_name="site_url_observations",
    )
    op.drop_table("site_url_observations")

    # Break the task<->artifact cycle before dropping either table.
    op.drop_constraint(
        "fk_site_crawl_tasks_result_artifact_id",
        "site_crawl_tasks",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_site_fetch_artifacts_workspace_id"),
        table_name="site_fetch_artifacts",
    )
    op.drop_index(
        op.f("ix_site_fetch_artifacts_crawl_id"),
        table_name="site_fetch_artifacts",
    )
    op.drop_index(
        op.f("ix_site_fetch_artifacts_task_id"),
        table_name="site_fetch_artifacts",
    )
    op.drop_table("site_fetch_artifacts")

    op.drop_index(
        "ix_site_crawl_tasks_lease", table_name="site_crawl_tasks"
    )
    op.drop_index(
        "ix_site_crawl_tasks_claim", table_name="site_crawl_tasks"
    )
    op.drop_index(
        op.f("ix_site_crawl_tasks_available_at"),
        table_name="site_crawl_tasks",
    )
    op.drop_index(
        op.f("ix_site_crawl_tasks_status"), table_name="site_crawl_tasks"
    )
    op.drop_index(
        op.f("ix_site_crawl_tasks_site_url_id"),
        table_name="site_crawl_tasks",
    )
    op.drop_index(
        op.f("ix_site_crawl_tasks_workspace_id"),
        table_name="site_crawl_tasks",
    )
    op.drop_index(
        op.f("ix_site_crawl_tasks_crawl_id"), table_name="site_crawl_tasks"
    )
    op.drop_table("site_crawl_tasks")

    op.drop_index(
        "ix_site_urls_project_keyset", table_name="site_urls"
    )
    op.drop_index(op.f("ix_site_urls_project_id"), table_name="site_urls")
    op.drop_index(op.f("ix_site_urls_workspace_id"), table_name="site_urls")
    op.drop_table("site_urls")

    op.drop_index(op.f("ix_site_crawls_status"), table_name="site_crawls")
    op.drop_index(
        op.f("ix_site_crawls_profile_id"), table_name="site_crawls"
    )
    op.drop_index(
        op.f("ix_site_crawls_project_id"), table_name="site_crawls"
    )
    op.drop_index(
        op.f("ix_site_crawls_workspace_id"), table_name="site_crawls"
    )
    op.drop_table("site_crawls")

    op.drop_index(
        op.f("ix_site_health_profiles_project_id"),
        table_name="site_health_profiles",
    )
    op.drop_index(
        op.f("ix_site_health_profiles_workspace_id"),
        table_name="site_health_profiles",
    )
    op.drop_table("site_health_profiles")

    op.drop_index(
        op.f("ix_workspace_site_health_entitlements_workspace_id"),
        table_name="workspace_site_health_entitlements",
    )
    op.drop_table("workspace_site_health_entitlements")
