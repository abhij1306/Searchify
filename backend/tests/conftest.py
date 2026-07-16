"""Shared test fixtures for the Searchify backend.

Uses an async Postgres database with a fresh, isolated schema per test (no
SQLite: the models use Postgres UUID columns). Point ``TEST_DATABASE_URL`` at
a running Postgres — e.g. the Docker container used for migration verification:

    postgresql+asyncpg://postgres:<pw>@localhost:55432/test_db
"""
from __future__ import annotations

import itertools
import os
import re
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.database import Base  # noqa: E402
from app.main import app  # noqa: E402

_COUNTER = itertools.count()
_TEST_RUN_ID = uuid.uuid4().hex[:12]

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:searchify_dev_password@localhost:55432/test_db",
)


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Create an isolated Postgres schema per test and yield a session factory.

    The schema is dropped on teardown so tests never leak state into each other.
    """
    suffix = re.sub(
        r"[^a-zA-Z0-9_]", "_", f"{_TEST_RUN_ID}_{next(_COUNTER)}"
    )
    schema_name = f"test_{suffix}"
    quoted = f'"{schema_name}"'
    engine = create_async_engine(TEST_DATABASE_URL, future=True, echo=False)
    scoped_engine = engine.execution_options(
        schema_translate_map={None: schema_name}
    )
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
