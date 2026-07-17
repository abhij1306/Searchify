"""Merge the Site Health and direct OpenAI migration branches.

Revision ID: 0009_merge_site_health_openai
Revises: 0008_site_health, 0008_direct_openai_retirement
Create Date: 2026-07-17
"""

from __future__ import annotations

revision = "0009_merge_site_health_openai"
down_revision = ("0008_site_health", "0008_direct_openai_retirement")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the two independently applied migration branches."""


def downgrade() -> None:
    """Split the migration graph back to its two parent heads."""
