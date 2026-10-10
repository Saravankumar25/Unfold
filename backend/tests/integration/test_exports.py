"""Exports over the real stores and a real Chromium: routes, the one-shot `export` task, kill/retry, downloads."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from unfold.cleanup.service import cleanup_project
from unfold.core.config import Settings
from unfold.core.errors import ExportError, NotFound
from unfold.domain.enums import ArtifactKind
from unfold.evaluation.scripted import ScriptedLLM
from unfold.exports.pdf import PdfRenderer
from unfold.exports.service import export_key, run_export
from unfold.persistence.postgres.models import ArtifactRow
from unfold.ports import project_rate_key
from unfold.runtime import Runtime
from unfold.worker import run as worker_run

pytestmark = pytest.mark.integration

API = "/api/v1"
BACKEND = Path(__file__).resolve().parents[2]
Seeded = Any


@pytest.fixture(scope="module")
async def project(
    runtime: Runtime, analyzed_project: Callable[..., Awaitable[Seeded]]
) -> AsyncIterator[Seeded]:
    seeded = await analyzed_project()
    yield seeded
    await cleanup_project(runtime, seeded.project_id)


@pytest.fixture(scope="module")
async def renderer() -> AsyncIterator[PdfRenderer]:
    r = PdfRenderer()
    yield r
    await r.close()


async def _artifact(runtime: Runtime, artifact_id: str) -> ArtifactRow:
    async with runtime.db.session() as s:
        row = await s.get(ArtifactRow, uuid.UUID(artifact_id))
    assert row is not None
    return row


async def _chat_with_a_turn(client: httpx.AsyncClient, project_id: str) -> str:
    chat = (await client.post(f"{API}/projects/{project_id}/chats")).json()["id"]
    done = await client.post(f"{API}/chats/{chat}/messages", json={"text": "Where is split_text?"})
    assert done.status_code == 200, done.text
    return str(chat)


async def test_markdown_guide_export_lifecycle(
    open_api: Callable[..., Any],
    project: Seeded,
    runtime: Runtime,
    renderer: PdfRenderer,
    recording_queue: Any,
) -> None:
    async with open_api(queue=recording_queue) as client:
        client.cookies.set("unfold_session", project.token)
        base = f"{API}/projects/{project.project_id}"
        created = await client.post(f"{base}/exports/guide?format=md")
        assert created.status_code == 202, created.text
        art = created.json()["artifact_id"]
        assert recording_queue.exports == [art]

        # a second request while the first is pending is the same artifact, not a second render
        again = await client.post(f"{base}/exports/guide?format=md")
        assert again.json()["artifact_id"] == art

        pending = (await client.get(f"{API}/exports/{art}")).json()
        assert pending["status"] == "pending" and pending["download_url"] is None
        assert (await client.get(f"{API}/exports/{art}/download")).status_code == 409

        await run_export(runtime, art, renderer)
        await run_export(runtime, art, renderer)  # idempotent: a finished export is left alone

        ready = (await client.get(f"{API}/exports/{art}")).json()
        assert ready["status"] == "ready" and ready["kind"] == "guide_md" and ready["size_bytes"] > 200
        assert ready["download_url"] == f"{API}/exports/{art}/download"
        dl = await client.get(f"{API}/exports/{art}/download")
        assert dl.status_code == 200
        assert dl.headers["content-type"].startswith("text/markdown")
        assert 'attachment; filename="fastapi-rag-guide-md.md"' in dl.headers["content-disposition"]
        text = dl.text
        assert text.startswith("# fastapi_rag") and "## Overview" in text and "### Evidence" in text

        listed = (await client.get(f"{base}/artifacts")).json()
        assert [a["id"] for a in listed] == [art]


async def test_pdf_exports_render_for_guide_and_chat(
    open_api: Callable[..., Any], project: Seeded, runtime: Runtime, renderer: PdfRenderer
) -> None:
    llm = ScriptedLLM()
    async with open_api(llm=llm) as client:
        client.cookies.set("unfold_session", project.token)
        guide = (await client.post(f"{API}/projects/{project.project_id}/exports/guide")).json()[
            "artifact_id"
        ]
        chat_id = await _chat_with_a_turn(client, project.project_id)
        chat = (await client.post(f"{API}/chats/{chat_id}/exports")).json()["artifact_id"]
        for artifact_id in (guide, chat):
            await run_export(runtime, artifact_id, renderer)
            dl = await client.get(f"{API}/exports/{artifact_id}/download")
            assert dl.status_code == 200 and dl.headers["content-type"] == "application/pdf"
            assert dl.content.startswith(b"%PDF-") and len(dl.content) > 5_000
        assert (await _artifact(runtime, guide)).kind == "guide_pdf"
        assert (await _artifact(runtime, chat)).chat_session_id == uuid.UUID(chat_id)


async def test_a_worker_killed_mid_export_is_rerun_without_duplicates(
    open_api: Callable[..., Any], project: Seeded, runtime: Runtime, settings: Settings
) -> None:
    async with open_api() as client:
        client.cookies.set("unfold_session", project.token)
        art = (await client.post(f"{API}/projects/{project.project_id}/exports/guide")).json()["artifact_id"]
    key = export_key(project.project_id, art, ArtifactKind.GUIDE_PDF)
    env = {
        **os.environ,
        "APP_ENV": "test",
        "LOG_FORMAT": "console",
        "DATABASE_URL": settings.database_url,
        "REDIS_URL": settings.redis_url,
        "OBJECT_STORE_BUCKET": settings.object_store_bucket,
        "QDRANT_COLLECTION_PREFIX": settings.qdrant_collection_prefix,
    }

    async def spawn() -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            sys.executable, "-m", "unfold.worker.run", "export", art, cwd=BACKEND, env=env
        )

    # Hold the artifact row lock: the worker renders and stores the PDF, then blocks before marking it ready.
    async with runtime.db.transaction() as s:
        await s.get(ArtifactRow, uuid.UUID(art), with_for_update=True)
        first = await spawn()
        for _ in range(240):
            try:
                await runtime.objects.get_bytes(key)
                break
            except NotFound:
                await asyncio.sleep(0.5)
        else:
            first.kill()
            pytest.fail("the worker never stored the PDF")
        first.kill()  # SIGKILL equivalent: no cleanup code runs
        await first.wait()
    assert (await _artifact(runtime, art)).status == "pending"  # the kill left the row exactly as it was

    second = await spawn()
    assert await asyncio.wait_for(second.wait(), 300) == 0
    row = await _artifact(runtime, art)
    assert row.status == "ready" and row.object_key == key
    stored = await runtime.objects.get_bytes(key)
    assert row.size_bytes == len(stored) and stored.startswith(b"%PDF-")
    async with runtime.db.session() as s:
        count = (
            await s.execute(
                select(func.count()).select_from(ArtifactRow).where(ArtifactRow.id == uuid.UUID(art))
            )
        ).scalar_one()
    assert count == 1


class _FailingRenderer:
    def __init__(self, error: ExportError) -> None:
        self._error = error

    async def render(self, html: str, *, footer_label: str = "") -> bytes:
        raise self._error

    async def close(self) -> None:
        return None


async def test_retryable_render_failure_leaves_the_export_pending_for_a_rerun(
    open_api: Callable[..., Any], project: Seeded, runtime: Runtime, renderer: PdfRenderer
) -> None:
    async with open_api() as client:
        client.cookies.set("unfold_session", project.token)
        art = (await client.post(f"{API}/projects/{project.project_id}/exports/guide")).json()["artifact_id"]
    with pytest.raises(ExportError) as err:
        await run_export(runtime, art, _FailingRenderer(ExportError("chromium timed out")))  # type: ignore[arg-type]
    assert err.value.retryable
    assert (await _artifact(runtime, art)).status == "pending"
    await run_export(runtime, art, renderer)
    assert (await _artifact(runtime, art)).status == "ready"


async def test_a_permanent_failure_is_recorded_and_the_worker_exits_cleanly(
    runtime: Runtime, seed_project: Callable[..., Awaitable[Seeded]]
) -> None:
    unready = await seed_project()  # no guide content: nothing can be exported
    art = uuid.uuid4()
    try:
        async with runtime.db.transaction() as s:
            s.add(
                ArtifactRow(
                    id=art,
                    project_id=uuid.UUID(unready.project_id),
                    kind="guide_md",
                    status="pending",
                    content_type="text/markdown",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
        assert await worker_run._export(runtime, str(art)) == 0
        row = await _artifact(runtime, str(art))
        assert row.status == "failed" and "no content" in (row.error or "")
    finally:
        await cleanup_project(runtime, unready.project_id)


async def test_export_ownership_limits_and_expiry(
    open_api: Callable[..., Any], project: Seeded, runtime: Runtime
) -> None:
    await runtime.limiter.delete_prefix(project_rate_key("export", project.project_id))  # earlier tests
    async with open_api(max_exports_per_hour=2) as client:
        client.cookies.set("unfold_session", project.token)
        base = f"{API}/projects/{project.project_id}"
        a = (await client.post(f"{base}/exports/guide?format=md")).json()["artifact_id"]
        # distinct kinds, so each is a new artifact and counts against the per-project hourly limit
        await client.post(f"{base}/exports/guide?format=pdf")
        limited = await client.post(f"{base}/exports/guide?format=md")
        assert limited.json()["artifact_id"] == a  # still pending: de-duplicated before it is counted
        async with runtime.db.transaction() as s:
            row = await s.get(ArtifactRow, uuid.UUID(a))
            assert row is not None
            row.status = "ready"
            row.object_key = f"projects/{project.project_id}/exports/{a}.md"
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        expired = await client.get(f"{API}/exports/{a}/download")
        assert expired.status_code == 410 and expired.json()["error"]["code"] == "ARTIFACT_EXPIRED"
        over = await client.post(f"{base}/exports/guide?format=md")
        assert over.status_code == 429

        stranger = (await client.post(f"{API}/sessions")).cookies["unfold_session"]
        client.cookies.set("unfold_session", stranger)
        for method, url in (
            ("GET", f"{API}/exports/{a}"),
            ("GET", f"{API}/exports/{a}/download"),
            ("GET", f"{base}/artifacts"),
            ("POST", f"{base}/exports/guide"),
            ("GET", f"{API}/exports/{uuid.uuid4()}"),
            ("GET", f"{API}/exports/nope"),
        ):
            assert (await client.request(method, url)).status_code == 404, url


async def test_presigned_download_hands_back_a_working_url(
    open_api: Callable[..., Any], project: Seeded, runtime: Runtime, renderer: PdfRenderer
) -> None:
    async with open_api(export_download="presigned") as client:
        client.cookies.set("unfold_session", project.token)
        art = (await client.post(f"{API}/projects/{project.project_id}/exports/guide?format=md")).json()[
            "artifact_id"
        ]
        await run_export(runtime, art, renderer)
        resp = await client.get(f"{API}/exports/{art}/download", follow_redirects=False)
        assert resp.status_code == 302
        async with httpx.AsyncClient() as direct:  # no cookie, no API: the URL alone grants the read
            fetched = await direct.get(resp.headers["location"])
        assert fetched.status_code == 200 and fetched.text.startswith("# fastapi_rag")
        assert "attachment" in fetched.headers["content-disposition"]
