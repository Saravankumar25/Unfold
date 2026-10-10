"""The analysis pipeline (LLD §14): one job, run to completion by a one-shot worker process.

acquire → inventory → analyse → IR → graph → lexical + dense index → subsystems → plan → write each section →
verify claims → diagrams → finalize. Plain async functions; no framework owns state. Each stage group persists its
output and then writes a `job_checkpoints` row, so a rerun of the same job skips finished groups. Success is
reported only after everything is persisted.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from unfold import ANALYSIS_VERSION
from unfold.core.errors import ErrorCode, InputError, UnfoldError
from unfold.core.ids import diagram_id
from unfold.core.logging import bind_context, get_logger
from unfold.diagrams.builders import build_all
from unfold.diagrams.inputs import SubsystemView
from unfold.diagrams.mermaid import to_mermaid
from unfold.diagrams.models import DiagramSpec
from unfold.domain.enums import JobStage, SectionStatus
from unfold.domain.models import ClaimRecord
from unfold.domain.records import DiagramRec, ProjectRec, SectionRec, SummaryRec
from unfold.graph.builder import GraphBuilder
from unfold.graph.communities import detect as detect_subsystems
from unfold.graph.communities import entity_subsystems
from unfold.guide.generate import (
    SectionEvidenceMissing,
    grade_claims,
    plan_guide,
    project_facts,
    write_section,
)
from unfold.guide.models import PLANNER_VERSION, DiagramBlock, GuidePlan, SectionContent
from unfold.indexing.chunking import chunk_project
from unfold.indexing.dense import dense_store_for
from unfold.indexing.lexical import BM25Index
from unfold.ingestion.service import AcquiredSource, acquire_github, acquire_zip
from unfold.llm.schemas import LLMClient
from unfold.observability.tracing import span
from unfold.orchestration.constants import ALIVE_WITHIN, HEARTBEAT_SECONDS, RETRY_EXIT_CODE
from unfold.orchestration.progress import ProgressReporter
from unfold.project_ir.builder import build_ir, validate_ir
from unfold.project_ir.schema import ProjectIdentity, ProjectIR
from unfold.project_ir.snapshot import load_ir, save_ir
from unfold.repo_intel.service import analyze_texts, scan_repository
from unfold.retrieval.hybrid import HybridRetriever
from unfold.runtime import Runtime

log = get_logger(__name__)

__all__ = ["ALIVE_WITHIN", "HEARTBEAT_SECONDS", "RETRY_EXIT_CODE", "RunOutcome", "run_analysis"]

_VISUAL_KINDS = {
    "architecture",
    "module_dependencies",
    "data_model",
    "request_flow",
    "deployment",
    "ai_pipeline",
}


@dataclass(frozen=True)
class RunOutcome:
    succeeded: bool
    retry: bool


async def run_analysis(rt: Runtime, llm: LLMClient, job_id: str) -> RunOutcome:
    """Run (or resume) one analysis job. Never raises for job-level failures: they are recorded on the job."""
    job = await rt.control.get_job(job_id)
    project = await rt.control.get_project(job.project_id) if job else None
    if job is None or project is None:
        raise UnfoldError("Analysis job not found.", code=ErrorCode.NOT_FOUND, http_status=404)
    if job.status == "succeeded":
        return RunOutcome(succeeded=True, retry=False)

    pid = project.id
    bind_context(project_id=pid, job_id=job_id)
    reporter = ProgressReporter(rt.control, rt.progress, pid, job_id)
    attempt = await reporter.start(alive_within=ALIVE_WITHIN)
    if attempt is None:
        log.warning("job.not_claimed", job_id=job_id)  # finished, or another worker owns it
        return RunOutcome(succeeded=False, retry=False)
    settings = rt.settings
    if attempt > settings.max_job_attempts:
        err = UnfoldError(
            f"The job failed {settings.max_job_attempts} times and was stopped.",
            code=ErrorCode.ANALYSIS_STAGE_FAILED,
            retryable=False,
        )
        await reporter.fail(err, will_retry=False)
        return RunOutcome(succeeded=False, retry=False)

    beat = asyncio.create_task(_heartbeat(reporter))
    limit = asyncio.timeout(settings.job_timeout_seconds)
    try:
        async with limit:
            await _Run(rt, llm, project, job_id, reporter).execute()
        await reporter.succeed()
        return RunOutcome(succeeded=True, retry=False)
    except TimeoutError:
        if limit.expired():
            err = UnfoldError(
                "The analysis exceeded its time limit.", code=ErrorCode.ANALYSIS_STAGE_FAILED, retryable=True
            )
        else:
            err = UnfoldError(
                "A network call timed out.", code=ErrorCode.PROVIDER_UNAVAILABLE, retryable=True
            )
    except UnfoldError as exc:
        err = exc
    except Exception as exc:
        log.exception("job.crashed", error_type=type(exc).__name__)
        err = UnfoldError(
            f"Unexpected error ({type(exc).__name__}).", code=ErrorCode.INTERNAL_ERROR, retryable=False
        )
    finally:
        beat.cancel()
    will_retry = err.retryable and attempt < settings.max_job_attempts
    await reporter.fail(err, will_retry=will_retry)
    return RunOutcome(succeeded=False, retry=will_retry)


async def _heartbeat(reporter: ProgressReporter, every_s: float = HEARTBEAT_SECONDS) -> None:
    """Keep `heartbeat_at` fresh through long stages so recovery never mistakes a busy worker for a dead one."""
    while True:
        await asyncio.sleep(every_s)
        try:
            await reporter.heartbeat()
        except Exception as exc:
            log.warning("job.heartbeat_failed", error_type=type(exc).__name__)


class _Run:
    def __init__(
        self, rt: Runtime, llm: LLMClient, project: ProjectRec, job_id: str, reporter: ProgressReporter
    ) -> None:
        self.rt = rt
        self.llm = llm
        self.project = project
        self.pid = project.id
        self.job_id = job_id
        self.reporter = reporter
        self.chunks = rt.chunks
        self.done: set[str] = set()
        self.ir: ProjectIR | None = None

    # --- driver --------------------------------------------------------------------------------------------

    async def execute(self) -> None:
        self.done = await self.rt.control.checkpoints(self.job_id)
        steps: list[tuple[str, Callable[[], Awaitable[dict[str, Any] | None]]]] = [
            ("analyze", self._analyze),
            ("graph", self._graph),
            ("lexical", self._lexical),
            ("dense", self._dense),
            ("subsystems", self._subsystems),
            ("plan", self._plan),
            ("sections", self._sections),
            ("verify", self._verify),
            ("visuals", self._visuals),
        ]
        for name, step in steps:
            await self._ensure_project_exists()
            if name in self.done:
                log.info("job.stage_skipped", group=name)
                continue
            with span(f"pipeline.{name}", project_id=self.pid):
                data = await step()
            await self._checkpoint(name, data or {})
        await self.reporter.stage(JobStage.FINALIZING)

    async def _ensure_project_exists(self) -> None:
        """Stop when the project was removed mid-run, so cleanup's work is not recreated behind it."""
        if await self.rt.control.get_project(self.pid) is None:
            raise UnfoldError("The project was removed.", code=ErrorCode.NOT_FOUND, retryable=False)

    async def _checkpoint(self, name: str, data: dict[str, Any]) -> None:
        await self.rt.control.put_checkpoint(self.job_id, name, data)
        self.done.add(name)

    async def _project(self) -> ProjectRec:
        row = await self.rt.control.get_project(self.pid)
        assert row is not None
        return row

    async def _load_ir(self) -> ProjectIR:
        if self.ir is None:
            key = (await self._project()).ir_object_key
            if not key:
                raise UnfoldError("The project model is missing.", code=ErrorCode.ANALYSIS_STAGE_FAILED)
            self.ir = await load_ir(self.rt.objects, key)
        return self.ir

    async def _optional(self, component: str, work: Callable[[], Awaitable[None]]) -> None:
        """Best-effort component: failure is recorded as `degraded` on the job, retrieval adapts."""
        try:
            await work()
        except Exception as exc:
            log.warning(
                "job.degraded", component=component, error_type=type(exc).__name__, error=str(exc)[:200]
            )
            await self.reporter.degraded(component)

    # --- stage groups --------------------------------------------------------------------------------------

    async def _analyze(self) -> dict[str, Any]:
        rt, p = self.rt, self.project
        rep = self.reporter
        await rep.stage(JobStage.VALIDATING_INPUT)
        with tempfile.TemporaryDirectory(prefix="unfold-job-", dir=rt.settings.work_dir or None) as tmp:
            workdir = Path(tmp)
            await rep.stage(JobStage.INGESTING)
            src: AcquiredSource
            if p.input_type == "github":
                src = await acquire_github(rt.settings, p.source_locator or "", workdir)
            else:
                src = await acquire_zip(
                    rt.settings, rt.objects, p.upload_object_key, p.detected_name or "project", workdir
                )
            await rep.stage(JobStage.SECURITY_SCANNING, f"{src.report.file_count} files extracted")
            await rep.stage(JobStage.INVENTORY)
            limit = rt.settings.max_single_file_bytes
            inventory, texts = await asyncio.to_thread(scan_repository, src.root, max_single_file_bytes=limit)
            if not inventory.files:
                raise InputError("The project contains no readable files.", code=ErrorCode.EMPTY_REPOSITORY)
            await rep.stage(JobStage.STRUCTURAL_ANALYSIS)
            analysis = await asyncio.to_thread(analyze_texts, self.pid, inventory, texts)
            await rep.stage(JobStage.BUILDING_IR)
            identity = ProjectIdentity(
                project_id=self.pid,
                name=src.name or p.detected_name or "project",
                input_type="github" if p.input_type == "github" else "zip",
                source_locator=p.source_locator,
                commit_sha=src.commit_sha,
                default_branch=src.default_branch,
                description=src.description or analysis.description,
            )
            ir = await asyncio.to_thread(
                build_ir, identity, inventory, analysis, analysis_version=ANALYSIS_VERSION
            )
            problems = validate_ir(ir)
            if problems:
                raise UnfoldError(
                    "The project model failed validation: " + "; ".join(problems[:3]),
                    code=ErrorCode.ANALYSIS_STAGE_FAILED,
                )
            key = await save_ir(rt.objects, ir)
            chunks = await asyncio.to_thread(chunk_project, ir, texts)
            await self.chunks.replace_project_chunks(self.pid, chunks)
        project_type = {
            "categories": ir.project.categories,
            "frameworks": sorted({f.name for f in ir.frameworks}),
            "languages": [lang.name for lang in ir.languages],
        }

        def record_analysis(row: ProjectRec) -> None:
            row.ir_object_key = key
            row.commit_sha = src.commit_sha
            row.default_branch = src.default_branch
            row.detected_name = identity.name
            row.project_type = project_type
            row.stats = {**ir.stats, "chunks": len(chunks)}

        await rt.control.update_project(self.pid, record_analysis)
        self.ir = ir
        return {"ir_key": key, "chunks": len(chunks)}

    async def _graph(self) -> None:
        await self.reporter.stage(JobStage.BUILDING_GRAPH)
        backend = self.rt.settings.graph_backend
        if backend != "neo4j":  # memory: derived from the IR at query time; off: the recorded degradation
            if backend == "off":
                await self.reporter.degraded("graph")
            return
        ir = await self._load_ir()

        async def work() -> None:
            builder = GraphBuilder(self.rt.neo4j)
            await builder.ensure_schema()
            await builder.write(ir)

        await self._optional("graph", work)

    async def _lexical(self) -> None:
        await self.reporter.stage(JobStage.INDEXING_LEXICAL)
        chunks = await self.chunks.list_project(self.pid)
        artifact = await BM25Index.build_async(chunks)
        key = await artifact.save(self.rt.objects, self.pid, ANALYSIS_VERSION)

        def record_lexical(row: ProjectRec) -> None:
            row.lexical_object_key = key

        await self.rt.control.update_project(self.pid, record_lexical)

    async def _dense(self) -> None:
        await self.reporter.stage(JobStage.INDEXING_DENSE)
        chunks = await self.chunks.list_project(self.pid)

        async def work() -> None:
            index = dense_store_for(self.rt)
            await index.upsert(chunks)

        await self._optional("dense", work)

    async def _subsystems(self) -> None:
        rep = self.reporter
        ir = await self._load_ir()
        await rep.stage(JobStage.SUMMARIZING_HIERARCHY)
        project_summary = SummaryRec(
            project_id=self.pid,
            level="project",
            key=self.pid,
            title=ir.project.name,
            facts={"facts": project_facts(ir)},
            semantic=ir.project.description,
            members=[],
            prompt_version="deterministic",
        )
        await rep.stage(JobStage.DISCOVERING_SUBSYSTEMS)
        candidates = await asyncio.to_thread(detect_subsystems, ir)
        path_of = {f.id: f.path for f in ir.files}
        rows = [project_summary]
        for c in candidates:
            members = sorted(path_of[i] for i in c.member_file_ids if i in path_of)
            rows.append(
                SummaryRec(
                    project_id=self.pid,
                    level="subsystem",
                    key=c.id,
                    title=c.label_hint or c.id,
                    facts={"cohesion": c.cohesion, "key_files": [path_of.get(k, k) for k in c.key_files]},
                    members=members,
                    prompt_version="deterministic",
                )
            )
        await self.rt.control.replace_summaries(self.pid, rows)

        path_by_id = {f.id: f.path for f in ir.files}
        file_sub = {path_by_id[i]: c.id for c in candidates for i in c.member_file_ids if i in path_by_id}
        chunk_map = {
            c.id: file_sub[c.path] for c in await self.chunks.list_project(self.pid) if c.path in file_sub
        }
        await self.chunks.set_subsystems(self.pid, chunk_map)

        async def graph_work() -> None:
            await GraphBuilder(self.rt.neo4j).set_subsystem(self.pid, entity_subsystems(ir, candidates))

        async def dense_work() -> None:
            await dense_store_for(self.rt).set_subsystems(self.pid, chunk_map)

        if self.rt.settings.graph_backend == "neo4j" and "graph" not in await self._degraded():
            await self._optional("graph", graph_work)
        if "dense" not in await self._degraded():
            await self._optional("dense", dense_work)

    async def _degraded(self) -> set[str]:
        row = await self.rt.control.get_job(self.job_id)
        return set(((row.stage_progress if row else {}) or {}).get("degraded", []))

    async def _subsystem_views(self) -> list[SubsystemView]:
        rows = await self.rt.control.summaries(self.pid, "subsystem")
        return [
            SubsystemView(key=r.key, title=r.title, member_file_paths=[str(m) for m in r.members])
            for r in rows
        ]

    async def _plan(self) -> dict[str, Any]:
        await self.reporter.stage(JobStage.PLANNING_GUIDE)
        ir = await self._load_ir()
        views = await self._subsystem_views()
        facts = project_facts(ir, [(v.title, v.member_file_paths) for v in views])
        p = await self._project()
        plan = await plan_guide(
            self.llm,
            ir,
            facts,
            technical_level=p.technical_level,
            instruction=p.user_instruction,
            job_id=self.job_id,
            project_id=self.pid,
        )
        await self.rt.control.replace_plan(
            self.pid,
            plan.model_dump(mode="json"),
            PLANNER_VERSION,
            [
                SectionRec(
                    project_id=self.pid,
                    section_key=sec.key,
                    title=sec.title,
                    position=i,
                    status=SectionStatus.PENDING.value,
                    claims=[],
                )
                for i, sec in enumerate(plan.sections)
            ],
        )
        return {"sections": len(plan.sections)}

    async def _stored_plan(self) -> GuidePlan:
        stored = await self.rt.control.get_plan(self.pid)
        if stored is None:
            raise UnfoldError("The guide plan is missing.", code=ErrorCode.GENERATION_FAILED)
        return GuidePlan.model_validate(stored)

    async def _sections(self) -> dict[str, Any]:
        await self.reporter.stage(JobStage.GENERATING_GUIDE)
        ir = await self._load_ir()
        plan = await self._stored_plan()
        p = await self._project()
        views = await self._subsystem_views()
        facts = project_facts(ir, [(v.title, v.member_file_paths) for v in views])
        retriever = HybridRetriever.for_project(self.rt, self.pid, ir, p.lexical_object_key)
        gate = asyncio.Semaphore(max(1, self.rt.settings.section_concurrency))
        written = 0
        missing: list[SectionEvidenceMissing] = []

        async def one(sec_key: str) -> None:
            nonlocal written
            spec = next(s for s in plan.sections if s.key == sec_key)
            existing = await self.rt.control.get_section(self.pid, sec_key)
            assert existing is not None
            if existing.content:
                return  # written by an earlier attempt; resume skips it
            try:
                async with gate:
                    draft = await write_section(
                        self.llm,
                        retriever,
                        spec,
                        facts,
                        technical_level=p.technical_level,
                        instruction=p.user_instruction,
                        job_id=self.job_id,
                        project_id=self.pid,
                    )
            except SectionEvidenceMissing as exc:
                missing.append(exc)
                reason = exc.message

                def mark_failed(row: SectionRec) -> None:
                    row.status = SectionStatus.FAILED.value
                    row.quality_scores = {"reason": reason}

                await self.rt.control.update_section(self.pid, sec_key, mark_failed)
                return

            def store_draft(row: SectionRec) -> None:
                row.content = draft.content.model_dump(mode="json")
                row.claims = [c.model_dump(mode="json") for c in draft.claims]
                row.evidence_summary = draft.evidence
                row.status = SectionStatus.GENERATING.value  # `_verify` grades it, then it is ready
                row.version = existing.version + 1

            await self.rt.control.update_section(self.pid, sec_key, store_draft)
            written += 1
            await self.reporter.heartbeat()

        results = await asyncio.gather(*(one(s.key) for s in plan.sections), return_exceptions=True)
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            first = failures[0]
            if isinstance(first, UnfoldError):
                raise first
            raise UnfoldError(
                f"Section generation failed ({type(first).__name__}).",
                code=ErrorCode.GENERATION_FAILED,
                retryable=True,
            ) from first
        if missing:
            if len(missing) == len(plan.sections):
                raise missing[0]  # nothing to show: fail the job as before
            await self.reporter.degraded("guide_sections")
        return {"written": written, "failed": len(missing)}

    async def _verify(self) -> dict[str, Any]:
        await self.reporter.stage(JobStage.VERIFYING_GUIDE)
        graded = 0
        rows = await self.rt.control.sections(self.pid)
        for row in rows:
            if row.status == SectionStatus.FAILED.value:
                continue  # no content to grade; it stays failed
            claims = [ClaimRecord.model_validate(c) for c in row.claims or []]
            ids = sorted({cid for c in claims for cid in c.evidence_ids})
            texts = {c.id: c.text for c in await self.chunks.get_many(self.pid, ids)}
            records, quality = grade_claims(claims, texts)
            total = int(quality["claims"])  # type: ignore[call-overload]
            degraded = total > 0 and int(quality["unsupported"]) * 2 > total  # type: ignore[call-overload]

            def store_grades(
                fresh: SectionRec,
                records: list[ClaimRecord] = records,
                quality: dict[str, object] = quality,
                degraded: bool = degraded,
            ) -> None:
                fresh.claims = [r.model_dump(mode="json") for r in records]
                fresh.quality_scores = quality
                fresh.status = SectionStatus.DEGRADED.value if degraded else SectionStatus.READY.value

            await self.rt.control.update_section(self.pid, row.section_key, store_grades)
            graded += len(records)
        return {"claims": graded}

    async def _visuals(self) -> dict[str, Any]:
        await self.reporter.stage(JobStage.GENERATING_VISUALS)
        ir = await self._load_ir()
        views = await self._subsystem_views()
        specs = await asyncio.to_thread(build_all, self.pid, ir, views, [])
        plan = await self._stored_plan()
        by_kind: dict[str, DiagramSpec] = {s.kind: s for s in specs}
        await self.rt.control.replace_diagrams(
            self.pid,
            [
                DiagramRec(
                    id=spec.id or diagram_id(self.pid, spec.kind, spec.title),
                    project_id=self.pid,
                    kind=spec.kind,
                    title=spec.title,
                    description=spec.description,
                    spec=spec.model_dump(mode="json"),
                    mermaid=to_mermaid(spec),
                    origin="deterministic",
                )
                for spec in specs
            ],
        )
        # Attach each diagram the plan asked for to its section (first matching section wins).
        attached: set[str] = set()
        for sec in plan.sections:
            for need in sec.visual_needs:
                wanted = by_kind.get(need) if need in _VISUAL_KINDS else None
                if wanted is None or wanted.id in attached:
                    continue
                if await self._attach_diagram(sec.key, wanted):
                    attached.add(wanted.id)
        return {"diagrams": len(specs), "attached": len(attached)}

    async def _attach_diagram(self, section_key: str, diagram: DiagramSpec) -> bool:
        """Append the diagram block to a section once; False when the section is empty or already has it."""
        attached = False

        def attach(row: SectionRec) -> bool:
            nonlocal attached
            attached = False  # the store may run this again after a lost race
            if not row.content:
                return False
            content = SectionContent.model_validate(row.content)
            if any(isinstance(b, DiagramBlock) and b.diagram_id == diagram.id for b in content.blocks):
                return False
            content.blocks.append(DiagramBlock(diagram_id=diagram.id, caption=diagram.title))
            row.content = content.model_dump(mode="json")
            attached = True
            return True

        if await self.rt.control.get_section(self.pid, section_key) is None:
            return False
        await self.rt.control.update_section(self.pid, section_key, attach)
        return attached
