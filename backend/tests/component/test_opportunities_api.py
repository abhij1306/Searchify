"""Component tests for the opportunities router (flat /api/v1 surface).

Covers auth (401), second-workspace isolation (404), the recompute endpoint
(200 + provenance body, 404 for a foreign audit), the priority-sorted
keyset-paginated catalog (ordering, filters, coded 400 cursor, 422 unknown
token), detail (200/404), the status PATCH (200/409 coded/422/404), the
summary (computed=false then populated), and the CSV/Markdown exports.
Seed helpers live in ``tests/component/opportunity_helpers.py``.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.audit import Audit
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from tests.component.opportunity_helpers import (
    SCORE_BRAND_ABSENT,
    SCORE_OWNED_PAGE,
    Scenario,
    _seed_scenario,
)

pytestmark = pytest.mark.asyncio

_EMAIL = "opp@example.com"


async def _register(client: httpx.AsyncClient, email: str = _EMAIL) -> None:
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert reg.status_code == 201


def _headers(scn: Scenario) -> dict[str, str]:
    return {"X-Workspace-Id": str(scn.workspace_id)}


async def _seed_and_recompute(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> Scenario:
    await _register(client)
    async with session_factory() as session:
        scn = await _seed_scenario(session, email=_EMAIL)
    resp = await client.post(
        f"/api/v1/projects/{scn.project_id}/opportunities/recompute",
        headers=_headers(scn),
    )
    assert resp.status_code == 200
    return scn


# =========================================================================
# Auth + workspace isolation
# =========================================================================
async def test_unauthenticated_requests_401(client: httpx.AsyncClient) -> None:
    pid = uuid.uuid4()
    assert (
        await client.get(f"/api/v1/projects/{pid}/opportunities")
    ).status_code == 401
    assert (
        await client.get(f"/api/v1/projects/{pid}/opportunities/summary")
    ).status_code == 401
    assert (
        await client.post(f"/api/v1/projects/{pid}/opportunities/recompute")
    ).status_code == 401
    assert (
        await client.get(f"/api/v1/opportunities/{uuid.uuid4()}")
    ).status_code == 401
    assert (
        await client.patch(
            f"/api/v1/opportunities/{uuid.uuid4()}", json={"status": "open"}
        )
    ).status_code == 401
    assert (
        await client.get(f"/api/v1/projects/{pid}/opportunities/export.csv")
    ).status_code == 401


async def test_cross_workspace_isolation_404(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A second workspace the user belongs to never sees the first's rows."""
    scn = await _seed_and_recompute(client, session_factory)
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.email == _EMAIL))
        assert user is not None
        other_ws = Workspace(name="Other WS")
        session.add(other_ws)
        await session.flush()
        session.add(
            WorkspaceMember(
                workspace_id=other_ws.id, user_id=user.id, role="owner"
            )
        )
        await session.commit()
        other_headers = {"X-Workspace-Id": str(other_ws.id)}

    listed = await client.get(
        f"/api/v1/projects/{scn.project_id}/opportunities", headers=other_headers
    )
    assert listed.status_code == 404
    detail = await client.get(
        f"/api/v1/opportunities/{uuid.uuid4()}", headers=other_headers
    )
    assert detail.status_code == 404
    summary = await client.get(
        f"/api/v1/projects/{scn.project_id}/opportunities/summary",
        headers=other_headers,
    )
    assert summary.status_code == 404
    recompute = await client.post(
        f"/api/v1/projects/{scn.project_id}/opportunities/recompute",
        headers=other_headers,
    )
    assert recompute.status_code == 404
    export = await client.get(
        f"/api/v1/projects/{scn.project_id}/opportunities/export.csv",
        headers=other_headers,
    )
    assert export.status_code == 404

    # The owning workspace still resolves everything.
    assert (
        await client.get(
            f"/api/v1/projects/{scn.project_id}/opportunities",
            headers=_headers(scn),
        )
    ).status_code == 200


# =========================================================================
# Recompute + summary
# =========================================================================
async def test_recompute_returns_snapshot_with_provenance(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client)
    async with session_factory() as session:
        scn = await _seed_scenario(session, email=_EMAIL)

    resp = await client.post(
        f"/api/v1/projects/{scn.project_id}/opportunities/recompute",
        headers=_headers(scn),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 4
    assert body["run_id"]
    assert body["audit_id"] == str(scn.audit_id)
    assert body["site_crawl_id"] == str(scn.crawl_id)
    assert body["counts_by_type"] == {
        "site": 2,
        "topic": 0,
        "traffic": 0,
        "visibility": 2,
    }
    assert body["counts_by_severity"]["high"] == 1
    assert body["counts_by_status"]["open"] == 4
    assert body["median_priority"] == 50.0
    assert body["analyzer_version"]
    assert body["rule_version"]
    assert body["formula_version"]
    assert body["created_at"]


async def test_recompute_foreign_audit_404(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scn = await _seed_and_recompute(client, session_factory)
    async with session_factory() as session:
        # An audit in ANOTHER workspace (foreign to the caller's).
        other_ws = Workspace(name="Foreign WS")
        session.add(other_ws)
        await session.flush()
        foreign_audit = Audit(
            workspace_id=other_ws.id,
            project_id=scn.project_id,  # even reusing ids cannot cross scopes
            status="completed",
        )
        session.add(foreign_audit)
        await session.commit()
        foreign_audit_id = foreign_audit.id

    resp = await client.post(
        f"/api/v1/projects/{scn.project_id}/opportunities/recompute",
        headers=_headers(scn),
        json={"audit_id": str(foreign_audit_id)},
    )
    assert resp.status_code == 404
    missing = await client.post(
        f"/api/v1/projects/{scn.project_id}/opportunities/recompute",
        headers=_headers(scn),
        json={"audit_id": str(uuid.uuid4())},
    )
    assert missing.status_code == 404


async def test_summary_empty_then_populated(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client)
    async with session_factory() as session:
        scn = await _seed_scenario(session, email=_EMAIL)
    url = f"/api/v1/projects/{scn.project_id}/opportunities/summary"

    before = await client.get(url, headers=_headers(scn))
    assert before.status_code == 200
    body = before.json()
    assert body["computed"] is False
    assert body["counts_by_type"] == {}
    assert body["total_count"] == 0
    assert body["run_id"] is None
    assert body["analyzer_version"]

    recompute = await client.post(
        f"/api/v1/projects/{scn.project_id}/opportunities/recompute",
        headers=_headers(scn),
    )
    assert recompute.status_code == 200

    after = await client.get(url, headers=_headers(scn))
    assert after.status_code == 200
    body = after.json()
    assert body["computed"] is True
    assert body["run_id"] == recompute.json()["run_id"]
    assert body["total_count"] == 4
    assert body["median_priority"] == 50.0
    assert body["computed_at"]


# =========================================================================
# Catalog list
# =========================================================================
async def test_list_ordering_filters_and_keyset(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scn = await _seed_and_recompute(client, session_factory)
    url = f"/api/v1/projects/{scn.project_id}/opportunities"

    resp = await client.get(url, headers=_headers(scn))
    assert resp.status_code == 200
    body = resp.json()
    assert [item["rule_id"] for item in body["items"]] == [
        "brand_absent_high_value_prompt",
        "owned_page_not_cited",
        "missing_structured_data",
        "thin_content",
    ]
    first = body["items"][0]
    assert first["priority_score"] == SCORE_BRAND_ABSENT
    assert first["status"] == "open"
    assert first["target_key"]
    assert first["created_at"]
    assert body["next_cursor"] is None

    page1 = await client.get(f"{url}?limit=2", headers=_headers(scn))
    assert page1.status_code == 200
    cursor = page1.json()["next_cursor"]
    assert cursor
    page2 = await client.get(
        f"{url}?limit=2&cursor={cursor}", headers=_headers(scn)
    )
    assert page2.status_code == 200
    assert [item["rule_id"] for item in page2.json()["items"]] == [
        "missing_structured_data",
        "thin_content",
    ]
    assert page2.json()["next_cursor"] is None

    # A tampered/replayed cursor is a 400; an unknown token is a 422.
    assert (
        await client.get(f"{url}?cursor=not-a-cursor", headers=_headers(scn))
    ).status_code == 400
    assert (
        await client.get(
            f"{url}?limit=2&cursor={cursor}&severity=high", headers=_headers(scn)
        )
    ).status_code == 400
    assert (
        await client.get(f"{url}?type=bogus", headers=_headers(scn))
    ).status_code == 422
    assert (
        await client.get(f"{url}?severity=bogus", headers=_headers(scn))
    ).status_code == 422
    assert (
        await client.get(f"{url}?status=bogus", headers=_headers(scn))
    ).status_code == 422
    assert (
        await client.get(f"{url}?rule_id=bogus", headers=_headers(scn))
    ).status_code == 422

    by_type = await client.get(f"{url}?type=site", headers=_headers(scn))
    assert {item["rule_id"] for item in by_type.json()["items"]} == {
        "missing_structured_data",
        "thin_content",
    }
    by_floor = await client.get(f"{url}?min_priority=50", headers=_headers(scn))
    assert [item["priority_score"] for item in by_floor.json()["items"]] == [
        SCORE_BRAND_ABSENT,
        SCORE_OWNED_PAGE,
    ]
    by_rule = await client.get(f"{url}?rule_id=thin_content", headers=_headers(scn))
    assert len(by_rule.json()["items"]) == 1
    dismissed = await client.get(f"{url}?status=dismissed", headers=_headers(scn))
    assert dismissed.json()["items"] == []


# =========================================================================
# Detail + status PATCH
# =========================================================================
async def test_detail_200_and_404(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scn = await _seed_and_recompute(client, session_factory)
    listed = await client.get(
        f"/api/v1/projects/{scn.project_id}/opportunities?rule_id=thin_content",
        headers=_headers(scn),
    )
    item = listed.json()["items"][0]

    detail = await client.get(
        f"/api/v1/opportunities/{item['id']}", headers=_headers(scn)
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == item["id"]
    assert body["rule_id"] == "thin_content"
    assert body["remediation"]
    assert body["evidence"]["issue_rule_id"] == "aeo.sufficient_text"
    assert body["source_issue_ids"] == [str(scn.issue_thin_id)]
    assert body["source_traffic_ids"] == []
    assert body["analyzer_version"]
    assert body["superseded_at"] is None

    missing = await client.get(
        f"/api/v1/opportunities/{uuid.uuid4()}", headers=_headers(scn)
    )
    assert missing.status_code == 404


async def test_patch_status_200_409_422_404(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scn = await _seed_and_recompute(client, session_factory)
    listed = await client.get(
        f"/api/v1/projects/{scn.project_id}/opportunities?rule_id=thin_content",
        headers=_headers(scn),
    )
    item = listed.json()["items"][0]

    patched = await client.patch(
        f"/api/v1/opportunities/{item['id']}",
        headers=_headers(scn),
        json={"status": "in_progress"},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "in_progress"
    assert patched.json()["rule_id"] == "thin_content"

    # Unknown status + unknown body keys are 422; a missing row is 404.
    assert (
        await client.patch(
            f"/api/v1/opportunities/{item['id']}",
            headers=_headers(scn),
            json={"status": "bogus"},
        )
    ).status_code == 422
    assert (
        await client.patch(
            f"/api/v1/opportunities/{item['id']}",
            headers=_headers(scn),
            json={"status": "open", "priority_score": 1},
        )
    ).status_code == 422
    assert (
        await client.patch(
            f"/api/v1/opportunities/{uuid.uuid4()}",
            headers=_headers(scn),
            json={"status": "open"},
        )
    ).status_code == 404

    # After another recompute the row is superseded: PATCH is a coded 409.
    recompute = await client.post(
        f"/api/v1/projects/{scn.project_id}/opportunities/recompute",
        headers=_headers(scn),
    )
    assert recompute.status_code == 200
    conflict = await client.patch(
        f"/api/v1/opportunities/{item['id']}",
        headers=_headers(scn),
        json={"status": "resolved"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "opportunity_superseded"


# =========================================================================
# Exports
# =========================================================================
async def test_export_csv_and_markdown(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scn = await _seed_and_recompute(client, session_factory)
    base = f"/api/v1/projects/{scn.project_id}/opportunities"

    csv_resp = await client.get(f"{base}/export.csv", headers=_headers(scn))
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    disposition = csv_resp.headers["content-disposition"]
    assert "attachment" in disposition and "opportunities-" in disposition
    lines = csv_resp.text.strip().splitlines()
    assert lines[0].startswith("id,rule_id,opportunity_type,severity")
    assert len(lines) == 5  # header + 4 rows
    assert "brand_absent_high_value_prompt" in csv_resp.text
    assert "best crm for small teams" in csv_resp.text

    md_resp = await client.get(f"{base}/export.md", headers=_headers(scn))
    assert md_resp.status_code == 200
    assert md_resp.headers["content-type"].startswith("text/markdown")
    assert md_resp.text.startswith("# Searchify — Opportunities")
    assert "missing_structured_data" in md_resp.text

    filtered = await client.get(f"{base}/export.csv?type=site", headers=_headers(scn))
    assert filtered.status_code == 200
    assert "missing_structured_data" in filtered.text
    assert "brand_absent_high_value_prompt" not in filtered.text
    assert (
        await client.get(f"{base}/export.csv?severity=bogus", headers=_headers(scn))
    ).status_code == 422
