"""Failure injection against the real stores (phase 5, tasks 4 and 12): every case must resume, or fail with a
typed error, with no duplicate rows and no half-written chapter.

Parsing, indexing, retrieval, verification, persistence, the API and the sweeper are real. The doubles are the
model (`ScriptedLLM`, or a scripted *provider* behind the real `LLMGateway` when the gateway's 429 / timeout /
circuit / key-pool / budget behaviour is the point) and `FlakyDB`, a proxy that refuses a checkpoint write or every
statement. A "kill" cancels the running job from outside at a chosen model call: the job row is left `running`
exactly as a SIGKILL would leave it, and the heartbeat is aged by hand to what the sweeper waits for.

The contract asserted here is adapter-agnostic (job claim under a row lock, per-stage checkpoints, per-chapter
resume): the same assertions hold when delivery comes from SQS instead of the local queue.

One project is walked through a chain of failures (`chain` fixture) so the expensive early stages run once; each
test below asserts one row of `docs/design/reliability.md` against what the chain recorded.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel
from qdrant_client import models as qm
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import OperationalError

from unfold.cleanup.service import cleanup_project
from unfold.cleanup.sweeper import sweep
from unfold.core.config import Settings
from unfold.core.errors import ErrorCode, ProviderCapacityError, ProviderError
from unfold.evaluation.scripted import ScriptedLLM, evidence_blocks
from unfold.guide.models import DraftClaim, ParagraphBlock, SectionContent, SectionDraft
from unfold.guide.plan import PlanRefinement
from unfold.guide.research import QuestionSet
from unfold.guide.rewrite import RewriteDraft
from unfold.guide.verification_models import SemanticCritique
from unfold.indexing.dense_qdrant import DenseIndex
from unfold.indexing.embeddings import embedder_for
from unfold.llm.circuit import CircuitBreaker, CircuitState
from unfold.llm.gateway import LLMGateway
from unfold.llm.key_pool import KeyPool
from unfold.llm.providers.base import AuthError, RateLimited, TransientError, tier_models
from unfold.llm.schemas import LLMRequest, LLMResponse, Usage
from unfold.orchestration.pipeline import RunOutcome, run_analysis
from unfold.persistence.postgres.models import (
    AnalysisJobRow,
    Base,
    GuidePlanRow,
    GuideSectionRow,
    JobCheckpointRow,
)
from unfold.ports import project_prefix
from unfold.runtime import Runtime

pytestmark = pytest.mark.integration

API = "/api/v1"
UNSUPPORTED = "It retries failed calls with exponential backoff"
GENEROUS = {"*": {"rpm": 10_000, "tpm": 100_000_000, "rpd": 1_000_000, "concurrency": 50}}
P1_PURPOSES = ("guide.plan", "guide.research", "guide.section")  # priority P1: never refused before P2


# ---- doubles ---------------------------------------------------------------------------------------------------


class Kill:
    """Fault: the worker dies here (the call never returns; the harness cancels the run)."""


@dataclass
class Fault:
    purpose: str
    nth: int  # 1-based, counted per purpose on one LLM instance
    action: Any  # Kill() | an exception instance | an async callable


class ScriptedChapters(ScriptedLLM):
    """Writes `<cited sentence>. <uncited, unsupported sentence>.` so that every chapter has something to repair."""

    def _section(self, req: LLMRequest) -> SectionDraft:
        base = super()._section(req)
        label, path, _ = evidence_blocks(req.messages[0].content)[0]
        text_ = f"The code in `{path}` is part of the project [{label}]. {UNSUPPORTED}."
        return SectionDraft(
            content=SectionContent(summary=base.content.summary, blocks=[ParagraphBlock(text=text_)]),
            claims=[
                DraftClaim(text=f"`{path}` is part of the project", kind="project_fact", evidence=[label]),
                DraftClaim(text=UNSUPPORTED, kind="project_fact", evidence=[]),
            ],
        )


class FaultyLLM(ScriptedChapters):
    def __init__(self, faults: list[Fault] | None = None) -> None:
        super().__init__()
        self.faults = faults or []
        self.seen: Counter[str] = Counter()
        self.reached = asyncio.Event()  # set when a Kill fires

    async def generate_structured(self, req: LLMRequest, schema: type[BaseModel]) -> Any:
        self.seen[req.purpose] += 1
        for f in self.faults:
            if f.purpose == req.purpose and f.nth == self.seen[req.purpose]:
                self.calls.append(req.purpose)
                if isinstance(f.action, Kill):
                    self.reached.set()
                    await asyncio.Event().wait()  # until the harness cancels the run
                if isinstance(f.action, BaseException):
                    raise f.action
                await f.action()
        return await super().generate_structured(req, schema)


class FlakyDB:
    """`Database` proxy: refuse one checkpoint write (`fail_checkpoint`) or every statement (`down`)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.fail_checkpoint: str | None = None
        self.down = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _refuse(self) -> None:
        if self.down:
            raise OperationalError("SELECT 1", {}, ConnectionRefusedError("the database is paused"))

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[Any]:
        self._refuse()
        async with self._inner.session() as s:
            yield s

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        self._refuse()
        async with self._inner.transaction() as s:
            yield _Session(s, self)


def _swap_db(rt: Runtime, db: Any) -> None:
    """Point every Postgres-backed store of the runtime at `db` (the stores keep their own reference)."""
    rt._stores["db"] = db
    for store, attr in ((rt.control, "_db"), (rt.progress, "_db"), (rt.chunks, "db")):
        setattr(store, attr, db)


class _Session:
    def __init__(self, inner: Any, db: FlakyDB) -> None:
        self._inner, self._db = inner, db

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def merge(self, obj: Any, *a: Any, **k: Any) -> Any:
        if isinstance(obj, JobCheckpointRow) and obj.name == self._db.fail_checkpoint:
            self._db.fail_checkpoint = None
            raise OperationalError(
                "INSERT INTO job_checkpoints", {}, ConnectionResetError("connection reset")
            )
        return await self._inner.merge(obj, *a, **k)


class ScriptedProvider:
    """A `Provider` behind the real gateway: answers with `ScriptedLLM`, after the fault scripted for the call."""

    name = "gemini"
    key_count = 2
    _TIERS: ClassVar[dict[str, str]] = {"fast": "g-fast", "balanced": "g-bal", "reasoning": "g-reason"}
    _SCHEMAS: ClassVar[dict[str, type[BaseModel]]] = {
        "guide.plan": PlanRefinement,
        "guide.research": QuestionSet,
        "guide.section": SectionDraft,
        "guide.critique": SemanticCritique,
        "guide.revise": RewriteDraft,
    }

    def __init__(self) -> None:
        self.brain = ScriptedChapters()
        self.script: dict[str, list[Any]] = {}  # purpose -> actions consumed one per provider call
        self.calls: list[tuple[str, str, int]] = []  # (purpose, model, key index)

    def models_for(self, capability: Any) -> list[str]:
        return tier_models(self._TIERS, capability)

    async def generate_json(
        self, model: str, key_index: int, req: LLMRequest, json_schema: dict[str, Any]
    ) -> tuple[dict[str, Any], LLMResponse]:
        self.calls.append((req.purpose, model, key_index))
        queue = self.script.get(req.purpose, [])
        action = queue.pop(0) if queue else None
        if callable(action):
            action = action(key_index)
        if isinstance(action, BaseException):
            raise action
        if isinstance(action, float):  # a provider that does not answer in time
            await asyncio.sleep(action)
        value = await self.brain.generate_structured(req, self._SCHEMAS[req.purpose])
        data = value.model_dump(mode="json")
        usage = Usage(input_tokens=200, output_tokens=100)
        return data, LLMResponse(text=json.dumps(data), provider="gemini", model=model, usage=usage)

    async def generate(self, model: str, key_index: int, req: LLMRequest) -> LLMResponse:
        raise NotImplementedError

    def stream(self, model: str, key_index: int, req: LLMRequest) -> AsyncIterator[str]:
        raise NotImplementedError


@contextlib.asynccontextmanager
async def _api(settings: Settings, queue: Any) -> AsyncIterator[Any]:
    """A real app on the real stores with a recording queue (the `open_api` fixture is function scoped)."""
    import httpx

    from unfold.api.main import create_app

    app = create_app(settings, queue=queue, llm=None)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


# ---- helpers ---------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
async def rt(settings: Settings) -> AsyncIterator[Runtime]:
    """A runtime of this module's own: one chapter at a time (so "the Nth call" is a place), and room for the
    many deliberate attempts (the default stops a job after three)."""
    cfg = settings.model_copy(
        update={
            "max_job_attempts": 50,
            "section_concurrency": 1,
            "llm_model_limits": json.dumps(GENEROUS),
            "llm_request_timeout_seconds": 1.0,
        }
    )
    runtime = Runtime.create(cfg, db_pool_size=5)
    await runtime.objects.ensure_bucket()
    yield runtime
    await runtime.aclose()


async def _job(rt: Runtime, job_id: str) -> AnalysisJobRow:
    async with rt.db.session() as s:
        job = await s.get(AnalysisJobRow, uuid.UUID(job_id))
    assert job is not None
    return job


async def _rows(rt: Runtime, project_id: str) -> dict[str, GuideSectionRow]:
    async with rt.db.session() as s:
        res = await s.execute(
            select(GuideSectionRow).where(GuideSectionRow.project_id == uuid.UUID(project_id))
        )
        return {r.section_key: r for r in res.scalars()}


async def _checkpoints(rt: Runtime, job_id: str) -> set[str]:
    async with rt.db.session() as s:
        res = await s.execute(
            select(JobCheckpointRow.name).where(JobCheckpointRow.job_id == uuid.UUID(job_id))
        )
        return set(res.scalars())


async def _age_heartbeat(rt: Runtime, job_id: str) -> None:
    """What the sweeper waits for: a worker that stopped heartbeating (it is dead, not busy)."""
    async with rt.db.transaction() as s:
        await s.execute(
            text("UPDATE analysis_jobs SET heartbeat_at = now() - interval '1 hour' WHERE id = :id"),
            {"id": job_id},
        )


def _frozen(row: GuideSectionRow) -> dict[str, Any]:
    """Everything a chapter stores (diagram blocks are the visuals stage's, not the chapter's own text)."""
    content = row.content and {
        **row.content,
        "blocks": [b for b in row.content["blocks"] if b["type"] != "diagram"],
    }
    keys = ("claims", "evidence_summary", "quality_scores", "research", "verification", "status", "version")
    return {"content": content} | {k: getattr(row, k) for k in keys}


def _text(row: GuideSectionRow) -> str:
    assert row.content
    return " ".join(str(b.get("text", "")) for b in row.content["blocks"])


async def _assert_consistent(rt: Runtime, project_id: str, job_id: str, *, finished: bool) -> None:
    """No half-written chapter and no duplicate row, whatever moment the job was stopped at."""
    rows = await _rows(rt, project_id)
    async with rt.db.session() as s:
        plan = await s.get(GuidePlanRow, uuid.UUID(project_id))
    assert plan is not None
    assert sorted(rows) == sorted(c["key"] for c in plan.plan["sections"])  # one row per planned chapter
    for key, row in rows.items():
        if row.content is not None:
            assert row.research is not None, f"{key}: written without a brief"
        if row.verification is not None:  # verified: whole and final
            assert row.content is not None and row.status in ("ready", "degraded"), key
            assert UNSUPPORTED not in _text(row), f"{key}: an unsupported statement was left standing"
        if row.status in ("ready", "degraded"):
            assert row.verification is not None, f"{key}: marked {row.status} but never verified"
        if row.status == "generating":
            assert row.content is not None and row.verification is None, key
        texts = [c["text"] for c in row.claims or []]
        assert len(texts) == len(set(texts)), f"{key}: duplicate claims"
        diagrams = [
            b["diagram_id"] for b in (row.content or {"blocks": []})["blocks"] if b["type"] == "diagram"
        ]
        assert len(diagrams) == len(set(diagrams)), f"{key}: duplicate diagram blocks"
        if finished:
            assert row.status in ("ready", "degraded") and row.verification is not None, key
    async with rt.db.session() as s:
        jobs = (
            (
                await s.execute(
                    select(AnalysisJobRow.id).where(AnalysisJobRow.project_id == uuid.UUID(project_id))
                )
            )
            .scalars()
            .all()
        )
    assert uuid.UUID(job_id) in jobs


async def _run_until_killed(rt: Runtime, llm: FaultyLLM, job_id: str) -> None:
    task = asyncio.create_task(run_analysis(rt, llm, job_id))
    waiter = asyncio.create_task(llm.reached.wait())
    done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED, timeout=900)
    assert waiter in done, (
        f"the job ended before the kill point: {task.result() if task.done() else 'timeout'}"
    )
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _counts(rt: Runtime, project_id: str) -> dict[str, int]:
    """Rows and points one project holds in every store (duplicates would show as larger numbers)."""
    out: dict[str, int] = {}
    async with rt.db.session() as s:
        for table in Base.metadata.sorted_tables:
            if "project_id" in table.c and table.name != "job_events":
                n = (
                    await s.execute(
                        text(f"SELECT count(*) FROM {table.name} WHERE project_id = :p"), {"p": project_id}
                    )
                ).scalar_one()
                out[f"pg.{table.name}"] = int(n)
    collection = DenseIndex(rt.qdrant, embedder_for(rt), rt.redis).collection
    counted = await rt.qdrant.client.count(
        collection,
        count_filter=qm.Filter(
            must=[qm.FieldCondition(key="project_id", match=qm.MatchValue(value=project_id))]
        ),
        exact=True,
    )
    out["vectors"] = int(counted.count)
    nodes = await rt.neo4j.run("MATCH (n {project_id: $pid}) RETURN count(n) AS n", pid=project_id)
    out["graph_nodes"] = int(nodes[0]["n"])
    return out


@dataclass
class Chain:
    """What the chain recorded; each test below asserts on one part."""

    seeded: Any
    plan_keys: list[str] = field(default_factory=list)
    obs: dict[str, Any] = field(default_factory=dict)


# ---- the chain: one project, many failures --------------------------------------------------------------------


@pytest.fixture(scope="module")
async def chain(
    rt: Runtime,
    seed_project: Callable[..., Awaitable[Any]],
    settings: Settings,
) -> AsyncIterator[Chain]:
    seeded = await seed_project()
    pid, job = seeded.project_id, seeded.job_id
    c = Chain(seeded)
    flaky = FlakyDB(rt.db)
    inner = rt.db
    _swap_db(rt, flaky)
    try:
        # 1. the same job delivered twice at once; the winner is killed after research, before the first write
        a, b = FaultyLLM([Fault("guide.section", 1, Kill())]), FaultyLLM([Fault("guide.section", 1, Kill())])
        ta = asyncio.create_task(run_analysis(rt, a, job))
        tb = asyncio.create_task(run_analysis(rt, b, job))
        done, _ = await asyncio.wait({ta, tb}, return_when=asyncio.FIRST_COMPLETED, timeout=600)
        loser_task = next(iter(done))
        loser, winner_task, winner = (a, tb, b) if loser_task is ta else (b, ta, a)
        c.obs["dup_loser_outcome"] = loser_task.result()
        c.obs["dup_loser_calls"] = list(loser.calls)
        await asyncio.wait_for(winner.reached.wait(), timeout=900)
        winner_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await winner_task
        row = await _job(rt, job)
        c.obs["dup_attempts"] = row.attempt
        c.obs["dup_status"] = row.status
        c.obs["after_kill_checkpoints"] = await _checkpoints(rt, job)
        rows = await _rows(rt, pid)
        c.plan_keys = list(rows)
        c.obs["after_kill_rows"] = {
            k: (r.research is not None, r.content is not None, r.status) for k, r in rows.items()
        }
        await _assert_consistent(rt, pid, job, finished=False)

        # a fresh heartbeat protects a live worker: a third delivery now does nothing
        refused = FaultyLLM()
        c.obs["live_refused"] = (await run_analysis(rt, refused, job), list(refused.calls))
        # the sweeper finds the dead worker once the heartbeat is stale, and hands the job back to the queue
        queue = _Queue()
        await _age_heartbeat(rt, job)
        report = await sweep(rt, queue)  # type: ignore[arg-type]
        c.obs["sweep_requeued"] = (report.jobs_requeued, job in queue.analyses)

        # 2. resume: the provider runs out of capacity at the third chapter (retryable, finished chapters kept)
        cap = FaultyLLM([Fault("guide.section", 3, ProviderCapacityError("scripted capacity failure"))])
        out = await run_analysis(rt, cap, job)
        row = await _job(rt, job)
        c.obs["capacity"] = (out, row.status, row.error_code, list(cap.calls))
        rows = await _rows(rt, pid)
        c.obs["capacity_rows"] = {k: r.content is not None for k, r in rows.items()}
        await _assert_consistent(rt, pid, job, finished=False)

        # 3. resume: killed during the second chapter's revision (the first chapter is verified and final)
        kill = FaultyLLM([Fault("guide.revise", 2, Kill())])
        await _age_heartbeat(rt, job)
        await _run_until_killed(rt, kill, job)
        rows = await _rows(rt, pid)
        c.obs["revision_kill_calls"] = list(kill.calls)
        c.obs["revision_kill_rows"] = {k: (r.verification is not None, r.status) for k, r in rows.items()}
        c.obs["verified_before"] = {k: _frozen(r) for k, r in rows.items() if r.verification is not None}
        await _assert_consistent(rt, pid, job, finished=False)

        # a regeneration is refused while the analysis is still unfinished, and changes nothing
        key0 = c.plan_keys[0]
        async with _api(settings, _Queue()) as client:
            client.cookies.set("unfold_session", seeded.token)
            before = await _job(rt, job)
            resp = await client.post(f"{API}/projects/{pid}/sections/{key0}/regenerate")
            after = await _job(rt, job)
            c.obs["regen_during_run"] = (
                resp.status_code,
                resp.json()["error"]["code"],
                (before.status, before.attempt) == (after.status, after.attempt),
            )
            async with rt.db.session() as s:
                n = (
                    await s.execute(
                        select(AnalysisJobRow.id).where(
                            AnalysisJobRow.project_id == uuid.UUID(pid), AnalysisJobRow.kind == "regenerate"
                        )
                    )
                ).all()
            c.obs["regen_rows_during_run"] = len(n)

        # 4. the database is paused for the whole attempt: nothing can even record the failure
        flaky_llm = FaultyLLM([Fault("guide.critique", 1, _arm(flaky, "down"))])
        await _age_heartbeat(rt, job)
        try:
            await run_analysis(rt, flaky_llm, job)
            c.obs["db_down_raised"] = None
        except OperationalError as exc:
            c.obs["db_down_raised"] = type(exc).__name__
        flaky.down = False
        row = await _job(rt, job)
        c.obs["db_down_job"] = (row.status, row.error_code)
        await _assert_consistent(rt, pid, job, finished=False)

        # 5. the database refuses the checkpoint write at the end of verification (everything else was written)
        await _age_heartbeat(rt, job)
        flaky.fail_checkpoint = "verify"
        blip = FaultyLLM()
        out = await run_analysis(rt, blip, job)
        row = await _job(rt, job)
        c.obs["checkpoint_blip"] = (out, row.status, row.error_code, list(blip.calls))
        await _assert_consistent(rt, pid, job, finished=False)

        # 6. resume: finishes without a single model call (everything was already written and verified)
        final = FaultyLLM()
        out = await run_analysis(rt, final, job)
        row = await _job(rt, job)
        c.obs["final"] = (out, row.status, list(final.calls))
        await _assert_consistent(rt, pid, job, finished=True)
        rows = await _rows(rt, pid)
        c.obs["verified_unchanged"] = {k: _frozen(rows[k]) for k in c.obs["verified_before"]} == c.obs[
            "verified_before"
        ]
        c.obs["final_frozen"] = {k: _frozen(r) for k, r in rows.items()}
        c.obs["final_counts"] = await _counts(rt, pid)

        # 7. the job delivered again after it succeeded does nothing
        again = FaultyLLM()
        c.obs["redelivery"] = (await run_analysis(rt, again, job), list(again.calls))
        rows = await _rows(rt, pid)
        c.obs["redelivery_unchanged"] = {k: _frozen(r) for k, r in rows.items()} == c.obs["final_frozen"]
        yield c
    finally:
        _swap_db(rt, inner)
        with contextlib.suppress(Exception):
            await cleanup_project(rt, pid)


class _Queue:
    """`JobQueue` double that records what the sweeper and the API asked for."""

    def __init__(self) -> None:
        self.analyses: list[str] = []

    async def enqueue_analysis(self, job_id: str, attempt: int, *, defer_s: float | None = None) -> None:
        self.analyses.append(job_id)

    async def enqueue_project_cleanup(self, project_id: str) -> None: ...

    async def enqueue_export(self, export_id: str) -> None: ...

    async def aclose(self) -> None: ...


def _arm(flaky: FlakyDB, attr: str) -> Callable[[], Awaitable[None]]:
    async def go() -> None:
        setattr(flaky, attr, True)
        raise ProviderError("the provider call failed while the database went away")

    return go


# ---- tests (one per row of the reliability matrix) --------------------------------------------------------------


async def test_duplicate_delivery_runs_the_job_once(chain: Chain) -> None:
    o = chain.obs
    assert o["dup_loser_outcome"] == RunOutcome(succeeded=False, retry=False)  # not claimed, not failed
    assert o["dup_loser_calls"] == []  # the second worker made no model call and wrote nothing
    assert o["dup_attempts"] == 1  # one claim; the attempt counter was not advanced twice
    assert len(chain.plan_keys) >= 3  # the chain needs a few chapters to be meaningful


async def test_worker_killed_between_research_and_write_keeps_the_briefs_and_no_content(chain: Chain) -> None:
    o = chain.obs
    assert o["dup_status"] == "running"  # a kill leaves no failure record
    assert {"analyze", "plan", "research"} <= o["after_kill_checkpoints"] and "sections" not in o[
        "after_kill_checkpoints"
    ]
    assert all(has_brief and not has_content for has_brief, has_content, _ in o["after_kill_rows"].values())


async def test_live_heartbeat_blocks_a_third_worker_and_the_sweeper_requeues_a_dead_one(chain: Chain) -> None:
    assert chain.obs["live_refused"] == (RunOutcome(succeeded=False, retry=False), [])
    requeued, listed = chain.obs["sweep_requeued"]
    assert requeued >= 1 and listed


async def test_resume_after_a_kill_skips_finished_work_and_capacity_loss_is_retryable(chain: Chain) -> None:
    out, status, code, calls = chain.obs["capacity"]
    assert out == RunOutcome(succeeded=False, retry=True)
    assert status == "queued" and code == ErrorCode.PROVIDER_RATE_LIMITED.value
    assert "guide.plan" not in calls and "guide.research" not in calls  # the checkpoints were honoured
    written = chain.obs["capacity_rows"]
    assert sum(written.values()) >= 2 and not all(
        written.values()
    )  # finished chapters kept, the rest to come


async def test_kill_during_revision_leaves_each_chapter_whole_and_verification_consistent(
    chain: Chain,
) -> None:
    rows = chain.obs["revision_kill_rows"]
    verified = [k for k, (done, _) in rows.items() if done]
    assert len(verified) == 1  # the first chapter finished before the kill; the second was mid-revision
    for _, (done, status) in rows.items():
        assert (status in ("ready", "degraded")) == done  # `verification` is the only finality marker
    assert chain.obs["revision_kill_calls"].count("guide.revise") == 2


async def test_resume_after_revision_kill_does_not_touch_verified_chapters_and_finishes(chain: Chain) -> None:
    out, status, calls = chain.obs["final"]
    assert out == RunOutcome(succeeded=True, retry=False) and status == "succeeded"
    assert calls == []  # the previous attempt had already written and verified everything
    assert chain.obs["verified_unchanged"] is True


async def test_regenerate_while_the_analysis_is_unfinished_is_refused_and_changes_nothing(
    chain: Chain,
) -> None:
    status, code, same_job = chain.obs["regen_during_run"]
    assert (status, code) == (409, "PROJECT_NOT_READY") and same_job is True
    assert chain.obs["regen_rows_during_run"] == 0


async def test_database_paused_for_a_whole_attempt_leaves_a_job_the_sweeper_can_resume(chain: Chain) -> None:
    assert chain.obs["db_down_raised"] == "OperationalError"  # nothing could record the failure ...
    status, code = chain.obs["db_down_job"]
    assert status == "running" and code is None  # ... so the row says "running" and its heartbeat goes stale


async def test_database_refusing_a_checkpoint_write_is_a_retryable_failure_not_a_dead_job(
    chain: Chain,
) -> None:
    out, status, code, calls = chain.obs["checkpoint_blip"]
    assert out == RunOutcome(succeeded=False, retry=True)
    assert status == "queued" and code == ErrorCode.STORE_UNAVAILABLE.value
    assert calls.count("guide.critique") >= 1  # the remaining chapters were verified before the write failed


async def test_job_redelivered_after_success_is_a_no_op(chain: Chain) -> None:
    assert chain.obs["redelivery"] == (RunOutcome(succeeded=True, retry=False), [])
    assert chain.obs["redelivery_unchanged"] is True


async def test_no_stage_left_duplicate_rows_after_all_the_interruptions(
    rt: Runtime, chain: Chain, seed_project: Callable[..., Awaitable[Any]]
) -> None:
    """A clean run of the same repository holds exactly what the interrupted one holds."""
    clean = await seed_project()
    try:
        assert (await run_analysis(rt, FaultyLLM(), clean.job_id)).succeeded
        expected = await _counts(rt, clean.project_id)
    finally:
        await cleanup_project(rt, clean.project_id)
    got = chain.obs["final_counts"]
    for key in ("vectors", "graph_nodes", "pg.chunks", "pg.summaries", "pg.guide_sections", "pg.diagrams"):
        assert got[key] == expected[key], key


# ---- regeneration and deletion ------------------------------------------------------------------------------------


async def test_regeneration_killed_midway_leaves_the_chapter_whole_and_rerun_swaps_it_once(
    rt: Runtime, chain: Chain, settings: Settings
) -> None:
    seeded, pid = chain.seeded, chain.seeded.project_id
    key, other = chain.plan_keys[0], chain.plan_keys[1]
    base = f"{API}/projects/{pid}"
    queue = _Queue()
    async with _api(settings, queue) as client:
        client.cookies.set("unfold_session", seeded.token)
        created = await client.post(f"{base}/sections/{key}/regenerate")
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        # one at a time: the same chapter is the same job; another chapter meanwhile is refused
        assert (await client.post(f"{base}/sections/{key}/regenerate")).json()["job_id"] == job_id
        busy = await client.post(f"{base}/sections/{other}/regenerate")
        assert busy.status_code == 409 and busy.json()["error"]["code"] == "CONFLICT"
        # (the app's start-up sweep may also hand back unrelated stale jobs of the shared test database)
        assert queue.analyses.count(job_id) == 1

        before = {k: _frozen(r) for k, r in (await _rows(rt, pid)).items()}
        kill = FaultyLLM([Fault("guide.revise", 1, Kill())])
        await _run_until_killed(rt, kill, job_id)
        killed = {k: _frozen(r) for k, r in (await _rows(rt, pid)).items()}
        assert killed == before  # the chapter on screen is untouched until the new one is verified
        project = (await client.get(f"{API}/projects/{pid}")).json()
        assert project["job"]["status"] == "succeeded"  # a stuck regeneration never bricks a finished guide

        await _age_heartbeat(rt, job_id)
        done = FaultyLLM()
        assert (await run_analysis(rt, done, job_id)).succeeded
        after = {k: _frozen(r) for k, r in (await _rows(rt, pid)).items()}
        assert after[key]["version"] == before[key]["version"] + 1  # swapped in once, not once per attempt
        assert {k: v for k, v in after.items() if k != key} == {k: v for k, v in before.items() if k != key}
        assert after[key]["verification"] is not None and UNSUPPORTED not in json.dumps(after[key]["content"])
        await _assert_consistent(rt, pid, seeded.job_id, finished=True)

        # delivered once more after it succeeded: nothing changes
        again = FaultyLLM()
        assert (await run_analysis(rt, again, job_id)).succeeded and again.calls == []
        chain.obs["regen_job_id"] = job_id


async def test_deleting_a_project_with_regenerations_leaves_nothing_behind(
    rt: Runtime, chain: Chain, settings: Settings
) -> None:
    seeded, pid = chain.seeded, chain.seeded.project_id
    regen_job = chain.obs.get("regen_job_id")
    assert regen_job, "the regeneration test must run first"
    key = chain.plan_keys[1]
    base = f"{API}/projects/{pid}"
    async with rt.db.session() as s:
        jobs = [
            str(i)
            for i in (
                await s.execute(select(AnalysisJobRow.id).where(AnalysisJobRow.project_id == uuid.UUID(pid)))
            ).scalars()
        ]
    # usage and rate-limit keys the way a live gateway leaves them, for the analysis job and every regeneration
    for j in jobs:
        await rt.redis.client.hset(f"usage:{j}", mapping={"calls": 3})
    async with _api(settings, _Queue()) as client:
        client.cookies.set("unfold_session", seeded.token)
        second = await client.post(f"{base}/sections/{key}/regenerate")  # another regeneration, left queued
        assert second.status_code == 202, second.text
        jobs.append(second.json()["job_id"])
        await rt.redis.client.hset(f"usage:{jobs[-1]}", mapping={"calls": 1})
        # a regeneration whose worker is alive blocks deletion until it is gone
        async with rt.db.transaction() as s:
            await s.execute(
                update(AnalysisJobRow)
                .where(AnalysisJobRow.id == uuid.UUID(jobs[-1]))
                .values(status="running", heartbeat_at=func.now())
            )
        assert (await client.delete(f"{base}")).status_code in (200, 202, 204)
        assert await cleanup_project(rt, pid) == {"deferred": 1}
        await _age_heartbeat(rt, jobs[-1])
        counts = await cleanup_project(rt, pid)
    assert counts.get("projects") == 1 and not counts.get("deferred")

    left: dict[str, int] = {}
    async with rt.db.session() as s:
        for table in Base.metadata.sorted_tables:
            if "project_id" in table.c:
                where, params = "project_id = :p", {"p": pid}
            elif table.name == "projects":
                where, params = "id = :p", {"p": pid}
            elif table.name == "job_checkpoints":
                where, params = "job_id = ANY(CAST(:j AS uuid[]))", {"j": jobs}
            else:
                continue
            n = (
                await s.execute(text(f"SELECT count(*) FROM {table.name} WHERE {where}"), params)
            ).scalar_one()
            if n:
                left[f"pg.{table.name}"] = int(n)
    keys = []
    async for k in rt.redis.client.scan_iter(match="*", count=1000):
        name = k.decode() if isinstance(k, bytes) else str(k)
        if pid in name or any(j in name for j in jobs):
            keys.append(name)
    assert left == {} and keys == [], (left, keys)
    blobs = list(
        rt.objects._client.list_objects(rt.objects.bucket, prefix=project_prefix(pid), recursive=True)
    )  # type: ignore[attr-defined]
    assert blobs == []
    vectors = await _counts_after_delete(rt, pid)
    assert vectors == {"vectors": 0, "graph_nodes": 0}
    # idempotent: a second pass over a project that is gone finds nothing to remove
    again = await cleanup_project(rt, pid)
    assert {k: v for k, v in again.items() if v} == {}


async def _counts_after_delete(rt: Runtime, pid: str) -> dict[str, int]:
    counts = await _counts(rt, pid)
    return {k: v for k, v in counts.items() if k in ("vectors", "graph_nodes")}


# ---- the real gateway: 429, timeouts, circuit, key pool, budget --------------------------------------------------


@dataclass
class Gateway:
    llm: LLMGateway
    provider: ScriptedProvider
    circuit: CircuitBreaker
    keys: KeyPool
    prefix: str


def _gateway(rt: Runtime, *, prefix: str | None = None, **settings: Any) -> Gateway:
    prefix = prefix or f"p5r:{uuid.uuid4().hex[:10]}"
    provider = ScriptedProvider()
    circuit = CircuitBreaker(rt.redis.client, prefix=prefix, threshold=1, open_seconds=600.0)
    keys = KeyPool(rt.redis.client, prefix=prefix)
    cfg = rt.settings.model_copy(update=settings) if settings else rt.settings
    llm = LLMGateway(
        cfg, rt.redis, {"gemini": provider}, prefix=prefix, circuit=circuit, keys=keys, backoff_base_s=0.001
    )  # type: ignore[arg-type]
    return Gateway(llm, provider, circuit, keys, prefix)


@pytest.fixture(scope="module")
async def provider_faults(
    rt: Runtime, seed_project: Callable[..., Awaitable[Any]]
) -> AsyncIterator[dict[str, Any]]:
    """One project through the real gateway while the provider rate-limits, rejects a key, errors and hangs."""
    seeded = await seed_project()
    pid, job = seeded.project_id, seeded.job_id
    obs: dict[str, Any] = {"seeded": seeded}
    gw = _gateway(rt)
    flaky = FlakyDB(rt.db)
    inner = rt.db
    _swap_db(rt, flaky)
    try:
        # the database refuses the checkpoint of the dense index after the vectors were written: the stage reruns
        flaky.fail_checkpoint = "dense"
        first = await run_analysis(rt, gw.llm, job)
        row = await _job(rt, job)
        obs["dense_blip"] = (first, row.status, row.error_code)
        obs["dense_blip_counts"] = await _counts(rt, pid)
        gw.provider.calls.clear()

        s = gw.provider.script
        s["guide.plan"] = [
            lambda k: AuthError("401") if k == 0 else None,
            RateLimited("429", retry_after_s=0.2),
        ]
        s["guide.section"] = [
            TransientError("503")
        ] * 3  # the primary model's retries are used up: circuit opens
        s["guide.critique"] = [5.0] * 6  # does not answer within the request timeout, on every candidate
        await _age_heartbeat(rt, job)
        out = await run_analysis(rt, gw.llm, job)
        row = await _job(rt, job)
        obs["faults_run"] = (out, row.status, row.error_code)
        obs["usage_after_faults"] = await gw.llm.usage(job)
        obs["circuit"] = {m: await gw.circuit.state("gemini", m) for m in ("g-fast", "g-bal", "g-reason")}
        obs["key_health"] = await gw.keys.health("gemini", 2)
        obs["rows_after_faults"] = {
            k: (r.content is not None, r.verification is not None, r.status)
            for k, r in (await _rows(rt, pid)).items()
        }
        await _assert_consistent(rt, pid, job, finished=False)

        # the provider recovers; the circuit's open period is cut short so the half-open probe can run
        gw.provider.script.clear()
        for m in ("g-fast", "g-bal", "g-reason"):
            await gw.circuit.record_success("gemini", m)
        await _age_heartbeat(rt, job)
        out = await run_analysis(rt, gw.llm, job)
        row = await _job(rt, job)
        obs["recovered"] = (out, row.status)
        obs["usage_final"] = await gw.llm.usage(job)
        await _assert_consistent(rt, pid, job, finished=True)
        obs["final_counts"] = await _counts(rt, pid)
        yield obs
    finally:
        _swap_db(rt, inner)
        await rt.redis.delete_prefix(gw.prefix)
        with contextlib.suppress(Exception):
            await cleanup_project(rt, pid)


async def test_429_rejected_key_and_transient_errors_are_absorbed_by_the_gateway(
    provider_faults: dict[str, Any],
) -> None:
    usage = provider_faults["usage_after_faults"]
    assert usage["rate_limited"] >= 1  # the 429 was waited out on the same model
    assert usage["retries"] >= 4  # 429 retry + key switch + three transient retries
    health = provider_faults["key_health"]
    assert (
        health[0]["disabled"] is True and health[1]["healthy"] is True
    )  # the rejected key left the rotation
    assert (
        provider_faults["circuit"]["g-bal"] is CircuitState.OPEN
    )  # the failing model is skipped from now on


async def test_provider_that_stops_answering_mid_guide_is_a_retryable_failure_that_resumes(
    provider_faults: dict[str, Any],
) -> None:
    out, status, code = provider_faults["faults_run"]
    assert out == RunOutcome(succeeded=False, retry=True)
    assert status == "queued" and code == ErrorCode.PROVIDER_RATE_LIMITED.value
    rows = provider_faults["rows_after_faults"]
    assert all(written for written, _, _ in rows.values())  # every chapter was written before verification
    assert not any(verified for _, verified, _ in rows.values())  # and none is presented as verified
    assert provider_faults["recovered"] == (RunOutcome(succeeded=True, retry=False), "succeeded")
    final = provider_faults["usage_final"]["by_purpose"]
    assert final["guide.plan"]["calls"] == 1  # the plan was never asked for twice


async def test_database_refusing_the_dense_index_checkpoint_reruns_that_stage_without_duplicates(
    provider_faults: dict[str, Any], chain: Chain
) -> None:
    out, status, code = provider_faults["dense_blip"]
    assert out == RunOutcome(succeeded=False, retry=True)
    assert status == "queued" and code == ErrorCode.STORE_UNAVAILABLE.value
    # the stage ran twice (its vectors were written before the checkpoint failed) and holds one set of points
    assert provider_faults["final_counts"]["vectors"] == provider_faults["dense_blip_counts"]["vectors"]
    assert provider_faults["final_counts"]["vectors"] == chain.obs["final_counts"]["vectors"]
    assert provider_faults["final_counts"]["pg.chunks"] == chain.obs["final_counts"]["pg.chunks"]


async def test_budget_exhaustion_degrades_enrichments_and_fails_typed_before_writing_is_starved(
    rt: Runtime, seed_project: Callable[..., Awaitable[Any]], provider_faults: dict[str, Any]
) -> None:
    by_purpose = provider_faults["usage_final"]["by_purpose"]
    p1_calls = sum(by_purpose.get(p, {}).get("calls", 0) for p in P1_PURPOSES)
    assert p1_calls >= 3

    starved, enough = await seed_project(), await seed_project()
    prefixes = [f"p5r:{uuid.uuid4().hex[:10]}" for _ in range(2)]
    try:
        # a budget that runs out while the chapters are being written ...
        tiny = _gateway(rt, prefix=prefixes[0], analysis_llm_call_budget=2, llm_request_timeout_seconds=60.0)
        # ... and one that covers writing but not the enrichments (P2 is refused first)
        just = _gateway(
            rt, prefix=prefixes[1], analysis_llm_call_budget=p1_calls, llm_request_timeout_seconds=60.0
        )
        out_tiny, out_just = await asyncio.gather(
            run_analysis(rt, tiny.llm, starved.job_id), run_analysis(rt, just.llm, enough.job_id)
        )

        # typed, final, nothing half written: what was written is kept
        row = await _job(rt, starved.job_id)
        assert out_tiny == RunOutcome(succeeded=False, retry=False)
        assert row.status == "failed" and row.error_code == ErrorCode.LLM_BUDGET_EXHAUSTED.value
        assert row.retryable is False
        await _assert_consistent(rt, starved.project_id, starved.job_id, finished=False)
        written = [k for k, r in (await _rows(rt, starved.project_id)).items() if r.content is not None]
        assert written  # the chapters finished before the budget ran out are still there
        assert (await tiny.llm.usage(starved.job_id))["budget_rejections"] >= 1

        # degraded, not failed: critique and revision were refused, the deterministic repair stood in
        row = await _job(rt, enough.job_id)
        assert out_just == RunOutcome(succeeded=True, retry=False) and row.status == "succeeded"
        assert {"guide_critique", "guide_revision"} <= set(row.stage_progress["degraded"])
        assert (await just.llm.usage(enough.job_id))["budget_rejections"] >= 1
        for r in (await _rows(rt, enough.project_id)).values():
            assert r.verification is not None and r.status == "degraded"
            assert UNSUPPORTED not in _text(r)  # the claim nobody could check was qualified or removed
        await _assert_consistent(rt, enough.project_id, enough.job_id, finished=True)
    finally:
        for prefix in prefixes:
            await rt.redis.delete_prefix(prefix)
        for seeded in (starved, enough):
            await cleanup_project(rt, seeded.project_id)


# ---- a job that failed for good -------------------------------------------------------------------------------


async def test_a_job_that_failed_for_good_and_is_delivered_again_keeps_its_failure_record(
    rt: Runtime, seed_project: Callable[..., Awaitable[Any]]
) -> None:
    """At-least-once delivery: a second copy of a finished-and-failed job must not flip it back to running and
    must not replace the typed error with `INTERNAL_ERROR` (a failed stage is terminal)."""
    import io
    import zipfile

    seeded = await seed_project()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("proj/app.py", "print('ok')\n")
        zf.writestr("../../escape.py", "boom\n")
    await rt.objects.put_bytes(
        f"{project_prefix(seeded.project_id)}upload/source.zip", buf.getvalue(), "application/zip"
    )
    try:
        first = await run_analysis(rt, ScriptedLLM(), seeded.job_id)
        before = await _job(rt, seeded.job_id)
        assert first == RunOutcome(succeeded=False, retry=False) and before.status == "failed"
        assert before.error_code == ErrorCode.UNSAFE_ARCHIVE.value

        llm = ScriptedLLM()
        again = await run_analysis(rt, llm, seeded.job_id)
        after = await _job(rt, seeded.job_id)
        assert again == RunOutcome(succeeded=False, retry=False) and llm.calls == []
        assert (after.status, after.error_code, after.attempt, after.current_stage) == (
            before.status,
            before.error_code,
            before.attempt,
            before.current_stage,
        )
    finally:
        await cleanup_project(rt, seeded.project_id)
