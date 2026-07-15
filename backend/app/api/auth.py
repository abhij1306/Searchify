# Auth router stub — endpoints implemented in B2 (register/login/logout/me).
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])
