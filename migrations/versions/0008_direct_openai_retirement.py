"""v2 direct-provider retirement: retire active OpenRouter, add retirement fields

Retires OpenRouter as an ACTIVE transport in favour of direct OpenAI while
keeping every historical row readable (invariant 10). It is additive and fully
reversible:

Schema
  - ``provider_connections.deactivation_reason`` (varchar(64), NOT NULL, "")
  - ``provider_routes.active`` (boolean, NOT NULL, default true)
  - ``provider_routes.deactivation_reason`` (varchar(64), NOT NULL, "")

Data (marked with ``openrouter_retired_v2`` so downgrade only touches rows this
migration changed)
  - active ``openrouter`` connections -> active=false, reason=marker.
    Connections already inactive before v2 are LEFT UNCHANGED (empty reason).
  - all ``openrouter`` routes -> active=false, reason=marker.

Nothing else is touched: provider connection tests, discovery history, audit
snapshots, tasks, attempts, artifacts, analyses, metric snapshots, and
provenance triples are immutable and preserved verbatim.

Hand-written (Alembic autogenerate is disabled in this repo) and verified with
``alembic check`` + ``alembic upgrade head`` and a seeded downgrade/re-upgrade
integration test.

Revision ID: 0008_direct_openai_retirement
Revises: 0007_snapshot_provenance
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_direct_openai_retirement"
down_revision = "0007_snapshot_provenance"
branch_labels = None
depends_on = None

# Marker written into deactivation_reason for ONLY the rows this migration
# retires, so a downgrade can restore exactly those rows and nothing else.
_RETIREMENT_MARKER = "openrouter_retired_v2"
_LEGACY_TRANSPORT = "openrouter"


def upgrade() -> None:
    # 1. Additive schema: three NOT NULL columns with safe server defaults so
    #    existing rows backfill without a rewrite and the old ORM ignores them.
    op.add_column(
        "provider_connections",
        sa.Column(
            "deactivation_reason",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "provider_routes",
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "provider_routes",
        sa.Column(
            "deactivation_reason",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
    )

    bind = op.get_bind()

    # 2. Retire only currently-active OpenRouter connections; leave rows that
    #    were already inactive before v2 untouched (empty reason).
    bind.execute(
        sa.text(
            "UPDATE provider_connections "
            "SET active = false, deactivation_reason = :marker "
            "WHERE transport_provider = :legacy AND active = true"
        ),
        {"marker": _RETIREMENT_MARKER, "legacy": _LEGACY_TRANSPORT},
    )

    # 3. Retire ALL OpenRouter routes (any that were still active).
    bind.execute(
        sa.text(
            "UPDATE provider_routes "
            "SET active = false, deactivation_reason = :marker "
            "WHERE transport_provider = :legacy AND active = true"
        ),
        {"marker": _RETIREMENT_MARKER, "legacy": _LEGACY_TRANSPORT},
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Reactivate ONLY the rows this migration retired (identified by marker);
    # connections/routes inactive for other reasons stay inactive. Direct
    # OpenAI rows created after upgrade have no marker and are untouched.
    bind.execute(
        sa.text(
            "UPDATE provider_connections "
            "SET active = true, deactivation_reason = '' "
            "WHERE deactivation_reason = :marker"
        ),
        {"marker": _RETIREMENT_MARKER},
    )
    bind.execute(
        sa.text(
            "UPDATE provider_routes "
            "SET active = true, deactivation_reason = '' "
            "WHERE deactivation_reason = :marker"
        ),
        {"marker": _RETIREMENT_MARKER},
    )

    op.drop_column("provider_routes", "deactivation_reason")
    op.drop_column("provider_routes", "active")
    op.drop_column("provider_connections", "deactivation_reason")
