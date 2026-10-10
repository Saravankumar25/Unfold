"""Pipeline end to end against the real stores (docker compose): fixture repo → guide with diagrams → resume → cleanup.

The only test double is the LLM: a scripted `LLMClient` that answers from the compiled context it is given (it cites
the evidence labels it sees). Parsing, indexing, graph, retrieval, persistence and verification are all real.
"""

from __future__ import annotations

import io
import re
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import func, select

from unfold.api.main import create_app
from unfold.cleanup.service import cleanup_project
from unfold.core.config import Settings
from unfold.core.errors import ErrorCode, ProviderCapacityError
from unfold.core.ids import new_session_token, token_hash
from unfold.guide.models import (
    DraftClaim,
    GuidePlan,
    GuideSectionPlan,
    ParagraphBlock,
    SectionContent,
    SectionDraft,
)
from unfold.llm.schemas import LLMRequest
from unfold.orchestration.pipeline import run_analysis
from unfold.persistence.postgres.models import (
    AnalysisJobRow,
    ChunkRow,
    DiagramRow,
    GuideSectionRow,
    JobCheckpointRow,
    ProjectRow,
    SessionRow,
    SummaryRow,
)
from unfold.ports import project_prefix
from unfold.runtime import Runtime

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[3] / "evals" / "fixtures" / "repos" / "fastapi_rag"
_LABEL = re.compile(r"\[(E\d+)\] (\S+?):(\d+)-(\d+)")


class ScriptedLLM:
    """Deterministic stand-in for the gateway: plans 4 sections, writes each from the evidence it was shown."""

    def __init__(self, *, fail_on_section: int | None = None) -> None:
        self.calls: list[str] = []
        self.sections_written = 0
        self.fail_on_section = fail_on_section

    async def generate(self, req: LLMRequest) -> Any:
        raise AssertionError("pipeline must use structured generation")

    def stream(self, req: LLMRequest) -> Any:
        raise AssertionError("pipeline must not stream")

    async def generate_structured(self, req: LLMRequest, schema: type[BaseModel]) -> Any:
        self.calls.append(req.purpose)
        if schema is GuidePlan:
            return GuidePlan(
                estimated_read_minutes=12,
                sections=[
                    GuideSectionPlan(
                        key=key,
                        title=title,
                        kind="backbone" if i < 2 else "core",
                        purpose=f"Explain {title.lower()} of this project",
                        target_minutes=3,
                        evidence_objectives=["app", "api", "routes", "retrieval"],
                        visual_needs=["architecture"] if i == 1 else [],
                    )
                    for i, (key, title) in enumerate(
                        [
                            ("overview", "Overview"),
                            ("architecture", "Architecture"),
                            ("data-flow", "Data flow"),
                            ("running", "Running it"),
                        ]
                    )
                ],
            )
        assert schema is SectionDraft
        if self.fail_on_section is not None and self.sections_written >= self.fail_on_section:
            raise ProviderCapacityError("scripted capacity failure", retryable=True)
        self.sections_written += 1
        text = req.messages[0].content
        labels = _LABEL.findall(text)
        assert labels, "the writer must always be given labelled evidence"
        label, path, _start, _end = labels[0]
        name = Path(path).stem
        return SectionDraft(
            content=SectionContent(
                summary=f"How {name} fits in.",
                blocks=[ParagraphBlock(text=f"The code in `{path}` is part of the project [{label}].")],
            ),
            claims=[
                DraftClaim(text=f"`{path}` is part of the project", kind="project_fact", evidence=[label]),
                DraftClaim(
                    text="There is a `NonexistentWidgetFactory` class", kind="project_fact", evidence=[label]
                ),
                DraftClaim(text="Nothing cites this", kind="project_fact", evidence=[]),
            ],
        )


def _zip_of(root: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, f"{root.name}/{path.relative_to(root).as_posix()}")
    return buf.getvalue()


async def _seed(
    rt: Runtime, archive: bytes, name: str = "fastapi_rag", *, token: str | None = None
) -> tuple[str, str]:
    """A project + queued job exactly as `POST /projects/zip` creates them (rows + uploaded blob)."""
    now = datetime.now(UTC)
    project_id = uuid.uuid4()
    key = f"projects/{project_id}/upload/source.zip"
    await rt.objects.put_bytes(key, archive, "application/zip")
    async with rt.db.transaction() as s:
        session = SessionRow(
            token_hash=token_hash(token) if token else uuid.uuid4().hex + uuid.uuid4().hex,
            created_at=now,
            last_active_at=now,
            expires_at=now + timedelta(hours=1),
            status="active",
        )
        s.add(session)
        await s.flush()
        s.add(
            ProjectRow(
                id=project_id,
                session_id=session.id,
                input_type="zip",
                source_locator=f"{name}.zip",
                detected_name=name,
                technical_level="intermediate",
                analysis_version="test",
                upload_object_key=key,
                expires_at=now + timedelta(hours=1),
            )
        )
        await s.flush()
        job = AnalysisJobRow(
            project_id=project_id, status="queued", current_stage="RECEIVED", stage_progress={}, attempt=0
        )
        s.add(job)
        await s.flush()
        return str(project_id), str(job.id)


async def _count(rt: Runtime, model: type[Any], project_id: str) -> int:
    async with rt.db.session() as s:
        return int(
            (
                await s.execute(
                    select(func.count()).select_from(model).where(model.project_id == uuid.UUID(project_id))
                )
            ).scalar_one()
        )


async def _teardown(rt: Runtime, project_id: str) -> None:
    await cleanup_project(rt, project_id)


async def test_fixture_repo_yields_guide_diagrams_and_cleans_up(runtime: Runtime) -> None:
    project_id, job_id = await _seed(runtime, _zip_of(FIXTURE))
    llm = ScriptedLLM()
    try:
        outcome = await run_analysis(runtime, llm, job_id)
        assert outcome.succeeded, outcome

        async with runtime.db.session() as s:
            job = await s.get(AnalysisJobRow, uuid.UUID(job_id))
            project = await s.get(ProjectRow, uuid.UUID(project_id))
            sections = (
                (
                    await s.execute(
                        select(GuideSectionRow)
                        .where(GuideSectionRow.project_id == uuid.UUID(project_id))
                        .order_by(GuideSectionRow.position)
                    )
                )
                .scalars()
                .all()
            )
            checkpoints = set(
                (
                    await s.execute(
                        select(JobCheckpointRow.name).where(JobCheckpointRow.job_id == uuid.UUID(job_id))
                    )
                ).scalars()
            )
        assert job is not None and project is not None
        assert (job.status, job.current_stage) == ("succeeded", "READY")
        assert {"analyze", "lexical", "subsystems", "plan", "sections", "verify", "visuals"} <= checkpoints
        assert project.ir_object_key and project.lexical_object_key
        assert (project.project_type or {}).get("categories")

        assert [s.section_key for s in sections] == ["overview", "architecture", "data-flow", "running"]
        for sec in sections:
            assert sec.content and sec.content["summary"]
            statuses = {c["status"] for c in sec.claims}
            assert "VERIFIED" in statuses  # cites a real label, names a real path
            assert "UNSUPPORTED" in statuses  # the uncited claim
            assert sec.quality_scores and sec.quality_scores["claims"] == 3
        arch = next(s for s in sections if s.section_key == "architecture")
        assert any(b["type"] == "diagram" for b in arch.content["blocks"])  # attached by the visuals stage

        assert await _count(runtime, ChunkRow, project_id) > 10
        assert await _count(runtime, DiagramRow, project_id) >= 3
        assert await _count(runtime, SummaryRow, project_id) >= 2

        # progress: ordered, durable, ends with `ready`
        events = await runtime.progress.read_after(project_id)
        seqs = [seq for seq, _ in events]
        assert seqs == sorted(seqs) and events[-1][1]["type"] == "ready"
        stages = [e["stage"] for _, e in events if e["type"] == "stage"]
        assert stages.index("INGESTING") < stages.index("BUILDING_IR") < stages.index("GENERATING_GUIDE")

        # a finished job is a no-op, not a rerun
        calls = len(llm.calls)
        again = await run_analysis(runtime, llm, job_id)
        assert again.succeeded and len(llm.calls) == calls
    finally:
        counts = await cleanup_project(runtime, project_id)

    assert counts["projects"] == 1
    assert await _count(runtime, ChunkRow, project_id) == 0
    assert await runtime.objects.delete_prefix(project_prefix(project_id)) == 0
    assert await runtime.progress.read_after(project_id) == []
    again = await cleanup_project(runtime, project_id)  # idempotent
    assert again["projects"] == 0


async def test_rerun_after_failure_resumes_without_duplicates(runtime: Runtime) -> None:
    project_id, job_id = await _seed(runtime, _zip_of(FIXTURE))
    try:
        flaky = ScriptedLLM(fail_on_section=2)
        first = await run_analysis(runtime, flaky, job_id)
        assert not first.succeeded and first.retry  # capacity errors are retryable: back to the queue

        async with runtime.db.session() as s:
            job = await s.get(AnalysisJobRow, uuid.UUID(job_id))
            done = set(
                (
                    await s.execute(
                        select(JobCheckpointRow.name).where(JobCheckpointRow.job_id == uuid.UUID(job_id))
                    )
                ).scalars()
            )
        assert job is not None and job.status == "queued"
        assert {"analyze", "plan"} <= done and "sections" not in done
        chunks_before = await _count(runtime, ChunkRow, project_id)

        healthy = ScriptedLLM()
        second = await run_analysis(runtime, healthy, job_id)
        assert second.succeeded
        assert healthy.calls.count("guide.plan") == 0  # plan checkpoint was reused
        assert healthy.calls.count("guide.section") == 2  # only the sections the first attempt did not finish
        assert await _count(runtime, ChunkRow, project_id) == chunks_before
        assert await _count(runtime, GuideSectionRow, project_id) == 4
    finally:
        await _teardown(runtime, project_id)


async def test_path_traversal_archive_is_rejected_by_the_worker(runtime: Runtime) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("proj/app.py", "print('ok')\n")
        zf.writestr("../../escape.py", "boom\n")
    project_id, job_id = await _seed(runtime, buf.getvalue(), name="traversal")
    try:
        outcome = await run_analysis(runtime, ScriptedLLM(), job_id)
        assert not outcome.succeeded and not outcome.retry
        async with runtime.db.session() as s:
            job = await s.get(AnalysisJobRow, uuid.UUID(job_id))
        assert job is not None
        assert job.status == "failed" and job.current_stage == "FAILED_SECURITY"
        assert job.error_code in {ErrorCode.UNSAFE_ARCHIVE.value, ErrorCode.ZIP_BOMB_DETECTED.value}
        assert await _count(runtime, ChunkRow, project_id) == 0
    finally:
        await _teardown(runtime, project_id)


async def test_zip_bomb_is_rejected_by_the_worker(runtime: Runtime) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("proj/zeros.txt", b"\0" * (24 * 1024 * 1024))  # ~24 KB on disk, 24 MB extracted
    project_id, job_id = await _seed(runtime, buf.getvalue(), name="bomb")
    try:
        outcome = await run_analysis(runtime, ScriptedLLM(), job_id)
        assert not outcome.succeeded and not outcome.retry
        async with runtime.db.session() as s:
            job = await s.get(AnalysisJobRow, uuid.UUID(job_id))
        assert job is not None and job.status == "failed"
        assert job.error_code == ErrorCode.ZIP_BOMB_DETECTED.value
    finally:
        await _teardown(runtime, project_id)


class _NullQueue:
    async def enqueue_analysis(self, job_id: str, attempt: int, *, defer_s: float | None = None) -> None:
        return None

    async def enqueue_project_cleanup(self, project_id: str) -> None:
        return None

    async def aclose(self) -> None:
        return None


async def test_read_routes_serve_the_persisted_guide(runtime: Runtime, settings: Settings) -> None:
    token = new_session_token()
    project_id, job_id = await _seed(runtime, _zip_of(FIXTURE), token=token)
    try:
        assert (await run_analysis(runtime, ScriptedLLM(), job_id)).succeeded
        app = create_app(settings, queue=_NullQueue())
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test", cookies={"unfold_session": token}
            ) as c:
                base = f"/api/v1/projects/{project_id}"
                guide = (await c.get(f"{base}/guide")).json()
                assert [s["key"] for s in guide["sections"]] == [
                    "overview",
                    "architecture",
                    "data-flow",
                    "running",
                ]
                assert guide["estimated_read_minutes"] > 0 and guide["sections"][0]["summary"]

                section = (await c.get(f"{base}/guide/sections/architecture")).json()
                assert section["status"] in ("ready", "degraded")
                assert section["evidence"] and section["claims"]
                assert {b["type"] for b in section["content"]["blocks"]} >= {"paragraph", "diagram"}
                diagram_id = next(
                    b["diagram_id"] for b in section["content"]["blocks"] if b["type"] == "diagram"
                )

                chunk_id = next(iter(section["evidence"].values()))["chunk_id"]
                evidence = (await c.get(f"{base}/evidence/{chunk_id}")).json()
                assert evidence["path"] and evidence["text"] and evidence["trust_label"]

                diagram = (await c.get(f"{base}/diagrams/{diagram_id}")).json()
                assert diagram["mermaid"].strip() and diagram["nodes"]

                arch = (await c.get(f"{base}/architecture")).json()
                assert arch["subsystems"] and arch["counts"]["files"] > 10 and arch["diagram"]["nodes"]

                assert (await c.get(f"{base}/guide/sections/nope")).status_code == 404
                assert (await c.get(f"{base}/evidence/0000")).status_code == 404

                # another session cannot read it (404, not 403)
                stranger = await c.post("/api/v1/sessions")
                assert stranger.status_code == 201
                bare = httpx.AsyncClient(
                    transport=transport,
                    base_url="http://test",
                    cookies={"unfold_session": stranger.cookies["unfold_session"]},
                )
                assert (await bare.get(f"{base}/guide")).status_code == 404
                await bare.aclose()
    finally:
        await _teardown(runtime, project_id)


async def test_a_job_with_a_live_heartbeat_is_not_run_twice(runtime: Runtime) -> None:
    project_id, job_id = await _seed(runtime, _zip_of(FIXTURE))
    try:
        async with runtime.db.transaction() as s:
            job = await s.get(AnalysisJobRow, uuid.UUID(job_id))
            assert job is not None
            job.status, job.attempt, job.heartbeat_at = "running", 1, datetime.now(UTC)
        llm = ScriptedLLM()
        outcome = await run_analysis(runtime, llm, job_id)
        assert not outcome.succeeded and not outcome.retry
        assert llm.calls == [] and await _count(runtime, ChunkRow, project_id) == 0
        async with runtime.db.session() as s:
            job = await s.get(AnalysisJobRow, uuid.UUID(job_id))
        assert job is not None and job.attempt == 1  # untouched: the owner is still working
    finally:
        await _teardown(runtime, project_id)
