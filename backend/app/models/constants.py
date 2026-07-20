# Shared literal constants for ORM model definitions.
#
# Deduplicates the SQLAlchemy ``ForeignKey`` target and ondelete/cascade strings
# repeated across the model modules. Every value is byte-identical to the inline
# literal it replaces — this is a readability refactor with NO schema or runtime
# behavior change.
from __future__ import annotations

# ForeignKey target for the audits table (repeated across analysis/audit models).
FK_AUDITS_ID = "audits.id"

# ``ondelete`` policy: null out the child FK when the parent row is removed.
ON_DELETE_SET_NULL = "SET NULL"

# ``relationship(cascade=...)`` policy: cascade all ops and delete orphaned rows.
CASCADE_ALL_DELETE_ORPHAN = "all, delete-orphan"
