"""Verify, revise, input hash and single-chapter regeneration against the real stores.

The only double is the model (`ScriptedLLM`, here writing a chapter with one uncited, unsupported sentence so that
every chapter has something to repair). Parsing, indexing, retrieval, verification, persistence and the API are real.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from unfold.cleanup.service import cleanup_project
from unfold.core.errors import ErrorCode, ProviderCapacityError, UnfoldError
from unfold.domain.enums import ClaimKind, ClaimStatus
from unfold.evaluation.scripted import ScriptedLLM, evidence_blocks
from unfold.guide.models import DraftClaim, ParagraphBlock, SectionContent, SectionDraft
from unfold.guide.rewrite import RewriteDraft
from unfold.guide.verification_models import SemanticCritique
from unfold.llm.schemas import LLMRequest
from unfold.orchestration.pipeline import run_analysis
from unfold.persistence.postgres.models import AnalysisJobRow, GuideSectionRow, JobCheckpointRow
from unfold.runtime import Runtime

pytestmark = pytest.mark.integration

API = "/api/v1"
UNSUPPORTED = "It retries failed calls with exponential backoff"


class GappyLLM(ScriptedLLM):
    """Writes `<cited sentence>. <uncited, unsupported sentence>.` plus a claim for each."""

    def __init__(
        self,
        *,
        fail_revise_from: int | None = None,
        refuse_revise: bool = False,
        refuse_critique: bool = False,
    ) -> None:
        super().__init__()
        self.fail_revise_from = fail_revise_from
        self.refuse_revise = refuse_revise
        self.refuse_critique = refuse_critique
        self.revisions = 0

    def _section(self, req: LLMRequest) -> SectionDraft:
        base = super()._section(req)
        label, path, _text = evidence_blocks(req.messages[0].content)[0]
        text = f"The code in `{path}` is part of the project [{label}]. {UNSUPPORTED}."
        return SectionDraft(
            content=SectionContent(summary=base.content.summary, blocks=[ParagraphBlock(text=text)]),
            claims=[
                DraftClaim(text=f"`{path}` is part of the project", kind="project_fact", evidence=[label]),
                DraftClaim(text=UNSUPPORTED, kind="project_fact", evidence=[]),
            ],
        )

    async def generate_structured(self, req: LLMRequest, schema: type[BaseModel]) -> Any:
        refused = UnfoldError("budget", code=ErrorCode.LLM_BUDGET_EXHAUSTED, retryable=False)
        if schema is SemanticCritique and self.refuse_critique:
            self.calls.append(req.purpose)
            raise refused
        if schema is RewriteDraft:
            index, self.revisions = self.revisions, self.revisions + 1
            if self.refuse_revise:
                self.calls.append(req.purpose)
                raise refused
            if self.fail_revise_from is not None and index >= self.fail_revise_from:
                self.calls.append(req.purpose)
                raise ProviderCapacityError("scripted capacity failure", retryable=True)
        return await super().generate_structured(req, schema)


async def _rows(rt: Runtime, project_id: str) -> dict[str, GuideSectionRow]:
    async with rt.db.session() as s:
        res = await s.execute(
            select(GuideSectionRow).where(GuideSectionRow.project_id == uuid.UUID(project_id))
        )
        return {r.section_key: r for r in res.scalars()}


def _frozen(row: GuideSectionRow) -> dict[str, Any]:
    """Everything a chapter stores. Diagram blocks are the visuals stage's, not the chapter's own text."""
    content = row.content and {
        **row.content,
        "blocks": [b for b in row.content["blocks"] if b["type"] != "diagram"],
    }
    return {"content": content} | {
        k: getattr(row, k)
        for k in (
            "claims",
            "evidence_summary",
            "quality_scores",
            "research",
            "verification",
            "input_hash",
            "status",
            "version",
            "position",
            "title",
        )
    }


def _text(row: GuideSectionRow) -> str:
    assert row.content
    return " ".join(str(b.get("text", "")) for b in row.content["blocks"])


async def _job(rt: Runtime, job_id: str) -> AnalysisJobRow:
    async with rt.db.session() as s:
        job = await s.get(AnalysisJobRow, uuid.UUID(job_id))
    assert job is not None
    return job


async def _stages(rt: Runtime, project_id: str) -> list[str]:
    return [e["stage"] for _, e in await rt.progress.read_after(project_id) if e["type"] == "stage"]


@pytest.fixture(scope="module")
async def gappy(
    runtime: Runtime, seed_project: Callable[..., Awaitable[Any]]
) -> AsyncIterator[tuple[Any, GappyLLM]]:
    seeded = await seed_project()
    llm = GappyLLM()
    assert (await run_analysis(runtime, llm, seeded.job_id)).succeeded
    yield seeded, llm
    await cleanup_project(runtime, seeded.project_id)


# ---- verify and revise ------------------------------------------------------------------------------------------


async def test_every_chapter_is_verified_revised_and_no_unsupported_statement_is_left(
    runtime: Runtime, gappy: tuple[Any, GappyLLM]
) -> None:
    seeded, llm = gappy
    rows = await _rows(runtime, seeded.project_id)
    n = len(rows)
    assert llm.calls.count("guide.critique") == n  # one critique per chapter
    assert llm.calls.count("guide.revise") == n  # one repair round fixes the single defect
    for row in rows.values():
        v = row.verification
        assert v and v["result"]["verdict"] == "accept" and v["rounds"] == 1 and v["critique"] is True
        assert row.status == "ready" and row.quality_scores["verdict"] == "accept"
        assert UNSUPPORTED not in _text(row)  # the sentence was rewritten, not left standing
        facts = [c for c in row.claims if c["kind"] == ClaimKind.PROJECT_FACT.value]
        assert facts and all(c["status"] == ClaimStatus.VERIFIED.value for c in facts)
        assert row.input_hash and len(row.input_hash) == 64
    stages = await _stages(runtime, seeded.project_id)
    assert (
        stages.index("VERIFYING_GUIDE") < stages.index("REVISING_GUIDE") < stages.index("GENERATING_VISUALS")
    )


async def test_an_exhausted_budget_degrades_to_removal_instead_of_failing_or_keeping_the_claim(
    runtime: Runtime, seed_project: Callable[..., Awaitable[Any]]
) -> None:
    seeded = await seed_project()
    try:
        llm = GappyLLM(refuse_critique=True, refuse_revise=True)
        assert (await run_analysis(runtime, llm, seeded.job_id)).succeeded
        job = await _job(runtime, seeded.job_id)
        assert {"guide_critique", "guide_revision"} <= set(job.stage_progress["degraded"])
        for row in (await _rows(runtime, seeded.project_id)).values():
            assert row.verification and row.verification["stopped_by"] == "budget"
            assert row.verification["critique"] is False and row.status == "degraded"
            assert UNSUPPORTED not in _text(row)  # removed, and so is the claim that made it
            assert [c["text"] for c in row.claims if UNSUPPORTED in c["text"]] == []
            assert "is part of the project" in _text(row)  # the supported sentence stays
            assert row.quality_scores["gaps"] == 1
    finally:
        await cleanup_project(runtime, seeded.project_id)


async def test_resume_after_a_crash_mid_revision_redoes_only_unverified_chapters(
    runtime: Runtime, seed_project: Callable[..., Awaitable[Any]]
) -> None:
    seeded = await seed_project()
    try:
        flaky = GappyLLM(fail_revise_from=1)
        first = await run_analysis(runtime, flaky, seeded.job_id)
        assert not first.succeeded and first.retry
        rows = await _rows(runtime, seeded.project_id)
        verified = {k for k, r in rows.items() if r.verification is not None}
        assert 0 < len(verified) < len(rows)  # one chapter finished; the rest died mid-revision
        before = {k: _frozen(rows[k]) for k in verified}
        async with runtime.db.session() as s:
            done = set(
                (
                    await s.execute(
                        select(JobCheckpointRow.name).where(
                            JobCheckpointRow.job_id == uuid.UUID(seeded.job_id)
                        )
                    )
                ).scalars()
            )
        assert {"research", "sections"} <= done and "verify" not in done

        healthy = GappyLLM()
        assert (await run_analysis(runtime, healthy, seeded.job_id)).succeeded
        todo = len(rows) - len(verified)
        assert healthy.calls.count("guide.critique") == todo and healthy.calls.count("guide.revise") == todo
        assert "guide.section" not in healthy.calls and "guide.research" not in healthy.calls
        after = await _rows(runtime, seeded.project_id)
        assert all(r.verification is not None for r in after.values())
        assert {k: _frozen(after[k]) for k in verified} == before  # verified chapters were not touched
    finally:
        await cleanup_project(runtime, seeded.project_id)


# ---- single-chapter regeneration --------------------------------------------------------------------------------


async def test_regenerate_route_is_owner_scoped_idempotent_and_rate_limited(
    runtime: Runtime,
    gappy: tuple[Any, GappyLLM],
    open_api: Callable[..., Any],
    recording_queue: Any,
) -> None:
    seeded, _ = gappy
    keys = list(await _rows(runtime, seeded.project_id))
    key, other = keys[0], keys[1]
    base = f"{API}/projects/{seeded.project_id}"
    async with open_api(queue=recording_queue, max_regenerations_per_hour=1) as client:
        client.cookies.set("unfold_session", seeded.token)
        assert (await client.post(f"{base}/sections/nope/regenerate")).status_code == 404

        created = await client.post(f"{base}/sections/{key}/regenerate")
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        assert created.json()["section_key"] == key and recording_queue.analyses == [job_id]
        job = await _job(runtime, job_id)
        assert (job.kind, job.section_key, job.status) == ("regenerate", key, "queued")

        # idempotent while it runs: the same job, no second enqueue, no second rate-limit slot
        again = await client.post(f"{base}/sections/{key}/regenerate")
        assert again.status_code == 202 and again.json()["job_id"] == job_id
        assert recording_queue.analyses == [job_id]
        busy = await client.post(f"{base}/sections/{other}/regenerate")
        assert busy.status_code == 409 and busy.json()["error"]["code"] == "CONFLICT"

        # the project still reads as analysed, and progress reports the regeneration separately
        page = (await client.get(f"{base}/progress?after=999999999")).json()
        assert page["job"]["status"] == "succeeded" and page["job"]["kind"] == "analysis"
        assert page["regeneration"]["id"] == job_id and page["regeneration"]["section_key"] == key
        assert (await client.get(f"{API}/projects/{seeded.project_id}")).json()["job"][
            "status"
        ] == "succeeded"

        # another session cannot regenerate it (404, not 403)
        stranger = (await client.post(f"{API}/sessions")).cookies["unfold_session"]
        client.cookies.clear()
        client.cookies.set("unfold_session", stranger)
        assert (await client.post(f"{base}/sections/{key}/regenerate")).status_code == 404
        client.cookies.clear()
        client.cookies.set("unfold_session", seeded.token)

        # run it in a worker: only this chapter changes, the others are byte-identical
        rows = await _rows(runtime, seeded.project_id)
        before = {k: _frozen(r) for k, r in rows.items()}
        llm = GappyLLM()
        assert (await run_analysis(runtime, llm, job_id)).succeeded
        assert llm.calls.count("guide.section") == 1 and "guide.plan" not in llm.calls
        after = await _rows(runtime, seeded.project_id)
        assert {k: _frozen(r) for k, r in after.items() if k != key} == {
            k: v for k, v in before.items() if k != key
        }
        assert after[key].version == rows[key].version + 1
        assert after[key].verification and after[key].verification["result"]["verdict"] == "accept"
        assert after[key].research and after[key].input_hash == rows[key].input_hash  # same inputs, same hash
        done = (await client.get(f"{base}/progress?after=999999999")).json()
        assert done["regeneration"]["status"] == "succeeded" and done["job"]["status"] == "succeeded"

        # the hourly allowance (1 here) is spent: a new regeneration is refused, a repeat is not needed
        limited = await client.post(f"{base}/sections/{other}/regenerate")
        assert limited.status_code == 429 and limited.json()["error"]["code"] == "RATE_LIMITED"


async def test_regenerations_are_also_limited_per_session(
    runtime: Runtime,
    analyzed_project: Callable[..., Awaitable[Any]],
    open_api: Callable[..., Any],
    recording_queue: Any,
) -> None:
    seeded = await analyzed_project()  # a session of its own: the quota counter starts at zero
    try:
        keys = list(await _rows(runtime, seeded.project_id))
        base = f"{API}/projects/{seeded.project_id}"
        async with open_api(
            queue=recording_queue, max_regenerations_per_hour=100, max_regenerations_per_session_hour=1
        ) as client:
            client.cookies.set("unfold_session", seeded.token)
            first = await client.post(f"{base}/sections/{keys[0]}/regenerate")
            assert first.status_code == 202, first.text
            async with runtime.db.transaction() as s:  # the first one is over, so only the quota can refuse
                job = await s.get(AnalysisJobRow, uuid.UUID(first.json()["job_id"]))
                assert job is not None
                job.status = "failed"
            limited = await client.post(f"{base}/sections/{keys[1]}/regenerate")
            assert limited.status_code == 429 and limited.json()["error"]["code"] == "RATE_LIMITED"
    finally:
        await cleanup_project(runtime, seeded.project_id)


async def test_a_failed_regeneration_leaves_the_chapter_and_the_project_intact(
    runtime: Runtime, gappy: tuple[Any, GappyLLM], open_api: Callable[..., Any], recording_queue: Any
) -> None:
    seeded, _ = gappy
    key = next(iter(await _rows(runtime, seeded.project_id)))
    before = _frozen((await _rows(runtime, seeded.project_id))[key])
    base = f"{API}/projects/{seeded.project_id}"
    async with open_api(queue=recording_queue) as client:
        client.cookies.set("unfold_session", seeded.token)
        job_id = (await client.post(f"{base}/sections/{key}/regenerate")).json()["job_id"]
        broken = GappyLLM()

        async def boom(req: LLMRequest, schema: type[BaseModel]) -> Any:
            raise UnfoldError("provider is down", code=ErrorCode.PROVIDER_UNAVAILABLE, retryable=False)

        broken.generate_structured = boom  # type: ignore[method-assign]
        outcome = await run_analysis(runtime, broken, job_id)
        assert not outcome.succeeded and not outcome.retry
        assert (
            _frozen((await _rows(runtime, seeded.project_id))[key]) == before
        )  # swapped in whole or not at all
        page = (await client.get(f"{base}/progress?after=999999999")).json()
        assert page["regeneration"]["status"] == "failed" and page["job"]["status"] == "succeeded"
        assert (await client.get(f"{API}/projects/{seeded.project_id}")).json()["job"][
            "status"
        ] == "succeeded"


# ---- input hash -------------------------------------------------------------------------------------------------


async def test_a_rerun_with_unchanged_inputs_skips_research_writing_and_verification(
    runtime: Runtime, gappy: tuple[Any, GappyLLM]
) -> None:
    seeded, _ = gappy
    rows = await _rows(runtime, seeded.project_id)
    before = {k: _frozen(r) for k, r in rows.items()}
    async with runtime.db.transaction() as s:  # a second analysis job of the same project
        job = AnalysisJobRow(
            project_id=uuid.UUID(seeded.project_id),
            status="queued",
            current_stage="RECEIVED",
            stage_progress={},
            attempt=0,
            created_at=datetime.now(UTC),
        )
        s.add(job)
        await s.flush()
        job_id = str(job.id)
    llm = GappyLLM()
    assert (await run_analysis(runtime, llm, job_id)).succeeded
    for purpose in ("guide.research", "guide.section", "guide.critique", "guide.revise"):
        assert purpose not in llm.calls, purpose
    assert llm.calls.count("guide.plan") == 1  # the plan itself is cheap and decides what is stale
    assert {k: _frozen(r) for k, r in (await _rows(runtime, seeded.project_id)).items()} == before


async def test_only_stale_chapters_are_deleted_and_redone(
    runtime: Runtime, gappy: tuple[Any, GappyLLM]
) -> None:
    seeded, _ = gappy
    rows = await _rows(runtime, seeded.project_id)
    key = list(rows)[2]
    async with runtime.db.transaction() as s:
        (
            await s.execute(
                select(GuideSectionRow).where(
                    GuideSectionRow.project_id == uuid.UUID(seeded.project_id),
                    GuideSectionRow.section_key == key,
                )
            )
        ).scalar_one().input_hash = "not-the-current-hash"
        s.add(  # a chapter the new plan does not have
            GuideSectionRow(
                project_id=uuid.UUID(seeded.project_id),
                section_key="ghost",
                title="Ghost",
                position=99,
                status="ready",
                claims=[],
                content={"summary": "old", "blocks": []},
            )
        )
        job = AnalysisJobRow(
            project_id=uuid.UUID(seeded.project_id),
            status="queued",
            current_stage="RECEIVED",
            stage_progress={},
            attempt=0,
            created_at=datetime.now(UTC),
        )
        s.add(job)
        await s.flush()
        job_id = str(job.id)
    before = {k: _frozen(r) for k, r in rows.items() if k != key}
    llm = GappyLLM()
    assert (await run_analysis(runtime, llm, job_id)).succeeded
    assert llm.calls.count("guide.section") == 1 and llm.calls.count("guide.critique") == 1
    after = await _rows(runtime, seeded.project_id)
    assert "ghost" not in after and set(after) == set(rows)
    assert {k: _frozen(r) for k, r in after.items() if k != key} == before
    assert after[key].verification is not None and after[key].input_hash != "not-the-current-hash"
