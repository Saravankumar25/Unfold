"""Settings of the e2e stack, shared by `api_server.py` and `tool.py` (kept light: no app imports)."""

from __future__ import annotations

import os
import subprocess
import sys

import asyncpg
from sqlalchemy.engine import make_url

from unfold.core.config import Settings


def e2e_settings() -> Settings:
    base = Settings()
    url = make_url(base.database_url)
    return base.model_copy(
        update={
            "app_env": "test",
            "database_url": url.set(database=f"{url.database}_test").render_as_string(hide_password=False),
            "redis_url": base.redis_url.rsplit("/", 1)[0] + "/1",
            "object_store_bucket": "unfold-test",
            "qdrant_collection_prefix": "unfold_test",
            "sweep_interval_seconds": 0,
            # Journeys share one client address. The per-session ceiling is low so the 429 journey is cheap.
            "max_sessions_per_ip_per_hour": 100_000,
            "max_sessions_per_hour": 100_000,
            "max_projects_per_ip_per_hour": 100_000,
            "max_projects_per_hour_global": 100_000,
            "max_projects_per_hour": 3,
        }
    )


async def prepare_database(settings: Settings) -> None:
    u = make_url(settings.database_url)
    conn = await asyncpg.connect(
        host=u.host, port=u.port, user=u.username, password=u.password, database="postgres"
    )
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", u.database):
            await conn.execute(f'CREATE DATABASE "{u.database}"')
    finally:
        await conn.close()
    env = {**os.environ, "DATABASE_URL": settings.database_url}
    done = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"alembic upgrade failed ({done.returncode}): {done.stderr[-2000:]}")
