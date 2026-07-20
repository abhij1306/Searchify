# Shared HTTP error helpers for the API layer.
#
# One place to raise the repeated 404s so the detail strings stay consistent.
# The produced detail is exactly ``"{resource} not found"`` — callers pass the
# resource label ("Audit", "Workspace", "Project", "Prompt set", "Crawl") and
# get the byte-identical message the API has always returned.
from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException, status


def raise_not_found(resource: str, *, cause: BaseException | None = None) -> NoReturn:
    """Raise a 404 ``HTTPException`` whose detail is ``"{resource} not found"``.

    ``cause`` preserves explicit exception chaining (``raise ... from exc``) for
    the handlers that translate a domain "not found" into the HTTP response.
    """
    exc = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"{resource} not found"
    )
    if cause is not None:
        raise exc from cause
    raise exc
