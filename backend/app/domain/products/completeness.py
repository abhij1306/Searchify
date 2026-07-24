# Per-SKU data-quality completeness (deterministic, computed ON READ).
#
# The required-attribute matrix lives in ``app/core/config/products.py``
# (invariant 1): the top-level fields every SKU should populate plus the keys
# expected inside the ``attributes`` JSONB bag. Completeness is a pure
# function of the row — always in sync, never persisted, no provider call.
from __future__ import annotations

from typing import Any

from app.core.config.products import (
    PRODUCT_COMPLETENESS_ATTRIBUTE_KEYS,
    PRODUCT_REQUIRED_ATTRIBUTES,
)


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def product_completeness(product: Any) -> dict[str, Any]:
    """Score one product against the config required-attribute matrix.

    Works on any object with the Product fields (ORM row or test stub).
    Returns ``{"score", "present", "total", "missing"}`` where ``score`` is
    the present fraction (0..1) and ``missing`` lists the absent
    fields/attribute keys in matrix order (drives the badge tooltip).
    """
    attributes = getattr(product, "attributes", None) or {}
    missing = [
        field
        for field in PRODUCT_REQUIRED_ATTRIBUTES
        if not _present(getattr(product, field, None))
    ]
    missing.extend(
        key
        for key in PRODUCT_COMPLETENESS_ATTRIBUTE_KEYS
        if not _present(attributes.get(key))
    )
    total = len(PRODUCT_REQUIRED_ATTRIBUTES) + len(PRODUCT_COMPLETENESS_ATTRIBUTE_KEYS)
    present = total - len(missing)
    return {
        "score": round(present / total, 4) if total else 1.0,
        "present": present,
        "total": total,
        "missing": missing,
    }
