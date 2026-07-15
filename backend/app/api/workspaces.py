# Workspaces router stub — endpoints implemented in B2 (list/create).
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
