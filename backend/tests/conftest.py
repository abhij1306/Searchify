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
import warnings
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

# Between-test cleanup, as one round trip. Raw SQL bypasses the engine's
# ``schema_translate_map`` (which only rewrites SQLAlchemy constructs), so the
# schema is spelled out here.
#
# This deliberately uses DELETE rather than TRUNCATE. TRUNCATE is the faster
# choice for large tables, but it takes an ACCESS EXCLUSIVE lock and rewrites
# (and fsyncs) each table's storage even when the table is already empty —
# across 67 mostly-empty test tables that measured ~1280ms per test, i.e. the
# dominant cost of the whole suite. DELETE on an empty table is a no-op seq
# scan; the same cleanup measured ~8ms, a ~167x improvement.
#
# The statements are wrapped in a DO block because asyncpg sends statements as
# prepared statements and refuses multiple commands in one — the DO block is a
# single command, so all 67 deletes still cost one round trip.
#
# Order matters for DELETE (unlike TRUNCATE ... CASCADE): a parent row cannot go
# while a child still references it. ``sorted_tables`` is dependency order
# (parents first), so it is reversed here to delete children first.
# ``SET CONSTRAINTS ALL DEFERRED`` covers the FK cycles between the audit-task /
# artifact tables that make a total order impossible; it is a no-op for
# non-deferrable constraints rather than an error.
#
# NOTE: do NOT try to narrow this to "only non-empty tables" using
# ``pg_class.reltuples`` / ``relpages`` / ``pg_stat_all_tables.n_live_tup``.
# Those counters are estimates: ``reltuples`` is ``-1`` on a never-analyzed
# table (so it matches every freshly created table, narrowing nothing), while
# ``relpages`` and ``n_live_tup`` still read 0 immediately after an insert, so
# genuinely written tables get skipped and their rows leak into the next test.
with warnings.catch_warnings():
    # Emits a cycle warning for the audit-task / artifact tables; the deferred
    # constraints above are what actually makes those safe to delete.
    warnings.simplefilter("ignore")
    _DELETE_ORDER = list(reversed(Base.metadata.sorted_tables))

_CLEANUP_SQL = "DO $$ BEGIN SET CONSTRAINTS ALL DEFERRED; {deletes} END $$;".format(
    deletes="".join(
        f'DELETE FROM "{_TEST_SCHEMA}"."{table.name}";' for table in _DELETE_ORDER
    )
)


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

    Every table is emptied on teardown so tests never leak state into each
    other — the same isolation the old per-test schema gave, without paying to
    rebuild the schema each time. See ``_CLEANUP_SQL`` for why that is a batched
    DELETE rather than a TRUNCATE.

    The factory stays bound to the shared engine (rather than to a single
    connection inside an outer transaction that gets rolled back) because the
    queue tests exercise ``SELECT ... FOR UPDATE SKIP LOCKED`` from concurrent
    sessions: they need genuinely separate connections, which a rollback-based
    fixture could not give them.
    """
    factory = async_sessionmaker(
        _schema_engine, expire_on_commit=False, class_=AsyncSession
    )
    try:
        yield factory
    finally:
        async with _schema_engine.begin() as conn:
            await conn.execute(text(_CLEANUP_SQL))


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
