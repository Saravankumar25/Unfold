"""Per-chapter investigator (task 3.5) on the fastapi_rag fixture: real retrieval, real RepoMap, scripted model."""

from __future__ import annotations

import dataclasses
import gc
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import ValidationError

from unfold.context.budget import estimate_tokens
from unfold.core.config import Settings
from unfold.core.errors import ErrorCode, UnfoldError
from unfold.domain.enums import Proof
from unfold.evaluation.corpus import FixtureProject, MemoryChunks, load_fixture
from unfold.evaluation.scripted import ScriptedLLM
from unfold.guide.models import ChapterPlan, ResearchBrief
from unfold.guide.research import COVERAGE_TARGET, Investigator, ResearchCaps, render_brief
from unfold.understand.architecture import build_architecture
from unfold.understand.repomap import RepoMap


@pytest.fixture(scope="module")
def fx() -> Iterator[FixtureProject]:
    yield load_fixture("fastapi_rag")
    gc.collect()


def _caps(**override: int) -> ResearchCaps:
    """The settings' defaults, with some limits changed."""
    return dataclasses.replace(ResearchCaps.from_settings(Settings(_env_file=None)), **override)


def _inv(
    fx: FixtureProject, caps: ResearchCaps | None = None, llm: ScriptedLLM | None = None
) -> Investigator:
    return Investigator(
        llm=llm or ScriptedLLM(),
        retriever=fx.retriever(),
        chunks=MemoryChunks(fx.chunks),
        rm=RepoMap(fx.ir),
        arch=build_architecture(fx.ir),
        caps=caps or _caps(),
        project_id=fx.project_id,
        job_id="job",
    )


def _plan(targets: list[str], questions: list[str], key: str = "ch") -> ChapterPlan:
    return ChapterPlan(
        key=key,
        title="Chapter",
        kind="core",
        purpose="Explain it",
        target_minutes=3,
        questions=questions,
        targets=targets,
        expected_evidence=["definition", "flow"],
    )


def _id(fx: FixtureProject, name: str) -> str:
    ref = RepoMap(fx.ir).symbol(name)
    assert ref is not None, name
    return ref.id


ANSWERABLE = [
    "What does answer_query do when a question arrives?",
    "How does answer_query search for chunks with the retriever?",
    "What does LLMClient.answer return?",
]
UNANSWERABLE = [
    "How does the quantum flux capacitor calibrate?",
    "Why is the zebra striped?",
    "Where is the moon?",
]


async def test_brief_is_built_from_stored_chunks_flows_and_tests(fx: FixtureProject) -> None:
    handler, llm_answer = _id(fx, "answer_query"), _id(fx, "LLMClient.answer")
    brief = await _inv(fx).research(_plan([handler, llm_answer], ANSWERABLE))

    chunk_ids = {c.id for c in fx.chunks}
    assert brief.findings and all(set(f.evidence) <= chunk_ids for f in brief.findings)
    assert brief.coverage >= COVERAGE_TARGET and brief.stopped_by == "coverage"
    assert brief.spent.rounds == 1 and brief.spent.retrievals <= len(ANSWERABLE)
    assert brief.spent.llm_calls == 0  # findings are deterministic; the model is not asked
    assert [f.question_id for f in brief.findings] == sorted(f.question_id for f in brief.findings)
    # statements are built from IR facts, not prose: location and symbol are in them
    stated = [
        f
        for f in brief.findings
        if "answer_query" in f.statement and "app/api/routes/query.py:" in f.statement
    ]
    assert stated and all(f.proof in (Proof.STATIC, Proof.HEURISTIC) for f in stated)
    # the handler is an entry point: its flow is traced and reported
    assert brief.flows and brief.flows[0].entry.name == "answer_query"
    assert any(f.statement.startswith("Flow from `POST /query`") for f in brief.findings)
    assert brief.spent.tokens == estimate_tokens(render_brief(brief))


async def test_a_question_naming_its_target_is_answered_by_lookup_without_retrieval(
    fx: FixtureProject,
) -> None:
    named = await _inv(fx).research(
        _plan([_id(fx, "answer_query")], ["What does answer_query do?", "Who calls answer_query?"])
    )
    assert named.spent.retrievals == 0 and named.coverage == 1.0
    assert all(f.proof is not Proof.INFERRED for f in named.findings[:1])

    vague = await _inv(fx).research(
        _plan(
            [_id(fx, "answer_query")], ["How are cited answers produced?", "What does the retriever return?"]
        )
    )
    assert vague.spent.retrievals >= 1  # nothing names a target: hybrid retrieval does the work


async def test_test_lookup_finds_the_tests_of_a_target_file(fx: FixtureProject) -> None:
    chunking = RepoMap(fx.ir).symbol("app/services/chunking.py")
    assert chunking is not None
    brief = await _inv(fx).research(
        _plan([chunking.id], ["What does the chunking module split text into?", "How big is a chunk?"])
    )
    assert brief.tests == ["tests/test_chunking.py"]


async def test_research_is_deterministic(fx: FixtureProject) -> None:
    plan = _plan([_id(fx, "answer_query")], ANSWERABLE)
    assert await _inv(fx).research(plan) == await _inv(fx).research(plan)


async def test_unanswerable_questions_use_the_extra_round_then_stop_and_stay_open(fx: FixtureProject) -> None:
    cfg = RepoMap(fx.ir).symbol("app/core/config.py")
    assert cfg is not None
    plan = _plan([cfg.id], UNANSWERABLE).model_copy(update={"expected_evidence": ["config", "test"]})
    brief = await _inv(fx).research(plan)
    assert brief.coverage == 0.0 and brief.open_questions == UNANSWERABLE
    assert (
        brief.spent.rounds == 2 and brief.stopped_by == "rounds"
    )  # first pass + exactly one targeted extra round
    assert brief.spent.retrievals == 2 * len(UNANSWERABLE)
    assert all(f.proof is Proof.INFERRED for f in brief.findings)  # leads only, never counted as answers


async def test_extra_round_needs_a_missing_facet(fx: FixtureProject) -> None:
    cfg = RepoMap(fx.ir).symbol("app/core/config.py")
    assert cfg is not None
    plan = _plan([cfg.id], UNANSWERABLE[:2]).model_copy(update={"expected_evidence": ["definition"]})
    brief = await _inv(fx).research(plan)
    assert brief.spent.rounds == 1 and brief.spent.retrievals == 2 and brief.stopped_by == "exhausted"


async def test_retrieval_cap(fx: FixtureProject) -> None:
    cfg = RepoMap(fx.ir).symbol("app/core/config.py")
    assert cfg is not None
    brief = await _inv(fx, _caps(retrievals=1)).research(_plan([cfg.id], UNANSWERABLE))
    assert brief.spent.retrievals == 1 and brief.stopped_by == "retrievals"
    assert len(brief.open_questions) == len(UNANSWERABLE)


async def test_round_cap(fx: FixtureProject) -> None:
    cfg = RepoMap(fx.ir).symbol("app/core/config.py")
    assert cfg is not None
    brief = await _inv(fx, _caps(rounds=1)).research(_plan([cfg.id], UNANSWERABLE))
    assert brief.spent.rounds == 1 and brief.stopped_by == "rounds"
    assert brief.spent.retrievals == len(UNANSWERABLE)


async def test_llm_cap_and_decomposition_of_a_thin_plan(fx: FixtureProject) -> None:
    thin = _plan([_id(fx, "answer_query")], ["What does answer_query do?"])
    blocked = ScriptedLLM()
    brief = await _inv(fx, _caps(llm_calls=0), blocked).research(thin)
    assert blocked.calls == [] and brief.spent.llm_calls == 0 and len(brief.questions) == 1
    assert brief.stopped_by in {"llm_calls", "coverage"}

    allowed = ScriptedLLM()
    brief = await _inv(fx, _caps(llm_calls=1), allowed).research(thin)
    assert allowed.calls == ["guide.research"] and brief.spent.llm_calls == 1
    assert len(brief.questions) == 3 and brief.questions[0] == thin.questions[0]
    prompt = allowed.requests[0].messages[0].content
    assert "<repository_data" in prompt and "repository_data" in allowed.requests[0].system


@pytest.mark.parametrize("rounds", [0, 1, 2])
@pytest.mark.parametrize("retrievals", [0, 1, 4, 12])
@pytest.mark.parametrize("llm_calls", [0, 1])
async def test_spent_never_exceeds_any_cap(
    fx: FixtureProject, rounds: int, retrievals: int, llm_calls: int
) -> None:
    caps = _caps(rounds=rounds, retrievals=retrievals, llm_calls=llm_calls)
    cfg = RepoMap(fx.ir).symbol("app/core/config.py")
    assert cfg is not None
    thin = _plan([cfg.id], ["Where is the moon?"])
    llm = ScriptedLLM()
    brief = await _inv(fx, caps, llm).research(thin)
    assert brief.spent.rounds <= rounds
    assert brief.spent.retrievals <= retrievals
    assert brief.spent.llm_calls <= llm_calls and len(llm.calls) == brief.spent.llm_calls


async def test_brief_token_cap_is_enforced_and_coverage_stays_honest(fx: FixtureProject) -> None:
    plan = _plan([_id(fx, "answer_query"), _id(fx, "LLMClient.answer")], ANSWERABLE)
    full = await _inv(fx).research(plan)
    capped = await _inv(fx, _caps(brief_tokens=120)).research(plan)
    assert estimate_tokens(render_brief(capped)) <= 120 and capped.spent.tokens <= 120
    assert capped.stopped_by == "tokens" and len(capped.findings) < len(full.findings)
    assert capped.coverage <= full.coverage
    answered = {f.question_id for f in capped.findings if f.proof is not Proof.INFERRED}
    assert capped.coverage == pytest.approx(round(len(answered) / len(ANSWERABLE), 2))
    assert set(capped.open_questions) == {q for i, q in enumerate(ANSWERABLE) if i not in answered}


def test_brief_round_trips_through_json(fx: FixtureProject) -> None:
    brief = ResearchBrief(
        chapter_key="c",
        questions=["q?"],
        findings=[],
        flows=[],
        tests=[],
        open_questions=["q?"],
        coverage=0.0,
        spent={"rounds": 1},  # type: ignore[arg-type]
    )
    assert ResearchBrief.model_validate(brief.model_dump(mode="json")) == brief


class _BudgetRefused(ScriptedLLM):
    async def generate_structured(self, req: Any, schema: Any) -> Any:
        self.calls.append(req.purpose)
        raise UnfoldError("budget", code=ErrorCode.LLM_BUDGET_EXHAUSTED, retryable=False)


async def test_a_refused_decomposition_falls_back_to_the_plans_own_question(fx: FixtureProject) -> None:
    thin = _plan([_id(fx, "answer_query")], ["What does answer_query do?"])
    llm = _BudgetRefused()
    brief = await _inv(fx, _caps(), llm).research(thin)
    assert llm.calls == ["guide.research"]  # it was asked, was refused, and the chapter is still researched
    assert brief.questions == thin.questions and brief.findings


def test_more_research_rounds_than_the_loop_runs_is_a_configuration_error() -> None:
    assert Settings(_env_file=None, research_max_rounds=2).research_max_rounds == 2
    with pytest.raises(ValidationError):
        Settings(_env_file=None, research_max_rounds=3)
