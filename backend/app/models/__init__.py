# ORM model registry.
#
# Import the shared declarative ``Base`` and re-export it so Alembic's
# ``migrations/env.py`` binds autogeneration to a single metadata object.
# Later backend tasks (B2+) add model modules here and import them below so
# their tables register on ``Base.metadata`` before autogenerate runs.
from __future__ import annotations

from app.core.database import Base

__all__ = ["Base"]
