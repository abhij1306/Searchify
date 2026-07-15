"""initial empty baseline

Establishes the migration baseline on an empty database. Models arrive in
B2+; each subsequent revision chains from this one.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-15
"""
from __future__ import annotations

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
