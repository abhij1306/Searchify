# Product-catalog request/response schemas (ids string UUID, invariant 5).
#
# ``ProductResponse`` embeds the computed per-SKU ``completeness`` (pure
# function of the row, config matrix) so the catalog badge is always in sync.
# ORM -> DTO mappers live here (the surface is small enough that a separate
# mappers module would be indirection).
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.products.completeness import product_completeness
from app.models.product import CompetitorProduct, Product


def _clean_str_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def _clean_aliases(value: Any) -> list[str]:
    return _clean_str_list(value)


def _clean_optional_aliases(value: Any) -> list[str] | None:
    if value is None:
        return None
    return _clean_str_list(value)


def _clean_currency(value: str | None) -> str | None:
    return value.strip().upper() if value is not None else None


class ProductVariant(BaseModel):
    """One variant value object inside ``Product.variants``."""

    name: str = Field(min_length=1, max_length=255)
    sku: str = Field(default="", max_length=128)
    price: float | None = Field(default=None, ge=0)


class ProductCompleteness(BaseModel):
    """Computed data-quality badge: present/total against the config matrix."""

    score: float
    present: int
    total: int
    missing: list[str]


class ProductInput(BaseModel):
    """A single product on create/import. Currency is normalized to ISO-4217
    uppercase by the service."""

    sku: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    aliases: list[str] = Field(default_factory=list)
    variants: list[ProductVariant] = Field(default_factory=list)
    price: float | None = Field(default=None, ge=0)
    currency: str = Field(default="", max_length=3)
    url: str = Field(default="", max_length=2048)
    attributes: dict[str, Any] = Field(default_factory=dict)

    _aliases_clean = field_validator("aliases", mode="before")(_clean_aliases)
    _currency_upper = field_validator("currency")(_clean_currency)


class ProductUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    aliases: list[str] | None = None
    variants: list[ProductVariant] | None = None
    price: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=3)
    url: str | None = Field(default=None, max_length=2048)
    attributes: dict[str, Any] | None = None

    _aliases_clean = field_validator("aliases", mode="before")(
        _clean_optional_aliases
    )
    _currency_upper = field_validator("currency")(_clean_currency)


class ProductImport(BaseModel):
    """CSV bulk-create payload: already-parsed product rows (JSON import)."""

    products: list[ProductInput] = Field(default_factory=list)


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    sku: str
    name: str
    aliases: list[str]
    variants: list[ProductVariant]
    price: float | None
    currency: str
    url: str
    attributes: dict[str, Any]
    origin: str
    # Computed on read (never persisted): ``product_to_response`` overwrites
    # this placeholder via ``model_copy``.
    completeness: ProductCompleteness = Field(
        default_factory=lambda: ProductCompleteness(
            score=0.0, present=0, total=0, missing=[]
        )
    )
    created_at: datetime
    updated_at: datetime


class CompetitorProductInput(BaseModel):
    competitor_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    aliases: list[str] = Field(default_factory=list)
    price: float | None = Field(default=None, ge=0)
    currency: str = Field(default="", max_length=3)
    url: str = Field(default="", max_length=2048)

    _aliases_clean = field_validator("aliases", mode="before")(_clean_aliases)
    _currency_upper = field_validator("currency")(_clean_currency)


class CompetitorProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    aliases: list[str] | None = None
    price: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=3)
    url: str | None = Field(default=None, max_length=2048)

    _aliases_clean = field_validator("aliases", mode="before")(
        _clean_optional_aliases
    )
    _currency_upper = field_validator("currency")(_clean_currency)


class CompetitorProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    competitor_id: uuid.UUID
    name: str
    aliases: list[str]
    price: float | None
    currency: str
    url: str
    created_at: datetime
    updated_at: datetime


def product_to_response(product: Product) -> ProductResponse:
    dto = ProductResponse.model_validate(product)
    return dto.model_copy(
        update={"completeness": ProductCompleteness(**product_completeness(product))}
    )


def competitor_product_to_response(
    competitor_product: CompetitorProduct,
) -> CompetitorProductResponse:
    return CompetitorProductResponse.model_validate(competitor_product)


# --------------------------------------------------------------------------
# Visibility projections (persisted rows only, invariant 7)
# --------------------------------------------------------------------------
class ProductVisibilityEntry(BaseModel):
    """One own product's persisted aggregate for the selected audit."""

    product_id: uuid.UUID | None
    sku: str
    name: str
    mention_count: int
    sov_share: float
    avg_rank: float | None
    rank_distribution: dict[str, int]
    price_mention_count: int
    price_accuracy_rate: float | None


class CompetitorProductVisibilityEntry(BaseModel):
    """One competitor product's persisted aggregate for the selected audit."""

    competitor_product_id: uuid.UUID | None
    competitor_name: str
    name: str
    mention_count: int
    sov_share: float
    avg_rank: float | None
    rank_distribution: dict[str, int]
    price_mention_count: int
    price_accuracy_rate: float | None


class ProductVisibilityResponse(BaseModel):
    """Selected-audit product dashboard projection (mirror VisibilityResponse).

    Identity (sku/name/competitor_name) comes from the audit's FROZEN
    configuration so the projection survives later catalog deletes.
    """

    project_id: uuid.UUID
    audit_id: uuid.UUID
    audit_status: str
    product_analyzer_version: str
    product_scoring_rule_version: str
    total_mentions: int
    total_analyses: int
    products: list[ProductVisibilityEntry]
    competitor_products: list[CompetitorProductVisibilityEntry]
    created_at: datetime


class ProductEvidenceItem(BaseModel):
    """One persisted product mention with its frozen prompt + run linkage."""

    mention_id: uuid.UUID
    audit_id: uuid.UUID
    task_id: uuid.UUID
    artifact_id: uuid.UUID | None
    logical_engine: str
    transport_model: str
    prompt_text: str
    prompt_index: int
    repetition: int
    matched_name: str
    matched_sku: str
    first_offset: int | None
    rank_position: int | None
    price_text: str
    price_value: float | None
    price_currency: str
    price_matches_catalog: bool | None
    created_at: datetime


class ProductEvidenceResponse(BaseModel):
    items: list[ProductEvidenceItem]
    truncated: bool
