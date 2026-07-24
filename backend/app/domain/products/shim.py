# Serialization shim: catalog rows -> the plain product-scorer config dict.
#
# Mirrors ``domain/projects/shim.py project_scoring_identity``: the catalog is
# stored as normalized rows (``Product`` / ``CompetitorProduct``), but the
# deterministic product scorer consumes a plain dict via
# ``ProductScoringConfig.from_project``. The planner freezes this dict into
# every audit's ``configuration`` at creation (next to ``scoring_identity``)
# so re-scoring is deterministic — later catalog edits never alter an
# in-flight or completed audit (invariant 9).
#
# The dict shape:
#     {
#       "products": [
#           {"id", "sku", "name", "aliases", "variants", "price", "currency",
#            "url"},
#           ...
#       ],
#       "competitor_products": [
#           {"id", "competitor_id", "competitor_name", "name", "aliases",
#            "price", "currency"},
#           ...
#       ],
#     }
# Ids are strings; prices are floats (or None).
from __future__ import annotations

from typing import Any

from app.models.project import Project


def _price(value: Any) -> float | None:
    return float(value) if value is not None else None


def project_product_identity(project: Project) -> dict[str, Any]:
    """Rebuild the plain catalog dict the product scorer expects from rows.

    Requires the project's ``products`` and ``competitor_products`` (+ their
    ``competitor``) relationships to be loaded.
    """
    return {
        "products": [
            {
                "id": str(product.id),
                "sku": product.sku or "",
                "name": product.name or "",
                "aliases": list(product.aliases or []),
                "variants": [
                    {
                        "name": str(variant.get("name") or ""),
                        "sku": str(variant.get("sku") or ""),
                        "price": _price(variant.get("price")),
                    }
                    for variant in (product.variants or [])
                    if isinstance(variant, dict)
                ],
                "price": _price(product.price),
                "currency": product.currency or "",
                "url": product.url or "",
            }
            for product in project.products
        ],
        "competitor_products": [
            {
                "id": str(competitor_product.id),
                "competitor_id": str(competitor_product.competitor_id),
                "competitor_name": (
                    competitor_product.competitor.name
                    if competitor_product.competitor is not None
                    else ""
                ),
                "name": competitor_product.name or "",
                "aliases": list(competitor_product.aliases or []),
                "price": _price(competitor_product.price),
                "currency": competitor_product.currency or "",
            }
            for competitor_product in project.competitor_products
        ],
    }
