"""Initial schema bootstrap (squashed).

Revision ID: 0001_initial
Revises: None
Create Date: 2026-07-17

GREENFIELD POLICY: this project is pre-production, so the migration history
was squashed to this single bootstrap revision (previously 0001..0010). The
schema is created directly from ``Base.metadata`` — the same mechanism the
test suite uses — so this migration always matches the current ORM models.
Until there is a production database to preserve, schema changes are made by
editing the models and recreating the database (``alembic downgrade base``
then ``alembic upgrade head``, or drop/create the DB), NOT by adding new
revision files.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# env.py imports app.models before version files load, so `app` is importable
# here (alembic runs from backend/ with prepend_sys_path = .).
from app.models import Base

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    # metadata.drop_all cannot order the drop (audit/site-health artifact
    # tables form FK cycles), so drop each table with CASCADE instead.
    bind = op.get_bind()
    for table in Base.metadata.tables:
        bind.execute(sa.text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
