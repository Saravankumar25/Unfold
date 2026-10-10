"""Export job (LLD §19): persisted guide or chat -> PDF (Chromium) or Markdown -> `BlobStore` -> artifact `ready`.

`run_export` is the body of the one-shot worker task `export <artifact_id>`. It is idempotent: a finished artifact is
left alone, a rerun after a kill renders again and overwrites the same key, so no duplicates are possible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from unfold.core.errors import ErrorCode, ExportError, NotFound
from unfold.core.logging import get_logger
from unfold.diagrams.mermaid import to_mermaid
from unfold.diagrams.models import DiagramSpec
from unfold.domain.enums import ArtifactKind, ArtifactStatus
from unfold.domain.models import ClaimRecord, EvidenceRef
from unfold.domain.records import ArtifactRec, ProjectRec
from unfold.exports.markdown import render_chat_markdown, render_guide_markdown
from unfold.exports.models import DiagramExport, ExportChatMessage, ExportProject
from unfold.exports.render import render_chat_html, render_guide_html
from unfold.guide.models import GuidePlan, GuideSection, SectionContent
from unfold.ports import project_prefix
from unfold.runtime import Runtime

if TYPE_CHECKING:
    from unfold.exports.pdf import PdfRenderer

log = get_logger(__name__)


def export_key(project_id: str, artifact_id: str, kind: ArtifactKind) -> str:
    return f"{project_prefix(project_id)}exports/{artifact_id}.{kind.extension}"


def _project(row: ProjectRec, minutes: int | None = None) -> ExportProject:
    return ExportProject(
        name=row.detected_name or "Project",
        source=row.source_locator,
        input_type=row.input_type,
        commit_sha=row.commit_sha,
        technical_level=row.technical_level,
        generated_at=datetime.now(UTC),
        estimated_read_minutes=minutes,
    )


async def load_guide(
    rt: Runtime, project: ProjectRec
) -> tuple[ExportProject, list[GuideSection], dict[str, DiagramExport]]:
    stored_plan = await rt.control.get_plan(project.id)
    sections = await rt.control.sections(project.id)
    diagrams = await rt.control.diagrams(project.id)
    if stored_plan is None or not any(r.content for r in sections):
        raise ExportError(
            "The guide has no content to export.", code=ErrorCode.PROJECT_NOT_READY, retryable=False
        )
    plan = GuidePlan.model_validate(stored_plan)
    spec = {sec.key: sec for sec in plan.sections}
    out = [
        GuideSection(
            key=r.section_key,
            title=r.title,
            position=r.position,
            status=r.status,
            kind=spec[r.section_key].kind if r.section_key in spec else "core",
            target_minutes=spec[r.section_key].target_minutes if r.section_key in spec else 0.0,
            content=SectionContent.model_validate(r.content) if r.content else None,
            claims=[ClaimRecord.model_validate(c) for c in r.claims or []],
            evidence=dict(r.evidence_summary or {}),
            quality=dict(r.quality_scores or {}),
        )
        for r in sections
    ]
    exported = {
        d.id: DiagramExport(
            id=d.id, title=d.title, mermaid=d.mermaid or to_mermaid(DiagramSpec.model_validate(d.spec))
        )
        for d in diagrams
    }
    return _project(project, plan.estimated_read_minutes), out, exported


async def load_chat(
    rt: Runtime, project: ProjectRec, chat_id: str
) -> tuple[ExportProject, str | None, list[ExportChatMessage]]:
    chat = await rt.control.get_chat(chat_id)
    rows = await rt.control.chat_messages(chat_id) if chat is not None else []
    if chat is None or not rows:
        raise ExportError("The chat has no messages to export.", code=ErrorCode.NOT_FOUND, retryable=False)
    messages = [
        ExportChatMessage(
            role="assistant" if r.role == "assistant" else "user",
            text=str((r.content or {}).get("text", "")),
            mode=r.mode,
            evidence=[EvidenceRef.model_validate(e) for e in r.evidence or []],
            created_at=r.created_at,
        )
        for r in rows
    ]
    return _project(project), chat.title, messages


async def _render(rt: Runtime, art: ArtifactRec, project: ProjectRec, renderer: PdfRenderer) -> bytes:
    kind = ArtifactKind(art.kind)
    if kind.is_guide:
        meta, sections, diagrams = await load_guide(rt, project)
        if kind is ArtifactKind.GUIDE_MD:
            return render_guide_markdown(meta, sections, diagrams).encode()
        return await renderer.render(render_guide_html(meta, sections, diagrams), footer_label=meta.name)
    if art.chat_id is None:
        raise ExportError("The export names no chat.", code=ErrorCode.NOT_FOUND, retryable=False)
    meta, title, messages = await load_chat(rt, project, art.chat_id)
    if kind is ArtifactKind.CHAT_MD:
        return render_chat_markdown(meta, title, messages).encode()
    return await renderer.render(render_chat_html(meta, title, messages), footer_label=meta.name)


async def run_export(rt: Runtime, export_id: str, renderer: PdfRenderer) -> None:
    """Render and store one artifact. Raises `ExportError` (retryable or not) when it could not be produced."""
    art = await rt.control.get_artifact(export_id)
    project = await rt.control.get_project(art.project_id) if art else None
    if art is None or project is None or project.deleting_at is not None:
        log.info("export.skipped", export_id=export_id)  # the project is gone: nothing to produce
        return
    if art.status == ArtifactStatus.READY.value:
        return
    kind = ArtifactKind(art.kind)
    data = await _render(rt, art, project, renderer)
    key = export_key(project.id, export_id, kind)
    await rt.objects.put_bytes(key, data, kind.content_type)

    def ready(fresh: ArtifactRec) -> None:
        fresh.status = ArtifactStatus.READY.value
        fresh.object_key = key
        fresh.size_bytes = len(data)
        fresh.content_type = kind.content_type
        fresh.error = None

    try:
        await rt.control.update_artifact(export_id, ready)
        owner = await rt.control.get_project(project.id)
        # A project marked for deletion mid-render must not end up with a blob its cleanup already passed.
        removed = owner is None or owner.deleting_at is not None
    except NotFound:
        removed = True
    if removed:  # deleted while rendering: do not leave the blob behind
        await rt.objects.delete_prefix(key)
        return
    log.info("export.ready", export_id=export_id, kind=kind.value, bytes=len(data))
