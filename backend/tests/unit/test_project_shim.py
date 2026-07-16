"""Unit test for the brand-identity serialization shim (B-1, B3).

Decision B-1 stores brand identity as normalized rows; the deterministic scorer
(ported in B5/B6) consumes a plain dict via ``ScoringConfig.from_project``. This
test builds normalized rows, runs them through the shim, and asserts the dict
has exactly the shape ``from_project`` expects — including the contract that the
shim does NOT prepend ``brand_name`` into ``brand_aliases`` (the scorer does
that itself).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.projects.shim import project_scoring_identity
from app.models.brand import (
    Brand,
    BrandAlias,
    Competitor,
    OwnedDomain,
    UnintendedDomain,
)
from app.models.project import Project


# A faithful re-implementation of the ``ScoringConfig.from_project``
# contract (``ai_visibility/scoring.py``). If the shim's dict shape drifts
# from what the scorer reads, this consumer breaks — which is the point.
@dataclass(frozen=True)
class _CompetitorConfig:
    name: str
    aliases: tuple[str, ...]
    domains: tuple[str, ...]


@dataclass(frozen=True)
class _ScoringConfig:
    brand_name: str
    brand_aliases: tuple[str, ...]
    owned_domains: tuple[str, ...]
    unintended_domains: tuple[str, ...]
    country_code: str = ""
    language_code: str = ""
    benchmark_mode: str = ""
    competitors: tuple[_CompetitorConfig, ...] = field(default_factory=tuple)

    @classmethod
    def from_project(cls, config: dict[str, Any]) -> _ScoringConfig:
        brand_name = str(config.get("brand_name") or "")
        aliases = [brand_name, *(config.get("brand_aliases") or [])]
        return cls(
            brand_name=brand_name,
            brand_aliases=tuple(a for a in aliases if a),
            owned_domains=tuple(config.get("owned_domains") or []),
            unintended_domains=tuple(config.get("unintended_domains") or []),
            country_code=str(config.get("country_code") or ""),
            language_code=str(config.get("language_code") or ""),
            benchmark_mode=str(config.get("benchmark_mode") or ""),
            competitors=tuple(
                _CompetitorConfig(
                    name=str(item.get("name") or ""),
                    aliases=tuple(
                        str(a)
                        for a in ([item.get("name"), *(item.get("aliases") or [])])
                        if a
                    ),
                    domains=tuple(
                        str(d) for d in (item.get("domains") or []) if d
                    ),
                )
                for item in (config.get("competitors") or [])
            ),
        )


def _sample_project() -> Project:
    project = Project(
        name="Acme",
        brand_name="Acme Corp",
        country_code="AU",
        language_code="en-AU",
        benchmark_mode="controlled_localized",
    )
    brand = Brand(name="Acme Corp")
    brand.aliases = [BrandAlias(alias="Acme"), BrandAlias(alias="ACME Inc")]
    project.brand = brand
    project.owned_domains = [OwnedDomain(domain="acme.com")]
    project.unintended_domains = [UnintendedDomain(domain="support.acme.com")]
    project.competitors = [
        Competitor(
            name="Globex",
            aliases=["Globex Co"],
            domains=["globex.com"],
        )
    ]
    return project


def test_shim_produces_expected_dict_shape() -> None:
    identity = project_scoring_identity(_sample_project())
    assert identity == {
        "brand_name": "Acme Corp",
        "brand_aliases": ["Acme", "ACME Inc"],
        "owned_domains": ["acme.com"],
        "unintended_domains": ["support.acme.com"],
        "competitors": [
            {
                "name": "Globex",
                "aliases": ["Globex Co"],
                "domains": ["globex.com"],
            }
        ],
        "country_code": "AU",
        "language_code": "en-AU",
        "benchmark_mode": "controlled_localized",
    }
    # The shim must NOT duplicate brand_name into the alias list.
    assert "Acme Corp" not in identity["brand_aliases"]


def test_shim_feeds_scoring_config_unchanged() -> None:
    identity = project_scoring_identity(_sample_project())
    config = _ScoringConfig.from_project(identity)

    # from_project prepends brand_name; the shim's aliases feed in cleanly.
    assert config.brand_name == "Acme Corp"
    assert config.brand_aliases == ("Acme Corp", "Acme", "ACME Inc")
    assert config.owned_domains == ("acme.com",)
    assert config.unintended_domains == ("support.acme.com",)
    assert config.country_code == "AU"
    assert config.benchmark_mode == "controlled_localized"
    assert len(config.competitors) == 1
    comp = config.competitors[0]
    assert comp.name == "Globex"
    assert comp.aliases == ("Globex", "Globex Co")
    assert comp.domains == ("globex.com",)


def test_shim_handles_missing_brand() -> None:
    project = Project(name="Bare", brand_name="Fallback Brand")
    project.owned_domains = []
    project.unintended_domains = []
    project.competitors = []
    identity = project_scoring_identity(project)
    assert identity["brand_name"] == "Fallback Brand"
    assert identity["brand_aliases"] == []
    assert identity["competitors"] == []
