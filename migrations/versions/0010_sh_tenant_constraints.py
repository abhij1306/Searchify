"""Site Health observation tenant-consistency constraints.

Hardens ``site_url_observations`` so an observation can never join a crawl and
a URL from different workspaces/projects, and its own ``workspace_id`` can
never drift away from its crawl/URL workspace (tenant-consistency guard;
observations are the source of truth for crawl admission).

This migration is additive on top of the already-published Site Health graph
(``0008_site_health``) and the merge head (``0009_merge_site_health_openai``);
the original ``0008`` migration is left untouched. It:

  - adds ``site_url_observations.project_id`` (nullable, then backfilled from
    the observation's crawl, then made ``NOT NULL``) plus its index;
  - adds parent unique constraints ``uq_site_crawls_id_project`` on
    ``site_crawls (id, project_id, workspace_id)`` and
    ``uq_site_urls_id_project`` on ``site_urls (id, project_id, workspace_id)``
    to back the composite foreign keys;
  - replaces the plain ``crawl_id`` / ``site_url_id`` foreign keys with scoped
    composite foreign keys ``(workspace_id, project_id, crawl_id)`` and
    ``(workspace_id, project_id, site_url_id)``.

Downgrade reverses all of the above, restoring the plain single-column foreign
keys and dropping the added column / constraints.

Revision ID: 0010_sh_tenant_constraints
Revises: 0009_merge_site_health_openai
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_sh_tenant_constraints"
down_revision = "0009_merge_site_health_openai"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add project_id + scoped composite FKs to site_url_observations."""
    # 1) Add project_id nullable so existing rows can be backfilled in place.
    op.add_column(
        "site_url_observations",
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )

    # 2) Backfill project_id from each observation's crawl (crawls always
    #    carry project_id and every observation references an existing crawl).
    op.execute(
        """
        UPDATE site_url_observations AS o
        SET project_id = c.project_id
        FROM site_crawls AS c
        WHERE o.crawl_id = c.id
        """
    )

    # 3) Enforce NOT NULL now that all existing rows carry a value.
    op.alter_column(
        "site_url_observations",
        "project_id",
        nullable=False,
    )

    # 4) Index the new column (matches the ORM ``index=True``).
    op.create_index(
        op.f("ix_site_url_observations_project_id"),
        "site_url_observations",
        ["project_id"],
    )

    # 5) Parent unique constraints that back the composite foreign keys.
    op.create_unique_constraint(
        "uq_site_crawls_id_project",
        "site_crawls",
        ["id", "project_id", "workspace_id"],
    )
    op.create_unique_constraint(
        "uq_site_urls_id_project",
        "site_urls",
        ["id", "project_id", "workspace_id"],
    )

    # 6) Drop the old plain single-column foreign keys.
    op.drop_constraint(
        "site_url_observations_crawl_id_fkey",
        "site_url_observations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "site_url_observations_site_url_id_fkey",
        "site_url_observations",
        type_="foreignkey",
    )

    # 7) Add the scoped composite foreign keys.
    op.create_foreign_key(
        "fk_site_url_observation_crawl_scoped",
        "site_url_observations",
        "site_crawls",
        ["workspace_id", "project_id", "crawl_id"],
        ["workspace_id", "project_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_site_url_observation_site_url_scoped",
        "site_url_observations",
        "site_urls",
        ["workspace_id", "project_id", "site_url_id"],
        ["workspace_id", "project_id", "id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Restore plain single-column FKs and drop the added column/constraints."""
    # Reverse of step 7: drop scoped composite foreign keys.
    op.drop_constraint(
        "fk_site_url_observation_site_url_scoped",
        "site_url_observations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_site_url_observation_crawl_scoped",
        "site_url_observations",
        type_="foreignkey",
    )

    # Reverse of step 6: restore the plain single-column foreign keys under
    # their original auto-generated names.
    op.create_foreign_key(
        "site_url_observations_crawl_id_fkey",
        "site_url_observations",
        "site_crawls",
        ["crawl_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "site_url_observations_site_url_id_fkey",
        "site_url_observations",
        "site_urls",
        ["site_url_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Reverse of step 5: drop the parent unique constraints.
    op.drop_constraint(
        "uq_site_urls_id_project", "site_urls", type_="unique"
    )
    op.drop_constraint(
        "uq_site_crawls_id_project", "site_crawls", type_="unique"
    )

    # Reverse of step 4: drop the project_id index.
    op.drop_index(
        op.f("ix_site_url_observations_project_id"),
        table_name="site_url_observations",
    )

    # Reverse of steps 1-3: drop the project_id column.
    op.drop_column("site_url_observations", "project_id")
