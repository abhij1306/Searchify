"""Product visibility projections + evidence + export over HTTP (component).

Drives the real HTTP surface through the ASGI client against a fully analyzed
audit (provider calls stubbed), covering the Task 4 acceptance:

  - ``GET /projects/{id}/products/visibility`` defaults to the latest
    completed audit with product snapshots; an explicit ``audit_id`` selects
    that audit; a project with no product audit 404s;
  - identity (sku/name/competitor_name) comes from the audit's FROZEN
    configuration — later catalog edits/deletes never alter the projection;
  - ``GET /products/{id}/visibility/evidence`` serves persisted mention rows
    with frozen prompt text + run linkage, ``truncated`` windowing, engine
    filter, and a 422 limit cap;
  - ``GET .../visibility/export.csv`` downloads with the right media type;
  - cross-workspace access 404s (invariant 5);
  - projections never call a provider (invariant 7).
"""

from __future__ import annotations

import csv
import io
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors.answer_engines.contracts import (
    AnswerEngineRequest,
    AnswerEngineResponse,
    CitationResult,
    SearchEventResult,
)
from app.core.config.audits import audit_settings
from app.core.config.products import PRODUCT_EVIDENCE_MAX_LIMIT
from app.core.config.provider_catalog import (
    ENGINE_CHATGPT,
    ENGINE_GEMINI,
    TRANSPORT_GOOGLE,
)
from app.domain.audits.planner import create_audit
from app.models.brand import Competitor
from app.models.product import CompetitorProduct, Product
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.workers import audit_worker
from app.workers.audit_worker import AuditWorker
from tests.component.audit_helpers import seed_audit_fixtures

_ANSWER = (
    "1. Acme VoltBike 500 — the best commuter pick at $2,499.00\n"
    "2. Globex CityBike 450 — a solid alternative at $2,399.00\n"
    "3. Something generic with no catalog entry"
)


class _ProductStubAdapter:
    """In-memory stand-in answering with a product list (no network)."""

    logical_engine = ENGINE_GEMINI
    transport_provider = TRANSPORT_GOOGLE

    def __init__(self, **_: object) -> None:
        pass

    async def execute(self, request: AnswerEngineRequest) -> AnswerEngineResponse:
        return AnswerEngineResponse(
            logical_engine=self.logical_engine,
            transport_provider=self.transport_provider,
            transport_model=request.model,
            answer_text=_ANSWER,
            search_used=True,
            search_events=(SearchEventResult(sequence=0, query=request.prompt),),
            citations=(
                CitationResult(
                    ordinal=0,
                    url="https://acme.com/",
                    title="Acme",
                    domain="acme.com",
                    start_index=0,
                    end_index=4,
                    cited_text="Acme",
                ),
            ),
            provider_metadata={},
            usage={"input_tokens": 10, "output_tokens": 20},
            latency_ms=5,
        )


@pytest.fixture
def _stub_adapter(monkeypatch: pytest.MonkeyPatch):
    def _build(**_: object) -> _ProductStubAdapter:
        return _ProductStubAdapter()

    monkeypatch.setattr(audit_worker, "build_adapter", _build)
    monkeypatch.setattr(audit_settings, "min_request_interval_seconds", 0.0)
    monkeypatch.setattr(audit_settings, "heartbeat_interval_seconds", 3600.0)


async def _seed_workspace_with_catalog(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    prompts: int = 2,
):
    """Register a user, seed a catalog workspace, attach the user as owner."""
    email = f"prod-vis-{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert reg.status_code == 201

    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=prompts)
        product = Product(
            project_id=seed.project_id,
            sku="AC-VB500",
            name="Acme VoltBike 500",
            aliases=["VoltBike"],
            price=Decimal("2499.00"),
            currency="USD",
            url="https://acme.com/p/voltbike",
        )
        session.add(product)
        competitor = await session.scalar(
            select(Competitor).where(Competitor.project_id == seed.project_id)
        )
        assert competitor is not None
        competitor_product = CompetitorProduct(
            project_id=seed.project_id,
            competitor_id=competitor.id,
            name="Globex CityBike 450",
            price=Decimal("2399.00"),
            currency="USD",
        )
        session.add(competitor_product)
        user = await session.scalar(select(User).where(User.email == email))
        assert user is not None
        session.add(
            WorkspaceMember(
                workspace_id=seed.workspace_id, user_id=user.id, role="owner"
            )
        )
        await session.commit()
    return seed, product, competitor_product


async def _run_audit(
    session_factory: async_sessionmaker[AsyncSession], seed, *, reps: int = 1
):
    async with session_factory() as session:
        audit = await create_audit(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            engines=seed.engines,
            prompt_set_id=seed.prompt_set_id,
            repetitions=reps,
            random_seed="1",
        )
    worker = AuditWorker(session_factory=session_factory, owner="w-prodvis")
    await worker.run_until_idle()
    return audit


def _headers(seed) -> dict[str, str]:
    return {"X-Workspace-Id": str(seed.workspace_id)}


@pytest.mark.asyncio
async def test_visibility_defaults_to_latest_and_explicit_audit(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed, product, competitor_product = await _seed_workspace_with_catalog(
        client, session_factory
    )
    first = await _run_audit(session_factory, seed)
    second = await _run_audit(session_factory, seed)

    # Projections must never touch a provider (invariant 7).
    def _boom(**_: object):
        raise AssertionError("projection must not call a provider (invariant 7)")

    monkeypatch.setattr(audit_worker, "build_adapter", _boom)

    # Default resolves to the LATEST completed audit with product snapshots.
    default = await client.get(
        f"/api/v1/projects/{seed.project_id}/products/visibility",
        headers=_headers(seed),
    )
    assert default.status_code == 200
    body = default.json()
    assert body["audit_id"] == str(second.id)
    assert body["project_id"] == str(seed.project_id)
    assert body["audit_status"] == "completed"
    assert body["product_analyzer_version"]
    assert body["product_scoring_rule_version"]
    # 2 prompts x 1 rep -> 2 executions, each mentioning both entries.
    assert body["total_analyses"] == 2
    assert body["total_mentions"] == 4

    own = body["products"]
    assert len(own) == 1
    entry = own[0]
    assert entry["product_id"] == str(product.id)
    assert entry["sku"] == "AC-VB500"
    assert entry["name"] == "Acme VoltBike 500"
    assert entry["mention_count"] == 2
    assert entry["sov_share"] == 0.5
    assert entry["avg_rank"] == 1.0
    assert entry["rank_distribution"]["top_1"] == 2
    assert entry["price_mention_count"] == 2
    assert entry["price_accuracy_rate"] == 1.0

    competitor_entries = body["competitor_products"]
    assert len(competitor_entries) == 1
    competitor_entry = competitor_entries[0]
    assert competitor_entry["competitor_product_id"] == str(competitor_product.id)
    assert competitor_entry["competitor_name"] == "Globex"
    assert competitor_entry["name"] == "Globex CityBike 450"
    assert competitor_entry["mention_count"] == 2
    assert competitor_entry["avg_rank"] == 2.0

    # An explicit audit_id selects that audit's projection.
    explicit = await client.get(
        f"/api/v1/projects/{seed.project_id}/products/visibility",
        params={"audit_id": str(first.id)},
        headers=_headers(seed),
    )
    assert explicit.status_code == 200
    assert explicit.json()["audit_id"] == str(first.id)

    # Identity comes from the FROZEN configuration: deleting the catalog rows
    # must not change the projection (survives catalog deletes).
    async with session_factory() as session:
        await session.delete(await session.get(Product, product.id))
        await session.commit()
    frozen = await client.get(
        f"/api/v1/projects/{seed.project_id}/products/visibility",
        headers=_headers(seed),
    )
    assert frozen.status_code == 200
    frozen_entry = frozen.json()["products"][0]
    assert frozen_entry["name"] == "Acme VoltBike 500"
    assert frozen_entry["sku"] == "AC-VB500"
    assert frozen_entry["mention_count"] == 2


@pytest.mark.asyncio
async def test_visibility_engine_slice_is_projection_only(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    seed, product, _competitor_product = await _seed_workspace_with_catalog(
        client, session_factory
    )
    await _run_audit(session_factory, seed)
    url = f"/api/v1/projects/{seed.project_id}/products/visibility"

    # The audit ran on gemini only: the gemini slice matches the overall
    # projection (served from the persisted per-engine aggregate).
    gemini = await client.get(
        url, params={"engine": ENGINE_GEMINI}, headers=_headers(seed)
    )
    assert gemini.status_code == 200
    body = gemini.json()
    assert body["total_mentions"] == 4
    entry = body["products"][0]
    assert entry["mention_count"] == 2
    assert entry["sov_share"] == 0.5
    assert entry["avg_rank"] == 1.0

    # A valid engine with no data slices to a zero-filled aggregate (200).
    chatgpt = await client.get(
        url, params={"engine": ENGINE_CHATGPT}, headers=_headers(seed)
    )
    assert chatgpt.status_code == 200
    empty = chatgpt.json()
    assert empty["total_mentions"] == 0
    assert empty["products"][0]["mention_count"] == 0
    assert empty["products"][0]["price_accuracy_rate"] is None

    # An unknown engine is a 422 (never a silent all-engines fallback).
    bogus = await client.get(url, params={"engine": "bogus"}, headers=_headers(seed))
    assert bogus.status_code == 422


@pytest.mark.asyncio
async def test_visibility_empty_project_404(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    # No audit at all -> 404 (defaults-to-latest finds nothing).
    seed, _product, _competitor_product = await _seed_workspace_with_catalog(
        client, session_factory
    )
    resp = await client.get(
        f"/api/v1/projects/{seed.project_id}/products/visibility",
        headers=_headers(seed),
    )
    assert resp.status_code == 404

    csv_resp = await client.get(
        f"/api/v1/projects/{seed.project_id}/products/visibility/export.csv",
        headers=_headers(seed),
    )
    assert csv_resp.status_code == 404


@pytest.mark.asyncio
async def test_evidence_items_windowing_and_filters(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    seed, product, _competitor_product = await _seed_workspace_with_catalog(
        client, session_factory
    )
    audit = await _run_audit(session_factory, seed)

    # Default window: both persisted mentions, not truncated.
    resp = await client.get(
        f"/api/v1/products/{product.id}/visibility/evidence",
        headers=_headers(seed),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["truncated"] is False
    assert len(body["items"]) == 2
    item = body["items"][0]
    assert item["audit_id"] == str(audit.id)
    assert item["task_id"]
    assert item["mention_id"]
    assert item["artifact_id"]
    assert item["logical_engine"] == ENGINE_GEMINI
    assert item["transport_model"]
    assert item["prompt_text"].startswith("best option")
    assert item["matched_name"] == "Acme VoltBike 500"
    assert item["matched_sku"] == "AC-VB500"
    assert item["rank_position"] == 1
    assert item["price_value"] == 2499.0
    assert item["price_currency"] == "USD"
    assert item["price_matches_catalog"] is True

    # Bounded window: limit=1 truncates deterministically.
    window = await client.get(
        f"/api/v1/products/{product.id}/visibility/evidence",
        params={"limit": 1},
        headers=_headers(seed),
    )
    assert window.status_code == 200
    window_body = window.json()
    assert len(window_body["items"]) == 1
    assert window_body["truncated"] is True

    # Engine filter intersects: matching engine keeps rows, other engine empties.
    matching = await client.get(
        f"/api/v1/products/{product.id}/visibility/evidence",
        params={"engine": ENGINE_GEMINI},
        headers=_headers(seed),
    )
    assert matching.status_code == 200
    assert len(matching.json()["items"]) == 2
    other = await client.get(
        f"/api/v1/products/{product.id}/visibility/evidence",
        params={"engine": ENGINE_CHATGPT},
        headers=_headers(seed),
    )
    assert other.status_code == 200
    assert other.json() == {"items": [], "truncated": False}

    # Audit filter selects only that audit's evidence.
    by_audit = await client.get(
        f"/api/v1/products/{product.id}/visibility/evidence",
        params={"audit_id": str(audit.id)},
        headers=_headers(seed),
    )
    assert by_audit.status_code == 200
    assert len(by_audit.json()["items"]) == 2

    # Validation: over-cap limit and unknown engine are 422.
    over_cap = await client.get(
        f"/api/v1/products/{product.id}/visibility/evidence",
        params={"limit": PRODUCT_EVIDENCE_MAX_LIMIT + 1},
        headers=_headers(seed),
    )
    assert over_cap.status_code == 422
    bad_engine = await client.get(
        f"/api/v1/products/{product.id}/visibility/evidence",
        params={"engine": "not-an-engine"},
        headers=_headers(seed),
    )
    assert bad_engine.status_code == 422


@pytest.mark.asyncio
async def test_cross_workspace_access_404(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    seed, product, _competitor_product = await _seed_workspace_with_catalog(
        client, session_factory
    )
    audit = await _run_audit(session_factory, seed)

    foreign = {"X-Workspace-Id": str(uuid.uuid4())}
    visibility = await client.get(
        f"/api/v1/projects/{seed.project_id}/products/visibility",
        headers=foreign,
    )
    assert visibility.status_code == 404
    evidence = await client.get(
        f"/api/v1/products/{product.id}/visibility/evidence",
        headers=foreign,
    )
    assert evidence.status_code == 404
    export = await client.get(
        f"/api/v1/projects/{seed.project_id}/products/visibility/export.csv",
        headers=foreign,
    )
    assert export.status_code == 404
    # An explicit foreign audit_id must not leak that the audit exists.
    explicit = await client.get(
        f"/api/v1/projects/{seed.project_id}/products/visibility",
        params={"audit_id": str(audit.id)},
        headers=foreign,
    )
    assert explicit.status_code == 404


@pytest.mark.asyncio
async def test_export_csv_download(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    seed, _product, _competitor_product = await _seed_workspace_with_catalog(
        client, session_factory
    )
    audit = await _run_audit(session_factory, seed)

    resp = await client.get(
        f"/api/v1/projects/{seed.project_id}/products/visibility/export.csv",
        headers=_headers(seed),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert "product-visibility-" in resp.headers["content-disposition"]

    lines = [line for line in resp.text.strip().splitlines() if line]
    header = lines[0].split(",")
    assert header == [
        "audit_id",
        "product",
        "sku",
        "mentions",
        "sov",
        "avg_rank",
        "price_accuracy",
        "engine",
    ]
    # 2 entries x (overall + one engine row) = 4 data rows.
    rows = lines[1:]
    assert len(rows) == 4
    assert any("Acme VoltBike 500" in row and row.endswith("all") for row in rows)
    assert any(
        "Acme VoltBike 500" in row and row.endswith(ENGINE_GEMINI) for row in rows
    )
    assert any("Globex CityBike 450" in row for row in rows)
    assert all(str(audit.id) in row for row in rows)

    # Explicit audit selection works for the export too.
    explicit = await client.get(
        f"/api/v1/projects/{seed.project_id}/products/visibility/export.csv",
        params={"audit_id": str(audit.id)},
        headers=_headers(seed),
    )
    assert explicit.status_code == 200


@pytest.mark.asyncio
async def test_export_csv_formula_neutralization_and_zero_accuracy(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A user-controlled product name that a spreadsheet would evaluate, plus
    # an answer whose price contradicts the catalog (accuracy must render as
    # 0.0, not a blank cell indistinguishable from "not verifiable").
    seed, _product, _competitor_product = await _seed_workspace_with_catalog(
        client, session_factory
    )
    async with session_factory() as session:
        session.add(
            Product(
                project_id=seed.project_id,
                sku='=HYPERLINK("https://evil.example","x")',
                name="=cmd|'/c calc'!A1",
                price=Decimal("10.00"),
                currency="USD",
            )
        )
        await session.commit()
    monkeypatch.setitem(
        globals(),
        "_ANSWER",
        "1. Acme VoltBike 500 — the best commuter pick at $1,999.00",
    )
    audit = await _run_audit(session_factory, seed)

    resp = await client.get(
        f"/api/v1/projects/{seed.project_id}/products/visibility/export.csv",
        params={"audit_id": str(audit.id)},
        headers=_headers(seed),
    )
    assert resp.status_code == 200

    rows = list(csv.reader(io.StringIO(resp.text)))
    header, data = rows[0], rows[1:]
    product_col, sku_col = header.index("product"), header.index("sku")
    accuracy_col = header.index("price_accuracy")

    # Every formula-trigger cell is single-quote neutralized (OWASP pattern,
    # shared app/analysis/csv_cells.py owner).
    hostile = [row for row in data if "calc" in row[product_col]]
    assert hostile, "expected the formula-named product row in the export"
    assert all(row[product_col].startswith("'") for row in hostile)
    assert all(row[sku_col].startswith("'") for row in hostile)

    # The mismatched $1,999.00 vs catalog $2,499.00 renders as an explicit 0.0.
    voltbike = [row for row in data if "VoltBike" in row[product_col]]
    assert voltbike
    assert all(float(row[accuracy_col]) == 0.0 for row in voltbike)
