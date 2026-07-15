# Projects router stub — endpoints implemented in B3/B6
# (CRUD + /projects/{id}/visibility).
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/projects", tags=["projects"])
