"""On-demand guide-quality check (phase 5 task 9): regenerate one fixture guide with the real worker and the real
Gemini API, rescore it with the deterministic rubric and compare it with the recorded pipeline-v2 numbers.

    cd backend && docker compose up -d   # (repo root) stores; keys in the git-ignored .env
    uv run pytest -m llm tests/integration/test_guide_quality_live.py

Cost bounds: every model tier is pinned to `gemini-3.5-flash-lite` and the job is capped at 40 calls / 250k tokens
(the recorded run used 19-20 calls and 54k-58k tokens). Isolation: its own Postgres database, Redis logical DB, bucket
and Qdrant prefix (override the suffixes with `UNFOLD_LIVE_DB_SUFFIX` / `UNFOLD_LIVE_REDIS_DB`). Output varies run to
run, so only a drop of more than 0.3 below the recorded number counts, plus the hard gates.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from unfold.core.config import Settings, get_settings
from unfold.evaluation import baseline, regression, rubric

pytestmark = pytest.mark.llm

MODEL = "gemini-3.5-flash-lite"
REPO = "fastapi_rag"
BACKEND = Path(__file__).resolve().parents[2]
FIXTURE = BACKEND.parent / "evals" / "fixtures" / "repos" / REPO
OUT = Path(tempfile.gettempdir()) / "unfold-live-quality" / REPO


async def _migrate(url: str) -> None:
    u = make_url(url)
    conn = await asyncpg.connect(
        host=u.host, port=u.port, user=u.username, password=u.password, database="postgres"
    )
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", u.database):
            await conn.execute(f'CREATE DATABASE "{u.database}"')
    finally:
        await conn.close()
    await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": url},
        check=True,
        capture_output=True,
    )


async def test_regenerated_guide_stays_within_the_v2_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    base = Settings()
    if not base.gemini_keys:
        pytest.skip("no Gemini key configured")
    suffix = os.environ.get("UNFOLD_LIVE_DB_SUFFIX", "_quality")
    db_url = (
        make_url(base.database_url)
        .set(database=f"{make_url(base.database_url).database}{suffix}")
        .render_as_string(hide_password=False)
    )
    env = {
        "DATABASE_URL": db_url,
        "REDIS_URL": base.redis_url.rsplit("/", 1)[0] + "/" + os.environ.get("UNFOLD_LIVE_REDIS_DB", "9"),
        "OBJECT_STORE_BUCKET": "unfold-quality",
        "QDRANT_COLLECTION_PREFIX": "unfold_quality",
        "GEMINI_MODEL_FAST": MODEL,
        "GEMINI_MODEL_BALANCED": MODEL,
        "GEMINI_MODEL_REASONING": MODEL,
        "ANALYSIS_LLM_CALL_BUDGET": "40",
        "ANALYSIS_LLM_TOKEN_BUDGET": "250000",
    }
    for k, v in env.items():  # the worker subprocess inherits these
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    try:
        await _migrate(db_url)
        out = (
            OUT  # kept outside the repository so a failure can be rescored and read without another paid run
        )
        args = argparse.Namespace(
            name=REPO,
            github=None,
            dir=str(FIXTURE),
            out=str(out),
            usd_per_mtok_in=None,
            usd_per_mtok_out=None,
        )
        assert await baseline.main_async(args) == 0, "the analysis job did not succeed"
    finally:
        get_settings.cache_clear()

    run = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert set(run["tokens"]["by_model"]) == {MODEL}
    sections, ir, chunks = rubric.load_dir(out)
    recorded = json.loads((regression.BASELINE / "scores.json").read_text(encoding="utf-8"))[REPO]
    current = rubric.score_guide(sections, ir, chunks)
    assert regression.problems(sections, current, recorded, one_sided=True) == [], current["scores"]
