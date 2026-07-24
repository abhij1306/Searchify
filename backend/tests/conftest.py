"""Shared test fixtures for the Searchify backend.

Uses an async Postgres database with a fresh, isolated schema per test (no
SQLite: the models use Postgres UUID columns). No configuration is needed:
the suite derives server credentials from the app settings (repo ``.env``
``DATABASE_URL``), creates a throwaway ``searchify_tests_<runid>`` database
for the session, and drops it on teardown — nothing persists between runs.
"""

from __future__ import annotations

import asyncio
import re
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.main import app  # noqa: E402

_TEST_RUN_ID = uuid.uuid4().hex[:12]
_TEST_SCHEMA = f"test_{re.sub(r'[^a-zA-Z0-9_]', '_', _TEST_RUN_ID)}"

# Between-test cleanup only needs to touch tables that actually received rows.
# Raw SQL bypasses the engine's ``schema_translate_map`` (which only rewrites
# SQLAlchemy constructs), so the schema is spelled out in the statements below.
# TRUNCATE takes an ACCESS EXCLUSIVE lock and rewrites each table's storage even
# when it is already empty, so truncating all 67 tables after every test cost
# ~2s per test (minutes across the suite) to clear the two or three a typical
# test writes to. Postgres tracks per-relation liveness, so ask it which tables
# are non-empty and truncate just those; a test that wrote nothing pays a single
# cheap catalog query instead of 67 table rewrites.
#
# ``n_live_tup`` comes from the stats collector and is only eventually
# consistent, so it is NOT trusted on its own — it can still read 0 for rows
# written moments earlier. ``pg_class.reltuples`` is likewise an estimate
# (``-1`` before the first ANALYZE). The union of both, plus a ``relpages``
# check to catch freshly written tables neither counter has caught up with yet,
# is deliberately over-inclusive: a table wrongly included is merely truncated
# for nothing (correct, just slower), while one wrongly excluded would leak rows
# into the next test. Correctness never depends on the estimates being exact.
_NON_EMPTY_TABLES_SQL = """
SELECT c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_all_tables s ON s.relid = c.oid
WHERE n.nspname = :schema
  AND c.relkind = 'r'
  AND (c.reltuples <> 0 OR c.relpages > 0 OR COALESCE(s.n_live_tup, 0) > 0)
"""


@pytest.fixture(autouse=True)
def _pin_site_health_capability_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the suite from dev ``.env`` capability overrides.

    The Site Health capability profiles are settings-driven (the dev ``.env``
    ships ``SITE_HEALTH_DEFAULT_CAPABILITY=starter`` and a raised monitored
    limit for full-feature testing). Tests assert the SHIPPED defaults
    (fail-closed free, 10/10/50), so pin the settings back to the constants
    for every test regardless of the developer's local env.
    """
    from app.core.config.site_health import (
        DEFAULT_SITE_HEALTH_CAPABILITY,
        FREE_MONITORED_URL_LIMIT,
        FREE_SAMPLE_URL_LIMIT,
        STARTER_MONITORED_URL_LIMIT,
        site_health_settings,
    )

    monkeypatch.setattr(
        site_health_settings, "default_capability", DEFAULT_SITE_HEALTH_CAPABILITY
    )
    monkeypatch.setattr(
        site_health_settings, "free_sample_url_limit", FREE_SAMPLE_URL_LIMIT
    )
    monkeypatch.setattr(
        site_health_settings, "free_monitored_url_limit", FREE_MONITORED_URL_LIMIT
    )
    monkeypatch.setattr(
        site_health_settings, "starter_monitored_url_limit", STARTER_MONITORED_URL_LIMIT
    )


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    """Create a throwaway session database on the dev Postgres server.

    Reuses the server (host/port/credentials) from ``settings.database_url``
    but never touches the dev database itself: a dedicated
    ``searchify_tests_<runid>`` database is created up front and force-dropped
    on teardown, so test state can never persist between runs.
    """
    base = make_url(settings.database_url)
    db_name = f"searchify_tests_{_TEST_RUN_ID}"
    admin_dsn = base.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )

    async def _admin_execute(statement: str) -> None:
        conn = await asyncpg.connect(dsn=admin_dsn)
        try:
            await conn.execute(statement)
        finally:
            await conn.close()

    asyncio.run(_admin_execute(f'CREATE DATABASE "{db_name}"'))
    try:
        yield base.set(database=db_name).render_as_string(hide_password=False)
    finally:
        # FORCE (PG13+) disconnects any lingering sessions before the drop.
        asyncio.run(_admin_execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))


@pytest_asyncio.fixture(scope="session")
async def _schema_engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    """Build the test schema ONCE per session and yield a scoped engine.

    Creating ``Base.metadata`` per test is prohibitively slow: the models carry
    67 tables and 184 indexes, so a per-test ``create_all`` costs ~250 DDL
    round-trips *per test* (~1s of setup each, minutes of CI wall-clock across
    the suite). The schema is immutable during a run, so it is built once and
    every test reuses it; isolation comes from truncating rows between tests
    (see ``session_factory``), which is orders of magnitude cheaper than DDL.

    The engine — and therefore its connection pool — is session-scoped for the
    same reason: a per-test engine reconnects to Postgres on every test.
    """
    quoted = f'"{_TEST_SCHEMA}"'
    engine = create_async_engine(test_database_url, future=True, echo=False)
    scoped_engine = engine.execution_options(schema_translate_map={None: _TEST_SCHEMA})
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {quoted}"))
    async with scoped_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield scoped_engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(
    _schema_engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory bound to the shared per-session test schema.

    Tables written by the test are truncated on teardown so tests never leak
    state into each other — the same isolation the old per-test schema gave,
    without paying to rebuild the schema each time. ``TRUNCATE ... CASCADE`` in
    one statement also sidesteps the FK cycles between ``audit_tasks`` /
    ``raw_response_artifacts`` / ``site_crawl_tasks`` / ``site_fetch_artifacts``
    that make those tables unorderable for a delete-in-dependency-order pass.

    Only non-empty tables are truncated (see ``_NON_EMPTY_TABLES_SQL``); CASCADE
    still pulls in any FK-referencing table, so rows are never left behind by
    the narrower target list.
    """
    factory = async_sessionmaker(
        _schema_engine, expire_on_commit=False, class_=AsyncSession
    )
    try:
        yield factory
    finally:
        async with _schema_engine.begin() as conn:
            rows = await conn.execute(
                text(_NON_EMPTY_TABLES_SQL), {"schema": _TEST_SCHEMA}
            )
            names = [row[0] for row in rows]
            if names:
                targets = ", ".join(f'"{_TEST_SCHEMA}"."{name}"' for name in names)
                await conn.execute(text(f"TRUNCATE TABLE {targets} CASCADE"))


@pytest_asyncio.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client whose DB dependency is bound to the per-test schema.

    Overrides ``get_session`` so every request opens a fresh session against
    the isolated schema, mirroring production request scoping.
    """
    from app.core.database import get_session

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as http_client:
            yield http_client
    finally:
        app.dependency_overrides.pop(get_session, None)
