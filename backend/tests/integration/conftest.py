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
        # per-session chat and export ceilings have their own tests (they override these)
        max_chat_messages_per_session_per_hour=100_000,
        max_chat_messages_global_per_day=100_000,
        max_exports_per_session_per_hour=100_000,
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


# --- shared helpers for the chat / export / cleanup integration tests --------------------------------------

import io  # noqa: E402
import zipfile  # noqa: E402
from collections.abc import Awaitable, Callable  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from typing import Any  # noqa: E402

from unfold.core.ids import new_session_token, token_hash  # noqa: E402
from unfold.domain.records import JobRec, ProjectRec  # noqa: E402
from unfold.evaluation.scripted import ScriptedLLM  # noqa: E402
from unfold.orchestration.pipeline import run_analysis  # noqa: E402

FIXTURE_REPO = BACKEND.parent / "evals" / "fixtures" / "repos" / "fastapi_rag"


def zip_of(root: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, f"{root.name}/{path.relative_to(root).as_posix()}")
    return buf.getvalue()


class RecordingQueue:
    """`JobQueue` double: records what the API asked for instead of starting worker processes."""

    def __init__(self) -> None:
        self.analyses: list[str] = []
        self.cleanups: list[str] = []
        self.exports: list[str] = []

    async def enqueue_analysis(self, job_id: str, attempt: int, *, defer_s: float | None = None) -> None:
        self.analyses.append(job_id)

    async def enqueue_project_cleanup(self, project_id: str) -> None:
        self.cleanups.append(project_id)

    async def enqueue_export(self, export_id: str, *, defer_s: float | None = None) -> None:
        self.exports.append(export_id)

    async def aclose(self) -> None:
        return None


@dataclass
class SeededProject:
    project_id: str
    job_id: str
    session_id: str
    token: str


@pytest.fixture(scope="session")
def seed_project(runtime: Runtime) -> Callable[..., Awaitable[SeededProject]]:
    """A session + zip project + queued job exactly as `POST /projects/zip` leaves them."""

    async def seed(
        *, expires_in: timedelta = timedelta(hours=1), session_expires_in: timedelta | None = None
    ) -> SeededProject:
        now = datetime.now(UTC)
        token = new_session_token()
        project_id = str(uuid.uuid4())
        key = f"projects/{project_id}/upload/source.zip"
        await runtime.objects.put_bytes(key, zip_of(FIXTURE_REPO), "application/zip")
        session = await runtime.control.create_session(
            token_hash(token), now, now + (session_expires_in or timedelta(hours=1))
        )
        project = ProjectRec(
            id=project_id,
            session_id=session.id,
            input_type="zip",
            technical_level="intermediate",
            analysis_version="test",
            created_at=now,
            expires_at=now + expires_in,
            source_locator="fastapi_rag.zip",
            detected_name="fastapi_rag",
            upload_object_key=key,
        )
        job = JobRec(id=str(uuid.uuid4()), project_id=project_id, created_at=now)
        await runtime.control.create_project(project, job)
        return SeededProject(project_id, job.id, session.id, token)

    return seed


@pytest.fixture(scope="session")
def analyzed_project(
    runtime: Runtime, seed_project: Callable[..., Awaitable[SeededProject]]
) -> Callable[..., Awaitable[SeededProject]]:
    """Seed and run the whole pipeline (real parsing, indexing, graph, vectors); the double is the model."""

    async def make(**kw: object) -> SeededProject:
        seeded = await seed_project(**kw)
        outcome = await run_analysis(runtime, ScriptedLLM(), seeded.job_id)
        assert outcome.succeeded
        return seeded

    return make


@pytest.fixture
def recording_queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
def open_api(settings: Settings) -> Callable[..., Any]:
    """`async with open_api(llm=..., queue=..., **setting_overrides) as client` — a real app on real stores."""
    import contextlib

    import httpx

    from unfold.api.main import create_app

    @contextlib.asynccontextmanager
    async def _open(
        *, llm: Any = None, queue: Any = None, **overrides: Any
    ) -> AsyncIterator[httpx.AsyncClient]:
        cfg = settings.model_copy(update=overrides) if overrides else settings
        app = create_app(cfg, queue=queue or RecordingQueue(), llm=llm)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client

    return _open
