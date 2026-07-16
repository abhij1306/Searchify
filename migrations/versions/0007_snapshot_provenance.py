"""B6 fix: MetricSnapshot source provenance (invariant 4)

Adds two nullable JSONB columns to ``metric_snapshots`` so every run-level
aggregate traces back to the exact evidence set it was computed from:
  - ``source_analysis_ids`` — the ``ResponseAnalysis`` ids aggregated;
  - ``source_artifact_ids`` — the ``RawResponseArtifact`` ids those analyses
    were derived from.

Invariant 4 requires every derived row (including this aggregate) to be
traceable to the raw evidence + analyzer/rule versions.

Hand-written (Alembic autogenerate is disabled in this repo) and verified with
``alembic check`` + ``alembic upgrade head``.

Revision ID: 0007_snapshot_provenance
Revises: 0006_analysis_metrics
Create Date: 2026-07-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_snapshot_provenance"
down_revision = "0006_analysis_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "metric_snapshots",
        sa.Column("source_analysis_ids", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "metric_snapshots",
        sa.Column("source_artifact_ids", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("metric_snapshots", "source_artifact_ids")
    op.drop_column("metric_snapshots", "source_analysis_ids")
