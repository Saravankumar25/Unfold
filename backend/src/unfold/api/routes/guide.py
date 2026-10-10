"""Read routes for a project's results: guide, sections, evidence, architecture, diagrams (LLD §20).

Every route is owner-scoped (`owned_project` → 404 on foreign ids). Content is served from what the worker
persisted; nothing here calls a model. The one write is `POST .../sections/{key}/regenerate`, which only records
a job and asks the queue to run it.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import Select, select

from unfold.api.deps import RuntimeDep, SessionDep, owned_project
from unfold.api.routes.projects import latest_job
from unfold.api.schemas import (
    ArchitectureOut,
    DiagramOut,
    EvidenceOut,
    GuideOut,
    GuideSectionOut,
    GuideSectionSummary,
    RegenerateCreated,
    SubsystemOut,
)
from unfold.core.errors import Conflict, NotFound, ProjectNotReady, RateLimited, UnfoldError
from unfold.core.logging import get_logger
from unfold.diagrams.models import DiagramSpec
from unfold.domain.enums import JobStage, JobStatus
from unfold.domain.models import ClaimRecord, EvidenceRef
from unfold.guide.models import GuidePlan, SectionContent
from unfold.indexing.store import ChunkRepository
from unfold.persistence.postgres.models import (
    AnalysisJobRow,
    DiagramRow,
    GuidePlanRow,
    GuideSectionRow,
    ProjectRow,
    SummaryRow,
)
from unfold.ports import JobQueue, project_rate_key
from unfold.project_ir.snapshot import load_ir
from unfold.runtime import Runtime
from unfold.security.injection import scan, trust_label
from unfold.security.secrets import redact_text

router = APIRouter(prefix="/projects", tags=["guide"])
log = get_logger(__name__)


async def _plan(rt: Runtime, project: ProjectRow) -> GuidePlan:
    async with rt.db.session() as s:
        row = await s.get(GuidePlanRow, project.id)
    if row is None:
        raise ProjectNotReady("The guide is not ready yet.")
    return GuidePlan.model_validate(row.plan)


def _summary(row: GuideSectionRow, kind: str, minutes: float) -> GuideSectionSummary:
    content = SectionContent.model_validate(row.content) if row.content else None
    return GuideSectionSummary(
        key=row.section_key,
        title=row.title,
        position=row.position,
        status=row.status,
        kind=kind,
        target_minutes=minutes,
        summary=content.summary if content else None,
    )


@router.get("/{project_id}/guide", response_model=GuideOut)
async def get_guide(project_id: str, rt: RuntimeDep, session: SessionDep) -> GuideOut:
    project = await owned_project(rt, session, project_id)
    plan = await _plan(rt, project)
    async with rt.db.session() as s:
        rows = (
            (
                await s.execute(
                    select(GuideSectionRow)
                    .where(GuideSectionRow.project_id == project.id)
                    .order_by(GuideSectionRow.position)
                )
            )
            .scalars()
            .all()
        )
    spec = {sec.key: sec for sec in plan.sections}
    sections = [
        _summary(
            r,
            spec[r.section_key].kind if r.section_key in spec else "core",
            spec[r.section_key].target_minutes if r.section_key in spec else 0.0,
        )
        for r in rows
    ]
    return GuideOut(
        project_id=str(project.id), estimated_read_minutes=plan.estimated_read_minutes, sections=sections
    )


@router.get("/{project_id}/guide/sections/{key}", response_model=GuideSectionOut)
async def get_section(project_id: str, key: str, rt: RuntimeDep, session: SessionDep) -> GuideSectionOut:
    project = await owned_project(rt, session, project_id)
    plan = await _plan(rt, project)
    async with rt.db.session() as s:
        row = (
            await s.execute(
                select(GuideSectionRow).where(
                    GuideSectionRow.project_id == project.id, GuideSectionRow.section_key == key
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise NotFound("Section not found.")
    spec = next((sec for sec in plan.sections if sec.key == key), None)
    evidence = {
        label: EvidenceRef(
            chunk_id=str(ref["chunk_id"]),
            path=str(ref["path"]),
            start_line=int(ref["start_line"]),
            end_line=int(ref["end_line"]),
            symbol=ref.get("symbol"),
        )
        for label, ref in (row.evidence_summary or {}).items()
    }
    return GuideSectionOut(
        key=row.section_key,
        title=row.title,
        position=row.position,
        status=row.status,
        kind=spec.kind if spec else "core",
        target_minutes=spec.target_minutes if spec else 0.0,
        content=SectionContent.model_validate(row.content) if row.content else None,
        claims=[ClaimRecord.model_validate(c) for c in row.claims or []],
        evidence=evidence,
        quality=dict(row.quality_scores or {}),
    )


@router.get("/{project_id}/evidence/{chunk_id}", response_model=EvidenceOut)
async def get_evidence(project_id: str, chunk_id: str, rt: RuntimeDep, session: SessionDep) -> EvidenceOut:
    project = await owned_project(rt, session, project_id)
    found = await ChunkRepository(rt.db).get_many(str(project.id), [chunk_id])
    if not found:
        raise NotFound("Evidence not found.")
    chunk = found[0]
    return EvidenceOut(
        chunk_id=chunk.id,
        path=chunk.path,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        symbol=chunk.symbol_name,
        language=chunk.language,
        text=redact_text(chunk.text),
        trust_label=trust_label(scan(chunk.text)),
    )


def _diagram_out(row: DiagramRow) -> DiagramOut:
    spec = DiagramSpec.model_validate(row.spec)
    return DiagramOut(**spec.model_dump(), mermaid=row.mermaid, origin=row.origin)


@router.get("/{project_id}/diagrams/{diagram_id}", response_model=DiagramOut)
async def get_diagram(project_id: str, diagram_id: str, rt: RuntimeDep, session: SessionDep) -> DiagramOut:
    project = await owned_project(rt, session, project_id)
    async with rt.db.session() as s:
        row = await s.get(DiagramRow, diagram_id)
    if row is None or row.project_id != project.id:
        raise NotFound("Diagram not found.")
    return _diagram_out(row)


@router.get("/{project_id}/architecture", response_model=ArchitectureOut)
async def get_architecture(project_id: str, rt: RuntimeDep, session: SessionDep) -> ArchitectureOut:
    project = await owned_project(rt, session, project_id)
    if not project.ir_object_key:
        raise ProjectNotReady("The guide is not ready yet.")
    ir = await load_ir(rt.objects, project.ir_object_key)
    async with rt.db.session() as s:
        subsystems = (
            (
                await s.execute(
                    select(SummaryRow).where(
                        SummaryRow.project_id == project.id, SummaryRow.level == "subsystem"
                    )
                )
            )
            .scalars()
            .all()
        )
        diagram = (
            (
                await s.execute(
                    select(DiagramRow).where(
                        DiagramRow.project_id == project.id, DiagramRow.kind == "architecture"
                    )
                )
            )
            .scalars()
            .first()
        )
    counts: dict[str, int] = {
        "files": len(ir.files),
        "symbols": len(ir.symbols),
        "endpoints": len(ir.endpoints),
        "schemas": len(ir.schemas),
        "services": len(ir.services),
        "datastores": len(ir.data_stores),
        "nodes": len(ir.entity_index()),
        "relationships": len(ir.relationships),
    }
    languages: list[dict[str, Any]] = [lang.model_dump() for lang in ir.languages]
    return ArchitectureOut(
        subsystems=[
            SubsystemOut(
                key=r.key,
                title=r.title,
                summary=r.semantic,
                members=[str(m) for m in r.members or []],
            )
            for r in sorted(subsystems, key=lambda r: (-len(r.members or []), r.key))
        ],
        counts=counts,
        languages=languages,
        frameworks=sorted({f.name for f in ir.frameworks}),
        diagram=_diagram_out(diagram) if diagram else None,
    )


def _running_regeneration(project_id: uuid.UUID) -> Select[Any]:
    return (
        select(AnalysisJobRow)
        .where(
            AnalysisJobRow.project_id == project_id,
            AnalysisJobRow.kind == "regenerate",
            AnalysisJobRow.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
        )
        .limit(1)
    )


def _same_chapter(job: AnalysisJobRow, key: str) -> RegenerateCreated:
    if job.section_key != key:  # one regeneration at a time per project: two would race on the same stores
        raise Conflict(f"The chapter '{job.section_key}' is being regenerated; try again when it is done.")
    return RegenerateCreated(job_id=str(job.id), section_key=key)


@router.post(
    "/{project_id}/sections/{section_key}/regenerate", status_code=202, response_model=RegenerateCreated
)
async def regenerate_section(
    project_id: str, section_key: str, request: Request, rt: RuntimeDep, session: SessionDep
) -> RegenerateCreated:
    """Redo research, writing and verification of one chapter in a worker; the others are left as they are.

    Idempotent while one is running (the same job is returned). Rate-limited per project: each call spends model
    calls from a job budget of its own."""
    project = await owned_project(rt, session, project_id)
    plan = await _plan(rt, project)
    if section_key not in {s.key for s in plan.sections}:
        raise NotFound("Section not found.")
    if (await latest_job(rt, project.id)).status != JobStatus.SUCCEEDED.value:
        raise ProjectNotReady("The guide is not ready yet.")
    async with rt.db.session() as s:
        running = (await s.execute(_running_regeneration(project.id))).scalar_one_or_none()
    if running is not None:
        return _same_chapter(running, section_key)
    cfg = rt.settings
    # the limiter is a network call: keep it outside the transaction (a slot lost to a later failure is cheap)
    if not await rt.limiter.hit(
        project_rate_key("regenerate", project.id), cfg.max_regenerations_per_hour, 3600
    ):
        raise RateLimited(
            f"At most {cfg.max_regenerations_per_hour} chapter regenerations per project per hour."
        )
    async with rt.db.transaction() as s:
        # the project row lock serialises concurrent requests and refuses a project deleted since the lookup
        alive = (
            await s.execute(
                select(ProjectRow.id)
                .where(ProjectRow.id == project.id, ProjectRow.deleting_at.is_(None))
                .with_for_update()
            )
        ).first()
        if alive is None:
            raise NotFound("Project not found.")
        raced = (await s.execute(_running_regeneration(project.id))).scalar_one_or_none()
        if raced is not None:
            return _same_chapter(raced, section_key)
        job = AnalysisJobRow(
            project_id=project.id,
            kind="regenerate",
            section_key=section_key,
            status=JobStatus.QUEUED.value,
            current_stage=JobStage.RECEIVED.value,
            stage_progress={},
            attempt=0,
        )
        s.add(job)
        await s.flush()
        job_id = str(job.id)
    queue: JobQueue = request.app.state.queue
    try:
        await queue.enqueue_analysis(job_id, 1)
    except UnfoldError as exc:  # committed and queued: the sweeper starts it
        log.warning("regenerate.enqueue_failed", job_id=job_id, code=exc.code.value)
    return RegenerateCreated(job_id=job_id, section_key=section_key)
