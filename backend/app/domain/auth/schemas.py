# Auth request/response schemas (all ids string UUID; secrets never returned).
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class Credentials(BaseModel):
    """Register / login payload."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class SessionUser(BaseModel):
    """Public user projection. The password hash is never exposed."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AuthResponse(BaseModel):
    """Response for register/login/me — the authenticated user only.

    The JWT is delivered out-of-band in the HttpOnly session cookie, never
    in the response body.
    """

    user: SessionUser
