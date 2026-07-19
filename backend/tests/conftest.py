"""Shared test fixtures for the Searchify backend.

Uses an async Postgres database with a fresh, isolated schema per test (no
SQLite: the models use Postgres UUID columns). No configuration is needed:
the suite derives server credentials from the app settings (repo ``.env``
``DATABASE_URL``), creates a throwaway ``searchify_tests_<runid>`` database
for the session, and drops it on teardown — nothing persists between runs.
"""

from __future__ import annotations

import asyncio
import itertools
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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.main import app  # noqa: E402

_COUNTER = itertools.count()
_TEST_RUN_ID = uuid.uuid4().hex[:12]


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


@pytest_asyncio.fixture
async def session_factory(
    test_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Create an isolated Postgres schema per test and yield a session factory.

    The schema is dropped on teardown so tests never leak state into each other.
    """
    suffix = re.sub(r"[^a-zA-Z0-9_]", "_", f"{_TEST_RUN_ID}_{next(_COUNTER)}")
    schema_name = f"test_{suffix}"
    quoted = f'"{schema_name}"'
    engine = create_async_engine(test_database_url, future=True, echo=False)
    scoped_engine = engine.execution_options(schema_translate_map={None: schema_name})
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {quoted}"))
    async with scoped_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        scoped_engine, expire_on_commit=False, class_=AsyncSession
    )
    try:
        yield factory
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {quoted} CASCADE"))
        await engine.dispose()


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
