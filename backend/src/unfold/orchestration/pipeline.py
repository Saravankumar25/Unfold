"""The analysis pipeline (LLD §14): one job, run to completion by a one-shot worker process.

acquire → inventory → analyse → IR → graph → lexical + dense index → subsystems → architecture + chapter plan →
research each chapter → write each chapter → verify and revise each chapter → diagrams → finalize. Plain async
functions; no framework owns state. Each stage group persists its output and then writes a `job_checkpoints` row, so
a rerun of the same job skips finished groups; inside a group a chapter that already has its result is skipped. A job
of kind `regenerate` redoes one chapter only. Success is reported only after everything is persisted.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from unfold import ANALYSIS_VERSION
from unfold.core.errors import ErrorCode, InputError, UnfoldError
from unfold.core.ids import diagram_id
from unfold.core.logging import bind_context, get_logger
from unfold.diagrams.builders import build_all
from unfold.diagrams.inputs import SubsystemView
from unfold.diagrams.mermaid import to_mermaid
from unfold.diagrams.models import DiagramSpec
from unfold.domain.enums import ClaimKind, ClaimStatus, JobStage, SectionStatus
from unfold.domain.models import ClaimRecord
from unfold.graph.builder import GraphBuilder
from unfold.graph.communities import detect as detect_subsystems
from unfold.graph.communities import entity_subsystems
from unfold.guide.critique import critique_section
from unfold.guide.generate import claim_quality, grade_claims, project_facts, unstated_claims
from unfold.guide.models import (
    PLANNER_VERSION,
    Block,
    ChapterPlan,
    CodeBlock,
    DiagramBlock,
    GuidePlan,
    ResearchBrief,
    SectionContent,
)
from unfold.guide.plan import plan_chapters
from unfold.guide.research import Investigator, ResearchCaps
from unfold.guide.revise import RevisionItem, plan_revision, run_revision_loop
from unfold.guide.rewrite import Rewriter
from unfold.guide.verification_models import SemanticCritique, StoredVerification, VerificationResult
from unfold.guide.verify import (
    block_texts,
    cited_paths,
    place_claims,
    text_from_chunks,
    verify_section,
    with_critique,
)
from unfold.guide.write import SectionEvidenceMissing, WrittenSection, write_chapter
from unfold.indexing.chunking import chunk_project
from unfold.indexing.dense import DenseIndex
from unfold.indexing.embeddings import embedder_for
from unfold.indexing.lexical import BM25Index
from unfold.indexing.store import ChunkRepository
from unfold.ingestion.service import AcquiredSource, acquire_github, acquire_zip
from unfold.llm.schemas import LLMClient
from unfold.observability.tracing import span
from unfold.orchestration.progress import ProgressReporter
from unfold.persistence.postgres.models import (
    AnalysisJobRow,
    DiagramRow,
    GuidePlanRow,
    GuideSectionRow,
    JobCheckpointRow,
    ProjectRow,
    SummaryRow,
)
from unfold.project_ir.builder import build_ir, validate_ir
from unfold.project_ir.schema import ProjectIdentity, ProjectIR
from unfold.project_ir.snapshot import load_ir, save_ir
from unfold.repo_intel.service import analyze_texts, scan_repository
from unfold.retrieval.hybrid import HybridRetriever
from unfold.runtime import Runtime
from unfold.understand.architecture import build_architecture
from unfold.understand.models import ArchitectureModel
from unfold.understand.repomap import RepoMap

log = get_logger(__name__)

# Exit code the worker uses for "job returned to the queue; run me again" (EX_TEMPFAIL).
RETRY_EXIT_CODE = 75
HEARTBEAT_SECONDS = 30.0
ALIVE_WITHIN = timedelta(minutes=2)

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


@dataclass(frozen=True)
class _Verified:
    content: SectionContent
    claims: list[ClaimRecord]
    evidence: dict[str, dict[str, object]]
    quality: dict[str, object]
    verification: StoredVerification
    status: SectionStatus


@dataclass
class _VerifyState:
    """What the closures of one chapter's verify-and-revise share."""

    claims: list[ClaimRecord]
    critique: SemanticCritique | None = None
    semantic: dict[str, float] | None = None  # the critique's scores, which round 0 alone computes
    stopped: str | None = None


async def run_analysis(rt: Runtime, llm: LLMClient, job_id: str) -> RunOutcome:
    """Run (or resume) one analysis job. Never raises for job-level failures: they are recorded on the job."""
    async with rt.db.session() as s:
        job = await s.get(AnalysisJobRow, uuid.UUID(job_id))
        project = await s.get(ProjectRow, job.project_id) if job else None
    if job is None or project is None:
        raise UnfoldError("Analysis job not found.", code=ErrorCode.NOT_FOUND, http_status=404)
    if job.status == "succeeded":
        return RunOutcome(succeeded=True, retry=False)

    pid = str(project.id)
    bind_context(project_id=pid, job_id=job_id)
    reporter = ProgressReporter(rt.db, rt.progress, pid, job_id)
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
            section = job.section_key if job.kind == "regenerate" else None
            await _Run(rt, llm, project, job_id, reporter, section_key=section).execute()
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
        self,
        rt: Runtime,
        llm: LLMClient,
        project: ProjectRow,
        job_id: str,
        reporter: ProgressReporter,
        *,
        section_key: str | None = None,
    ) -> None:
        self.rt = rt
        self.llm = llm
        self.project = project
        self.pid = str(project.id)
        self.job_uuid = uuid.UUID(job_id)
        self.job_id = job_id
        self.reporter = reporter
        self.chunks = ChunkRepository(rt.db)
        self.done: set[str] = set()
        self.ir: ProjectIR | None = None
        self._model: tuple[RepoMap, ArchitectureModel] | None = None
        self.section_key = section_key  # set: a `regenerate` job that redoes this one chapter
        self._hybrid: HybridRetriever | None = None
        self._investigate: Investigator | None = None

    # --- driver --------------------------------------------------------------------------------------------

    async def execute(self) -> None:
        async with self.rt.db.session() as s:
            res = await s.execute(
                select(JobCheckpointRow.name).where(JobCheckpointRow.job_id == self.job_uuid)
            )
            self.done = set(res.scalars())
        steps: list[tuple[str, Callable[[], Awaitable[dict[str, Any] | None]]]] = (
            [("regenerate", self._regenerate), ("visuals", self._visuals)]
            if self.section_key
            else [
                ("analyze", self._analyze),
                ("graph", self._graph),
                ("lexical", self._lexical),
                ("dense", self._dense),
                ("subsystems", self._subsystems),
                ("plan", self._plan),
                ("research", self._research),
                ("sections", self._sections),
                ("verify", self._verify),
                ("visuals", self._visuals),
            ]
        )
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
        async with self.rt.db.session() as s:
            row = await s.get(ProjectRow, self.project.id)
        if row is None or row.deleting_at is not None:
            raise UnfoldError("The project was removed.", code=ErrorCode.NOT_FOUND, retryable=False)

    async def _checkpoint(self, name: str, data: dict[str, Any]) -> None:
        async with self.rt.db.transaction() as s:
            await s.merge(JobCheckpointRow(job_id=self.job_uuid, name=name, data=data))
        self.done.add(name)

    async def _project(self) -> ProjectRow:
        async with self.rt.db.session() as s:
            row = await s.get(ProjectRow, self.project.id)
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
            inventory.skipped.update(dict.fromkeys(src.report.oversized, "oversized"))  # never written
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
        async with rt.db.transaction() as s:
            row = await s.get(ProjectRow, p.id, with_for_update=True)
            assert row is not None
            row.ir_object_key = key
            row.commit_sha = src.commit_sha
            row.default_branch = src.default_branch
            row.detected_name = identity.name
            row.project_type = {
                "categories": ir.project.categories,
                "frameworks": sorted({f.name for f in ir.frameworks}),
                "languages": [lang.name for lang in ir.languages],
            }
            row.stats = {**ir.stats, "chunks": len(chunks)}
        self.ir = ir
        return {"ir_key": key, "chunks": len(chunks)}

    async def _graph(self) -> None:
        await self.reporter.stage(JobStage.BUILDING_GRAPH)
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
        async with self.rt.db.transaction() as s:
            row = await s.get(ProjectRow, self.project.id, with_for_update=True)
            assert row is not None
            row.lexical_object_key = key

    async def _dense(self) -> None:
        await self.reporter.stage(JobStage.INDEXING_DENSE)
        chunks = await self.chunks.list_project(self.pid)

        async def work() -> None:
            index = DenseIndex(self.rt.qdrant, embedder_for(self.rt), self.rt.redis)
            await index.upsert(chunks)

        await self._optional("dense", work)

    async def _subsystems(self) -> None:
        rep = self.reporter
        ir = await self._load_ir()
        await rep.stage(JobStage.SUMMARIZING_HIERARCHY)
        project_summary = SummaryRow(
            project_id=self.project.id,
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
                SummaryRow(
                    project_id=self.project.id,
                    level="subsystem",
                    key=c.id,
                    title=c.label_hint or c.id,
                    facts={"cohesion": c.cohesion, "key_files": [path_of.get(k, k) for k in c.key_files]},
                    members=members,
                    prompt_version="deterministic",
                )
            )
        async with self.rt.db.transaction() as s:
            await s.execute(delete(SummaryRow).where(SummaryRow.project_id == self.project.id))
            s.add_all(rows)

        path_by_id = {f.id: f.path for f in ir.files}
        file_sub = {path_by_id[i]: c.id for c in candidates for i in c.member_file_ids if i in path_by_id}
        chunk_map = {
            c.id: file_sub[c.path] for c in await self.chunks.list_project(self.pid) if c.path in file_sub
        }
        await self.chunks.set_subsystems(self.pid, chunk_map)

        async def graph_work() -> None:
            await GraphBuilder(self.rt.neo4j).set_subsystem(self.pid, entity_subsystems(ir, candidates))

        async def dense_work() -> None:
            await DenseIndex(self.rt.qdrant, embedder_for(self.rt), self.rt.redis).set_subsystems(
                self.pid, chunk_map
            )

        if "graph" not in await self._degraded():
            await self._optional("graph", graph_work)
        if "dense" not in await self._degraded():
            await self._optional("dense", dense_work)

    async def _degraded(self) -> set[str]:
        async with self.rt.db.session() as s:
            row = await s.get(AnalysisJobRow, self.job_uuid)
        return set(((row.stage_progress if row else {}) or {}).get("degraded", []))

    async def _subsystem_views(self) -> list[SubsystemView]:
        async with self.rt.db.session() as s:
            res = await s.execute(
                select(SummaryRow).where(
                    SummaryRow.project_id == self.project.id, SummaryRow.level == "subsystem"
                )
            )
            return [
                SubsystemView(key=r.key, title=r.title, member_file_paths=[str(m) for m in r.members])
                for r in res.scalars()
            ]

    async def _understand(self) -> tuple[RepoMap, ArchitectureModel]:
        """RepoMap + ArchitectureModel: deterministic, so a resumed run rebuilds them instead of storing them."""
        if self._model is None:
            ir = await self._load_ir()

            def build() -> tuple[RepoMap, ArchitectureModel]:
                return RepoMap(ir), build_architecture(ir)

            self._model = await asyncio.to_thread(build)
        return self._model

    async def _plan(self) -> dict[str, Any]:
        await self.reporter.stage(JobStage.INVESTIGATING_PROJECT)
        ir = await self._load_ir()
        rm, arch = await self._understand()
        await self.reporter.stage(JobStage.PLANNING_GUIDE)
        views = await self._subsystem_views()
        facts = project_facts(ir, [(v.title, v.member_file_paths) for v in views])
        p = await self._project()
        plan, degraded = await plan_chapters(
            self.llm,
            ir,
            rm,
            arch,
            facts,
            technical_level=p.technical_level,
            instruction=p.user_instruction,
            job_id=self.job_id,
            project_id=self.pid,
        )
        if degraded:
            await self.reporter.degraded("guide_plan")
        async with self.rt.db.transaction() as s:
            # Rows left by a crashed earlier attempt of this stage go: a project has one analysis job, so there
            # is no finished chapter to reuse (chapter-level reuse by input hash is not built, ADR 0017).
            await s.execute(delete(GuideSectionRow).where(GuideSectionRow.project_id == p.id))
            await s.merge(
                GuidePlanRow(
                    project_id=p.id, plan=plan.model_dump(mode="json"), planner_version=PLANNER_VERSION
                )
            )
            for i, sec in enumerate(plan.sections):
                s.add(
                    GuideSectionRow(
                        project_id=p.id,
                        section_key=sec.key,
                        title=sec.title,
                        position=i,
                        status=SectionStatus.PENDING.value,
                        claims=[],
                    )
                )
        return {
            "sections": len(plan.sections),
            "project_kind": arch.project_kind,
            "degraded": degraded,
        }

    async def _stored_plan(self) -> GuidePlan:
        async with self.rt.db.session() as s:
            row = await s.get(GuidePlanRow, self.project.id)
        if row is None:
            raise UnfoldError("The guide plan is missing.", code=ErrorCode.GENERATION_FAILED)
        return GuidePlan.model_validate(row.plan)

    async def _section_rows(self) -> dict[str, GuideSectionRow]:
        async with self.rt.db.session() as s:
            res = await s.execute(
                select(GuideSectionRow).where(GuideSectionRow.project_id == self.project.id)
            )
            return {r.section_key: r for r in res.scalars()}

    async def _locked_row(self, s: Any, key: str) -> GuideSectionRow:
        row: GuideSectionRow = (
            await s.execute(
                select(GuideSectionRow)
                .where(GuideSectionRow.project_id == self.project.id, GuideSectionRow.section_key == key)
                .with_for_update()
            )
        ).scalar_one()
        return row

    @staticmethod
    def _raise_first(failures: list[BaseException], what: str) -> None:
        first = failures[0]
        if isinstance(first, UnfoldError):
            raise first
        raise UnfoldError(
            f"{what} failed ({type(first).__name__}).", code=ErrorCode.GENERATION_FAILED, retryable=True
        ) from first

    # --- one chapter ---------------------------------------------------------------------------------------
    # Each returns its result instead of persisting it: a stage persists chapter by chapter, `regenerate`
    # persists once at the end so the chapter on screen stays whole until the new one is verified.

    async def _retriever(self) -> HybridRetriever:
        if self._hybrid is None:
            ir = await self._load_ir()
            p = await self._project()
            self._hybrid = HybridRetriever.for_project(self.rt, self.pid, ir, p.lexical_object_key)
        return self._hybrid

    async def _investigator(self) -> Investigator:
        if self._investigate is None:
            rm, arch = await self._understand()
            self._investigate = Investigator(
                llm=self.llm,
                retriever=await self._retriever(),
                chunks=self.chunks,
                rm=rm,
                arch=arch,
                caps=ResearchCaps.from_settings(self.rt.settings),
                project_id=self.pid,
                job_id=self.job_id,
            )
        return self._investigate

    async def _claimed_elsewhere(self, plan: GuidePlan, spec: ChapterPlan) -> set[str]:
        """Paths other chapters name as targets (a component target names no file of its own)."""
        rm, _ = await self._understand()
        mine = {r.path for t in spec.targets if (r := rm.ref(t)) is not None}
        return {
            r.path
            for other in plan.sections
            if other.key != spec.key
            for t in other.targets
            if (r := rm.ref(t)) is not None
        } - mine

    async def _facts(self) -> list[str]:
        ir = await self._load_ir()
        views = await self._subsystem_views()
        return project_facts(ir, [(v.title, v.member_file_paths) for v in views])

    async def _snapshot_text(self, path: str, lines: int) -> str:
        """The file as the chunks hold it, one entry per file line (blank where no chunk covers), so that line
        counts and quoted excerpts are checked against the snapshot the guide was written from."""
        return text_from_chunks(await self.chunks.by_path(self.pid, path), lines)

    async def _critique(
        self, spec: ChapterPlan, content: SectionContent, others: Sequence[tuple[str, str]]
    ) -> SemanticCritique | None:
        """One model call that may only score and name blocks. Unusable output or an exhausted budget costs the
        critique, never the chapter: the deterministic checks stand on their own."""
        try:
            return await critique_section(
                self.llm,
                title=spec.title,
                objective=spec.objective,
                content=content,
                others=others,
                job_id=self.job_id,
                project_id=self.pid,
            )
        except UnfoldError as exc:
            if exc.code not in (ErrorCode.INVALID_MODEL_OUTPUT, ErrorCode.LLM_BUDGET_EXHAUSTED):
                raise
            log.warning("guide.critique_skipped", chapter=spec.key, code=exc.code.value)
            await self.reporter.degraded("guide_critique")
            return None

    async def _verify_one(
        self,
        spec: ChapterPlan,
        content: SectionContent,
        claims: Sequence[ClaimRecord],
        evidence: Mapping[str, Mapping[str, object]],
        brief: ResearchBrief,
        facts: Sequence[str],
        others: Sequence[tuple[str, str]],
    ) -> _Verified:
        """Deterministic checks and claim grading, one critique, then at most `revision_max_rounds` rewrites of
        the failing blocks. What still fails is qualified or removed; no claim is left standing as fact."""
        cfg = self.rt.settings
        rm, _ = await self._understand()
        p = await self._project()
        lines = {r.path: r.end_line for r in rm.files()}
        ev: dict[str, dict[str, object]] = {k: dict(v) for k, v in evidence.items()}
        rewriter = Rewriter(
            llm=self.llm,
            retriever=await self._retriever(),
            chunks=self.chunks,
            plan=spec,
            evidence=ev,
            facts=facts,
            technical_level=p.technical_level,
            instruction=p.user_instruction,
            job_id=self.job_id,
            project_id=self.pid,
        )
        state = _VerifyState(claims=list(claims))

        async def grade(raw: Sequence[ClaimRecord]) -> list[ClaimRecord]:
            ids = sorted({cid for c in raw for cid in c.evidence_ids})
            texts = {c.id: c.text for c in await self.chunks.get_many(self.pid, ids)}
            return grade_claims(raw, texts)[0]

        async def verify(c: SectionContent, rnd: int) -> VerificationResult:
            files = {
                path: await self._snapshot_text(path, lines.get(path, 1))
                for path in cited_paths(c)
                if path in lines
            }
            result = verify_section(
                c,
                await grade(state.claims),
                ev,
                files,
                rm,
                round_no=rnd,
                cap=cfg.revision_max_rounds,
            )
            if rnd == 0:  # one critique per chapter; revised blocks are judged by the deterministic checks
                state.critique = await self._critique(spec, c, others)
                if state.critique is not None:
                    result = with_critique(result, state.critique, rnd, cfg.revision_max_rounds)
                    state.semantic = result.semantic
            return result

        async def rewrite(c: SectionContent, items: Sequence[RevisionItem]) -> Mapping[str, Block]:
            await self.reporter.stage(JobStage.REVISING_GUIDE)
            try:
                out = await rewriter(c, items)
            except UnfoldError as exc:
                if exc.code not in (ErrorCode.INVALID_MODEL_OUTPUT, ErrorCode.LLM_BUDGET_EXHAUSTED):
                    raise
                log.warning("guide.rewrite_skipped", chapter=spec.key, code=exc.code.value)
                state.stopped = "budget"
                await self.reporter.degraded("guide_revision")
                return {}
            # a claim stands behind the prose it was made in: every claim placed in a rewritten block (verified
            # or not) goes, and the ones made for its replacement come in
            placed = place_claims(c, [x.text for x in state.claims])
            state.claims = [x for x in state.claims if placed[x.text] not in out] + rewriter.new_claims
            return out

        outcome = await run_revision_loop(
            verify,
            rewrite,
            content,
            cfg.revision_max_rounds,
            plan=lambda r, c: plan_revision(r, c, brief),
        )
        verification = outcome.verification.model_copy(
            update={"semantic": outcome.verification.semantic or state.semantic}
        )
        final = verification.claims
        out_claims: list[ClaimRecord] = []
        for c in final:
            fate = outcome.dispositions.get(c.text)
            if fate == "removed":
                continue
            if fate == "qualified":  # the prose now says it is not confirmed: no longer a statement of fact
                c = c.model_copy(
                    update={
                        "kind": ClaimKind.INFERENCE,
                        "status": ClaimStatus.INFERRED,
                        "confidence": 0.2,
                        "note": "Stated in the guide as not confirmed in the code.",
                    }
                )
            out_claims.append(c)
        # a claim the model listed but the chapter never makes (its unverified names are not in the prose) stands
        # behind no statement; it is recorded here and left out of `claims`, which describe what the guide says
        prose = " ".join(
            [outcome.content.summary]
            + [t for b in outcome.content.blocks for t in block_texts(b)]
            + [b.code for b in outcome.content.blocks if isinstance(b, CodeBlock)]
        )
        cited_ids = sorted({i for c in out_claims for i in c.evidence_ids})
        cited_text = {c.id: c.text for c in await self.chunks.get_many(self.pid, cited_ids)}
        unstated = unstated_claims(out_claims, cited_text, prose)
        out_claims = [c for c in out_claims if c not in unstated]
        skipped = {c.text[:120] for c in unstated}
        gaps = (
            [
                r.subject[:140]
                for r in outcome.verification.deterministic
                if not r.ok and r.subject not in skipped
            ]
            if outcome.verification.verdict == "accept_with_gaps"
            else []
        )
        stored = StoredVerification(
            result=verification,
            rounds=outcome.rounds,
            stopped_by=state.stopped or outcome.stopped_by,
            gaps=gaps,
            unstated=[c.text for c in unstated],
            critique=state.critique is not None,
        )
        quality = {
            **claim_quality(out_claims),
            "verdict": outcome.verification.verdict,
            "revision_rounds": outcome.rounds,
            "gaps": len(gaps),
            "claims_not_stated": len(unstated),
            "semantic": verification.semantic,
        }
        return _Verified(
            content=outcome.content,
            claims=out_claims,
            evidence=ev,
            quality=quality,
            verification=stored,
            status=SectionStatus.DEGRADED if gaps else SectionStatus.READY,
        )

    async def _persist_chapter(
        self, key: str, brief: ResearchBrief, draft: WrittenSection, done: _Verified
    ) -> None:
        async with self.rt.db.transaction() as s:
            row = await self._locked_row(s, key)
            row.research = brief.model_dump(mode="json")
            row.content = done.content.model_dump(mode="json")
            row.claims = [c.model_dump(mode="json") for c in done.claims]
            row.evidence_summary = done.evidence
            row.quality_scores = done.quality
            row.verification = done.verification.model_dump(mode="json")
            row.status = done.status.value
            row.version += 1

    # --- stage groups (all chapters) -----------------------------------------------------------------------

    async def _research(self) -> dict[str, Any]:
        await self.reporter.stage(JobStage.RESEARCHING_GUIDE)
        plan = await self._stored_plan()
        investigator = await self._investigator()
        rows = await self._section_rows()
        gate = asyncio.Semaphore(max(1, self.rt.settings.section_concurrency))
        researched = 0

        async def one(spec: ChapterPlan) -> None:
            nonlocal researched
            if rows[spec.key].research or rows[spec.key].content:
                return  # an earlier attempt (or an unchanged chapter) has it; resume skips it
            async with gate:
                brief = await investigator.research(spec, await self._claimed_elsewhere(plan, spec))
            async with self.rt.db.transaction() as s:
                (await self._locked_row(s, spec.key)).research = brief.model_dump(mode="json")
            researched += 1
            await self.reporter.heartbeat()

        results = await asyncio.gather(*(one(s) for s in plan.sections), return_exceptions=True)
        if failures := [r for r in results if isinstance(r, BaseException)]:
            self._raise_first(failures, "Chapter research")
        return {"researched": researched}

    async def _write_one(
        self,
        spec: ChapterPlan,
        brief: ResearchBrief,
        facts: Sequence[str],
        plan: GuidePlan,
        summaries: Mapping[str, str],
    ) -> WrittenSection:
        p = await self._project()
        titles = {s.key: s.title for s in plan.sections}
        deps = [(titles[d], summaries[d]) for d in spec.depends_on if d in summaries]
        others = [
            (s.title, s.objective)
            for s in plan.sections
            if s.key != spec.key and s.key not in spec.depends_on
        ]
        return await write_chapter(
            self.llm,
            self.chunks,
            spec,
            brief,
            facts,
            deps,
            others,
            technical_level=p.technical_level,
            instruction=p.user_instruction,
            job_id=self.job_id,
            project_id=self.pid,
        )

    async def _sections(self) -> dict[str, Any]:
        """Write every chapter that has a brief and no content. The draft is stored unverified (status
        GENERATING); `_verify` grades, revises and finishes it."""
        await self.reporter.stage(JobStage.GENERATING_GUIDE)
        plan = await self._stored_plan()
        facts = await self._facts()
        gate = asyncio.Semaphore(max(1, self.rt.settings.section_concurrency))
        rows = await self._section_rows()
        summaries = {
            k: SectionContent.model_validate(r.content).summary for k, r in rows.items() if r.content
        }
        written = 0
        missing: list[SectionEvidenceMissing] = []

        async def one(spec: ChapterPlan) -> None:
            nonlocal written
            research = rows[spec.key].research
            if not research:
                raise UnfoldError(
                    f"The research brief for '{spec.key}' is missing.", code=ErrorCode.GENERATION_FAILED
                )
            brief = ResearchBrief.model_validate(research)
            try:
                async with gate:
                    draft = await self._write_one(spec, brief, facts, plan, summaries)
            except SectionEvidenceMissing as exc:
                missing.append(exc)
                async with self.rt.db.transaction() as s:
                    failed_row = await self._locked_row(s, spec.key)
                    failed_row.status = SectionStatus.FAILED.value
                    failed_row.quality_scores = {"reason": exc.message}
                return
            async with self.rt.db.transaction() as s:
                row = await self._locked_row(s, spec.key)
                row.content = draft.content.model_dump(mode="json")
                row.claims = [c.model_dump(mode="json") for c in draft.claims]
                row.evidence_summary = draft.evidence
                row.status = (
                    SectionStatus.GENERATING.value
                )  # `_verify` grades and revises it, then it is ready
                row.version += 1
            summaries[spec.key] = draft.content.summary
            written += 1
            await self.reporter.heartbeat()

        # Waves in dependency order: a chapter sees the summaries of the chapters it builds on and nothing else.
        remaining = [s for s in plan.sections if not rows[s.key].content]
        while remaining:
            left = {s.key for s in remaining}
            wave = [s for s in remaining if not left & set(s.depends_on)] or remaining
            results = await asyncio.gather(*(one(s) for s in wave), return_exceptions=True)
            if failures := [r for r in results if isinstance(r, BaseException)]:
                self._raise_first(failures, "Section generation")
            done = {s.key for s in wave}
            remaining = [s for s in remaining if s.key not in done]
        if missing:
            if len(missing) == len(plan.sections):
                raise missing[0]  # nothing to show: fail the job as before
            await self.reporter.degraded("guide_sections")
        return {"written": written, "failed": len(missing)}

    async def _verify(self) -> dict[str, Any]:
        """Verify and, where needed, revise every written chapter that has no verification yet (the per-chapter
        checkpoint: a resumed run never redoes a verified chapter)."""
        await self.reporter.stage(JobStage.VERIFYING_GUIDE)
        plan = await self._stored_plan()
        facts = await self._facts()
        rows = await self._section_rows()
        summaries = {
            s.key: SectionContent.model_validate(rows[s.key].content).summary
            if rows[s.key].content
            else s.objective
            for s in plan.sections
        }
        gate = asyncio.Semaphore(max(1, self.rt.settings.section_concurrency))
        done = revised = gaps = 0

        async def one(spec: ChapterPlan) -> None:
            nonlocal done, revised, gaps
            row = rows[spec.key]
            if row.status == SectionStatus.FAILED.value or not row.content or row.verification is not None:
                return  # no content to check, or already verified by an earlier attempt
            others = [(s.title, summaries[s.key]) for s in plan.sections if s.key != spec.key]
            async with gate:
                v = await self._verify_one(
                    spec,
                    SectionContent.model_validate(row.content),
                    [ClaimRecord.model_validate(c) for c in row.claims or []],
                    row.evidence_summary or {},
                    ResearchBrief.model_validate(row.research),
                    facts,
                    others,
                )
            async with self.rt.db.transaction() as s:
                fresh = await self._locked_row(s, spec.key)
                fresh.content = v.content.model_dump(mode="json")
                fresh.claims = [c.model_dump(mode="json") for c in v.claims]
                fresh.evidence_summary = v.evidence
                fresh.quality_scores = v.quality
                fresh.verification = v.verification.model_dump(mode="json")
                fresh.status = v.status.value
            done += 1
            revised += v.verification.rounds > 0
            gaps += len(v.verification.gaps)
            await self.reporter.heartbeat()

        results = await asyncio.gather(*(one(s) for s in plan.sections), return_exceptions=True)
        if failures := [r for r in results if isinstance(r, BaseException)]:
            self._raise_first(failures, "Verification")
        return {"verified": done, "revised": revised, "gaps": gaps}

    async def _regenerate(self) -> dict[str, Any]:
        """Research, write, verify and revise one chapter and swap it in whole; the others are not touched."""
        key = self.section_key
        plan = await self._stored_plan()
        spec = next((s for s in plan.sections if s.key == key), None)
        if spec is None:
            raise UnfoldError("That chapter is not in the guide.", code=ErrorCode.NOT_FOUND, retryable=False)
        rows = await self._section_rows()
        facts = await self._facts()
        summaries = {
            k: SectionContent.model_validate(r.content).summary for k, r in rows.items() if r.content
        }
        await self.reporter.stage(JobStage.RESEARCHING_GUIDE)
        brief = await (await self._investigator()).research(spec, await self._claimed_elsewhere(plan, spec))
        await self.reporter.stage(JobStage.GENERATING_GUIDE)
        draft = await self._write_one(spec, brief, facts, plan, summaries)
        await self.reporter.stage(JobStage.VERIFYING_GUIDE)
        titles = {s.key: s.title for s in plan.sections}
        others = [
            (titles[k], summaries.get(k, s.objective)) for s in plan.sections if (k := s.key) != spec.key
        ]
        done = await self._verify_one(spec, draft.content, draft.claims, draft.evidence, brief, facts, others)
        await self._persist_chapter(spec.key, brief, draft, done)
        return {
            "regenerated": spec.key,
            "rounds": done.verification.rounds,
            "gaps": len(done.verification.gaps),
        }

    async def _visuals(self) -> dict[str, Any]:
        await self.reporter.stage(JobStage.GENERATING_VISUALS)
        ir = await self._load_ir()
        views = await self._subsystem_views()
        specs = await asyncio.to_thread(build_all, self.pid, ir, views, [])
        plan = await self._stored_plan()
        by_kind: dict[str, DiagramSpec] = {s.kind: s for s in specs}
        async with self.rt.db.transaction() as s:
            await s.execute(delete(DiagramRow).where(DiagramRow.project_id == self.project.id))
            for spec in specs:
                s.add(
                    DiagramRow(
                        id=spec.id or diagram_id(self.pid, spec.kind, spec.title),
                        project_id=self.project.id,
                        kind=spec.kind,
                        title=spec.title,
                        description=spec.description,
                        spec=spec.model_dump(mode="json"),
                        mermaid=to_mermaid(spec),
                        origin="deterministic",
                    )
                )
            # Attach each diagram the plan asked for to its section (first matching section wins).
            attached: set[str] = {
                b.diagram_id
                for r in (
                    await s.execute(
                        select(GuideSectionRow).where(GuideSectionRow.project_id == self.project.id)
                    )
                ).scalars()
                if r.content
                for b in SectionContent.model_validate(r.content).blocks
                if isinstance(b, DiagramBlock)
            }
            for sec in plan.sections:
                for need in sec.visual_needs:
                    wanted = by_kind.get(need) if need in _VISUAL_KINDS else None
                    if wanted is None or wanted.id in attached:
                        continue
                    row = (
                        await s.execute(
                            select(GuideSectionRow)
                            .where(
                                GuideSectionRow.project_id == self.project.id,
                                GuideSectionRow.section_key == sec.key,
                            )
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if row is None or not row.content:
                        continue
                    content = SectionContent.model_validate(row.content)
                    if any(isinstance(b, DiagramBlock) and b.diagram_id == wanted.id for b in content.blocks):
                        continue
                    content.blocks.append(DiagramBlock(diagram_id=wanted.id, caption=wanted.title))
                    row.content = content.model_dump(mode="json")
                    attached.add(wanted.id)
        return {"diagrams": len(specs), "attached": len(attached)}
