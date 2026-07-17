"""Seeded upgrade/downgrade/re-upgrade test for migration 0008.

Verifies the v2 direct-provider retirement migration on a UNIQUELY-NAMED,
dedicated Postgres database (never the shared per-schema ``session_factory``
fixture, so Alembic's ``alembic_version`` table and DDL cannot collide with the
component suite). The database is created and dropped by the test itself.

This test is intentionally SYNCHRONOUS: ``migrations/env.py`` drives the async
migration with its own ``asyncio.run(...)``, which cannot run inside the
pytest-asyncio event loop. All direct DB work here is wrapped with
``asyncio.run`` in the (loop-free) sync test body.

Assertions (per the plan's acceptance criteria):
  - upgrade deactivates active OpenRouter connections + all OpenRouter routes,
    marking ONLY the rows it changed with ``openrouter_retired_v2``;
  - a connection already inactive before v2 is left untouched (empty reason);
  - direct (openai/anthropic/google) rows are never touched;
  - downgrade restores ONLY the marked rows and drops the added columns;
  - re-upgrade reproduces the same deactivation.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from tests.conftest import TEST_DATABASE_URL

_MARKER = "openrouter_retired_v2"
_BEFORE = "0007_snapshot_provenance"
_AFTER = "0008_direct_openai_retirement"
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _asyncpg_dsn(url: str, *, database: str) -> str:
    """Rewrite the SQLAlchemy async URL into an asyncpg DSN for ``database``."""
    parts = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://"))
    return f"postgresql://{parts.netloc}/{database}"


def _alembic_config(db_url: str) -> Config:
    # NOTE: migrations/env.py overrides ``sqlalchemy.url`` with
    # ``settings.database_url`` at runtime, so the test also points
    # ``settings.database_url`` at the dedicated DB.
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture
def dedicated_db() -> Iterator[str]:
    """Create a uniquely-named database, yield its async URL, drop it after."""
    parts = urlsplit(
        TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    )
    db_name = f"mig_test_{uuid.uuid4().hex[:12]}"
    admin_dsn = _asyncpg_dsn(TEST_DATABASE_URL, database="postgres")

    async def _create() -> None:
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await admin.close()

    async def _drop() -> None:
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                db_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            await admin.close()

    asyncio.run(_create())
    db_url = f"postgresql+asyncpg://{parts.netloc}/{db_name}"
    try:
        yield db_url
    finally:
        asyncio.run(_drop())


async def _seed(dsn: str) -> dict[str, uuid.UUID]:
    """Seed a workspace + a mix of connections/routes at revision 0007."""
    conn = await asyncpg.connect(dsn)
    ids: dict[str, uuid.UUID] = {}
    try:
        ws = uuid.uuid4()
        await conn.execute(
            "INSERT INTO workspaces (id, name, created_at, updated_at) "
            "VALUES ($1, 'Mig WS', now(), now())",
            ws,
        )
        ids["workspace"] = ws

        async def add_connection(key: str, transport: str, active: bool) -> uuid.UUID:
            cid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO provider_connections "
                "(id, workspace_id, label, transport_provider, base_url, "
                " api_key_encrypted, active, last_test_status, created_at, "
                " updated_at) "
                "VALUES ($1,$2,$3,$4,'','x',$5,'',now(),now())",
                cid,
                ws,
                f"{key} conn",
                transport,
                active,
            )
            ids[key] = cid
            return cid

        async def add_route(
            key: str, connection_id: uuid.UUID, engine: str, transport: str
        ) -> None:
            rid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO provider_routes "
                "(id, workspace_id, connection_id, logical_engine, "
                " transport_provider, transport_model, is_default, created_at, "
                " updated_at) "
                "VALUES ($1,$2,$3,$4,$5,'m',true,now(),now())",
                rid,
                ws,
                connection_id,
                engine,
                transport,
            )
            ids[f"route_{key}"] = rid

        # Active OpenRouter connection + route → both retired by the migration.
        or_active = await add_connection("or_active", "openrouter", True)
        await add_route("or_active", or_active, "chatgpt", "openrouter")

        # OpenRouter connection ALREADY inactive before v2 → left untouched.
        or_inactive = await add_connection("or_inactive", "openrouter", False)
        await add_route("or_inactive", or_inactive, "claude", "openrouter")

        # Direct connections/routes → never touched.
        openai_conn = await add_connection("openai", "openai", True)
        await add_route("openai", openai_conn, "chatgpt", "openai")
        google_conn = await add_connection("google", "google", True)
        await add_route("google", google_conn, "gemini", "google")
    finally:
        await conn.close()
    return ids


async def _fetch_all(dsn: str, query: str) -> list[dict]:
    conn = await asyncpg.connect(dsn)
    try:
        return [dict(r) for r in await conn.fetch(query)]
    finally:
        await conn.close()


def test_migration_retires_only_marked_rows(
    dedicated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # migrations/env.py reads ``settings.database_url`` when it runs, so point
    # it at the dedicated DB for the duration of this test.
    from app.core.config import settings

    monkeypatch.setattr(settings, "database_url", dedicated_db)

    cfg = _alembic_config(dedicated_db)
    dsn = _asyncpg_dsn(dedicated_db, database=urlsplit(dedicated_db).path[1:])

    # Bring the dedicated DB up to the pre-v2 revision and seed it.
    command.upgrade(cfg, _BEFORE)
    ids = asyncio.run(_seed(dsn))

    # --- Upgrade to 0008 ---------------------------------------------------
    command.upgrade(cfg, _AFTER)
    rows = {
        r["id"]: r
        for r in asyncio.run(
            _fetch_all(
                dsn,
                "SELECT id, active, deactivation_reason FROM provider_connections",
            )
        )
    }
    routes = {
        r["id"]: r
        for r in asyncio.run(
            _fetch_all(
                dsn,
                "SELECT id, active, deactivation_reason FROM provider_routes",
            )
        )
    }

    # Active OpenRouter connection retired + marked.
    assert rows[ids["or_active"]]["active"] is False
    assert rows[ids["or_active"]]["deactivation_reason"] == _MARKER
    # Already-inactive OpenRouter connection untouched (empty reason).
    assert rows[ids["or_inactive"]]["active"] is False
    assert rows[ids["or_inactive"]]["deactivation_reason"] == ""
    # Direct connections stay active + unmarked.
    assert rows[ids["openai"]]["active"] is True
    assert rows[ids["openai"]]["deactivation_reason"] == ""
    assert rows[ids["google"]]["active"] is True

    # Every OpenRouter route is retired + marked; direct routes untouched.
    assert routes[ids["route_or_active"]]["active"] is False
    assert routes[ids["route_or_active"]]["deactivation_reason"] == _MARKER
    assert routes[ids["route_or_inactive"]]["active"] is False
    assert routes[ids["route_or_inactive"]]["deactivation_reason"] == _MARKER
    assert routes[ids["route_openai"]]["active"] is True
    assert routes[ids["route_openai"]]["deactivation_reason"] == ""
    assert routes[ids["route_google"]]["active"] is True

    # --- Downgrade to 0007 -------------------------------------------------
    command.downgrade(cfg, _BEFORE)
    route_cols = {
        r["column_name"]
        for r in asyncio.run(
            _fetch_all(
                dsn,
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'provider_routes'",
            )
        )
    }
    assert "active" not in route_cols
    assert "deactivation_reason" not in route_cols
    conn_cols = {
        r["column_name"]
        for r in asyncio.run(
            _fetch_all(
                dsn,
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'provider_connections'",
            )
        )
    }
    assert "deactivation_reason" not in conn_cols

    # ONLY the marked connection was reactivated; the pre-v2 inactive one stays
    # inactive.
    active_map = {
        r["id"]: r["active"]
        for r in asyncio.run(
            _fetch_all(dsn, "SELECT id, active FROM provider_connections")
        )
    }
    assert active_map[ids["or_active"]] is True
    assert active_map[ids["or_inactive"]] is False
    assert active_map[ids["openai"]] is True

    # --- Re-upgrade to 0008 ------------------------------------------------
    command.upgrade(cfg, _AFTER)
    rows2 = {
        r["id"]: r
        for r in asyncio.run(
            _fetch_all(
                dsn,
                "SELECT id, active, deactivation_reason FROM provider_connections",
            )
        )
    }
    assert rows2[ids["or_active"]]["active"] is False
    assert rows2[ids["or_active"]]["deactivation_reason"] == _MARKER
    # The pre-v2 inactive connection is still inactive + unmarked.
    assert rows2[ids["or_inactive"]]["active"] is False
    assert rows2[ids["or_inactive"]]["deactivation_reason"] == ""
