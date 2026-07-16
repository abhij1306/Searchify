# Serialization shim: normalized brand rows -> the plain scorer config dict.
#
# Decision B-1 stores brand identity as normalized rows (``Brand`` /
# ``BrandAlias`` / ``Competitor`` / ``OwnedDomain`` / ``UnintendedDomain``),
# but the deterministic scorer (ported in B5/B6 from crawlerai
# ``ai_visibility/scoring.py``) consumes a **plain dict** via
# ``ScoringConfig.from_project(config)``. Rather than rewrite the scorer to
# understand ORM rows, this shim rebuilds exactly the dict shape the scorer
# expects, so downstream scoring works unchanged.
#
# The dict shape (from the reference ``_scoring_configuration``):
#     {
#       "brand_name": str,
#       "brand_aliases": [str, ...],          # NOT including brand_name itself
#       "owned_domains": [str, ...],
#       "unintended_domains": [str, ...],
#       "competitors": [{"name","aliases","domains"}, ...],
#       "country_code": str,
#       "language_code": str,
#       "benchmark_mode": str,
#     }
# ``ScoringConfig.from_project`` prepends ``brand_name`` onto ``brand_aliases``
# itself, so this shim must NOT duplicate it into the alias list.
from __future__ import annotations

from typing import Any

from app.models.project import Project


def project_scoring_identity(project: Project) -> dict[str, Any]:
    """Rebuild the plain brand-identity dict the scorer expects from rows.

    Requires the project's ``brand`` (+ its ``aliases``), ``competitors``,
    ``owned_domains``, and ``unintended_domains`` relationships to be loaded.
    """
    brand = project.brand
    brand_name = brand.name if brand is not None else (project.brand_name or "")
    brand_aliases = (
        [alias.alias for alias in brand.aliases] if brand is not None else []
    )
    return {
        "brand_name": brand_name,
        "brand_aliases": [a for a in brand_aliases if a],
        "owned_domains": [d.domain for d in project.owned_domains if d.domain],
        "unintended_domains": [
            d.domain for d in project.unintended_domains if d.domain
        ],
        "competitors": [
            {
                "name": competitor.name,
                "aliases": list(competitor.aliases or []),
                "domains": list(competitor.domains or []),
            }
            for competitor in project.competitors
        ],
        "country_code": project.country_code or "",
        "language_code": project.language_code or "",
        "benchmark_mode": project.benchmark_mode or "",
    }
