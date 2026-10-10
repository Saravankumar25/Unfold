"""Integration fixtures: real data stores from `docker compose up -d` (ports from the repo-root .env).

Isolation from the development data: a dedicated Postgres database (`<db>_test`, migrated to head once per run),
Redis logical DB 1, bucket `unfold-test`, Qdrant prefix `unfold_test`. Neo4j data is scoped by random project ids.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from unfold.core.config import Settings
from unfold.runtime import Runtime

BACKEND = Path(__file__).resolve().parents[2]


def _test_urls() -> tuple[str, str]:
    base = Settings()
    db_url = make_url(base.database_url)
    test_db = f"{db_url.database}_test"
    redis_url = base.redis_url.rsplit("/", 1)[0] + "/1"
    return db_url.set(database=test_db).render_as_string(hide_password=False), redis_url


TEST_DATABASE_URL, TEST_REDIS_URL = _test_urls()


async def _ensure_database(url: str) -> None:
    u = make_url(url)
    conn = await asyncpg.connect(
        host=u.host, port=u.port, user=u.username, password=u.password, database="postgres"
    )
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", u.database)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{u.database}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
async def migrated_database() -> str:
    await _ensure_database(TEST_DATABASE_URL)
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
    )
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def settings(migrated_database: str) -> Settings:
    return Settings(
        app_env="test",
        database_url=migrated_database,
        redis_url=TEST_REDIS_URL,
        object_store_bucket="unfold-test",
        qdrant_collection_prefix="unfold_test",
        # One shared test client IP would trip the real ceilings; the limit tests build their own app.
        max_sessions_per_ip_per_hour=100_000,
        max_sessions_per_hour=100_000,
        max_projects_per_ip_per_hour=100_000,
        max_projects_per_hour_global=100_000,
        # the shared test database keeps `queued` jobs from earlier runs; the caps have their own tests
        max_active_jobs_per_session=100_000,
        max_queued_jobs_global=100_000,
    )


@pytest.fixture(scope="session")
async def runtime(settings: Settings) -> AsyncIterator[Runtime]:
    rt = Runtime.create(settings, db_pool_size=5)
    await rt.objects.ensure_bucket()
    yield rt
    await rt.aclose()


@pytest.fixture
def project_id() -> str:
    return str(uuid.uuid4())
