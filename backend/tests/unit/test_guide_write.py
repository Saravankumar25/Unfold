"""Chapter writer (task 3.6): the prompt is the research brief plus the excerpts it cites, fenced and redacted."""

from __future__ import annotations

import gc
from collections.abc import Iterator

import pytest

from unfold.domain.enums import Proof
from unfold.evaluation.corpus import FixtureProject, MemoryChunks, load_fixture
from unfold.evaluation.scripted import ScriptedLLM, evidence_blocks
from unfold.guide.models import ChapterPlan, Finding, ResearchBrief, ResearchSpent, SectionDraft
from unfold.guide.research import Investigator, ResearchCaps
from unfold.guide.write import SectionEvidenceMissing, write_chapter
from unfold.llm.schemas import LLMRequest
from unfold.security.fence import UNTRUSTED_POLICY
from unfold.security.secrets import REDACTED
from unfold.understand.architecture import build_architecture
from unfold.understand.repomap import RepoMap

TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2


@pytest.fixture(scope="module")
def fx() -> Iterator[FixtureProject]:
    yield load_fixture("fastapi_rag")
    gc.collect()


def _plan(**kw: object) -> ChapterPlan:
    base: dict[str, object] = {
        "key": "request-lifecycle",
        "title": "Request lifecycle",
        "kind": "core",
        "purpose": "Follow a request",
        "target_minutes": 4,
        "questions": ["What does answer_query do?", "What does LLMClient.answer return?"],
        "depends_on": ["overview"],
    }
    return ChapterPlan(**{**base, **kw})  # type: ignore[arg-type]


async def _brief(fx: FixtureProject, plan: ChapterPlan) -> ResearchBrief:
    rm = RepoMap(fx.ir)
    targets = [r.id for n in ("answer_query", "LLMClient.answer") if (r := rm.symbol(n))]
    inv = Investigator(
        llm=ScriptedLLM(),
        retriever=fx.retriever(),
        chunks=MemoryChunks(fx.chunks),
        rm=rm,
        arch=build_architecture(fx.ir),
        caps=ResearchCaps(),
        project_id=fx.project_id,
        job_id="j",
    )
    return await inv.research(plan.model_copy(update={"targets": targets}))


async def _write(fx: FixtureProject, llm: ScriptedLLM, brief: ResearchBrief, **kw: object):  # type: ignore[no-untyped-def]
    return await write_chapter(
        llm,  # type: ignore[arg-type]
        MemoryChunks(fx.chunks),
        _plan(),
        brief,
        ["Project name: fastapi_rag"],
        kw.pop("dependencies", ()),  # type: ignore[arg-type]
        technical_level="intermediate",
        instruction=None,
        job_id="j",
        project_id=fx.project_id,
    )


async def test_prompt_carries_the_brief_and_only_the_excerpts_it_cites(fx: FixtureProject) -> None:
    brief = await _brief(fx, _plan())
    llm = ScriptedLLM()
    written = await _write(fx, llm, brief)

    prompt = llm.requests[0].messages[0].content
    cited = {c.id for c in fx.chunks if c.id in {i for f in brief.findings for i in f.evidence}}
    shown = evidence_blocks(prompt)
    assert shown and len(shown) <= len(cited)  # a subset of what the brief cites, never a retrieval dump
    paths = {c.path for c in fx.chunks if c.id in cited}
    assert {p for _, p, _ in shown} <= paths
    assert prompt.index("## Research brief") > prompt.index("## Evidence")
    assert "Q1: What does answer_query do?" in prompt
    assert (
        "[E1]" in prompt.split("## Research brief")[1]
    )  # brief findings cite the labels of the excerpts shown
    assert llm.calls == ["guide.section"]
    assert written.evidence and set(written.evidence) == {label for label, _, _ in shown}


async def test_open_questions_are_handed_over_as_uncertainty(fx: FixtureProject) -> None:
    brief = await _brief(fx, _plan(questions=["What does answer_query do?", "Where is the moon?"]))
    assert brief.open_questions
    llm = ScriptedLLM()
    await _write(fx, llm, brief)
    prompt = llm.requests[0].messages[0].content
    assert "Not established by the research" in prompt and "Where is the moon?" in prompt
    assert "Retrieved evidence may be incomplete" in prompt  # compile_context's own uncertainty note


async def test_system_prompt_gates_failure_and_change_blocks_and_keeps_the_fence_policy(
    fx: FixtureProject,
) -> None:
    llm = ScriptedLLM()
    await _write(fx, llm, await _brief(fx, _plan()))
    system = llm.requests[0].system
    assert UNTRUSTED_POLICY in system
    assert "'failure'" in system and "'extension'" in system and "only when a finding" in system
    assert "instead of guessing" in system


async def test_dependency_summaries_are_fenced_and_only_those_passed(fx: FixtureProject) -> None:
    brief = await _brief(fx, _plan())
    llm = ScriptedLLM()
    await _write(fx, llm, brief, dependencies=[("Overview", "What fastapi_rag is.")])
    prompt = llm.requests[0].messages[0].content
    tail = prompt.split("## Earlier chapters this one builds on")[1]
    assert "Overview: What fastapi_rag is." in tail and tail.lstrip().startswith("<repository_data")

    none = ScriptedLLM()
    await _write(fx, none, brief)
    assert "Earlier chapters" not in none.requests[0].messages[0].content


async def test_secrets_in_brief_statements_and_excerpts_never_reach_the_prompt(fx: FixtureProject) -> None:
    chunk = next(c for c in fx.chunks if c.symbol_name)
    dirty = chunk.model_copy(update={"text": chunk.text + f"\nAPI_TOKEN = '{TOKEN}'\n"})
    brief = ResearchBrief(
        chapter_key="c",
        questions=["What is in it?"],
        findings=[
            Finding(
                question_id=0,
                statement=f"Sets token = '{TOKEN}' at x.py:1-2",
                evidence=[chunk.id],
                proof=Proof.STATIC,
            )
        ],
        flows=[],
        tests=[],
        open_questions=[],
        coverage=1.0,
        spent=ResearchSpent(),
    )
    llm = ScriptedLLM()
    store = MemoryChunks([dirty])
    await write_chapter(
        llm,  # type: ignore[arg-type]
        store,
        _plan(),
        brief,
        [],
        technical_level="intermediate",
        instruction=None,
        job_id="j",
        project_id=fx.project_id,
    )
    prompt = llm.requests[0].messages[0].content
    assert TOKEN not in prompt and REDACTED in prompt


async def test_a_brief_without_resolvable_evidence_fails_that_chapter(fx: FixtureProject) -> None:
    brief = ResearchBrief(
        chapter_key="c",
        questions=["q?"],
        findings=[Finding(question_id=0, statement="x", evidence=["no-such-chunk"], proof=Proof.STATIC)],
        flows=[],
        tests=[],
        open_questions=["q?"],
        coverage=0.0,
        spent=ResearchSpent(),
    )
    llm = ScriptedLLM()
    with pytest.raises(SectionEvidenceMissing):
        await _write(fx, llm, brief)
    assert llm.calls == []  # nothing was sent to the model


# ---- contract with the verifier (task 3.7): anchored code blocks and edge claims -----------------------------


class _Fixed:
    """Gateway double that returns one prepared draft."""

    def __init__(self, draft: SectionDraft) -> None:
        self.draft = draft
        self.requests: list[LLMRequest] = []

    async def generate_structured(self, req: LLMRequest, schema: type) -> SectionDraft:
        self.requests.append(req)
        return self.draft


async def test_code_blocks_are_anchored_to_their_excerpt_and_edge_claims_carry_two_ids(
    fx: FixtureProject,
) -> None:
    from unfold.guide.models import CodeBlock, DraftClaim, ParagraphBlock, SectionContent
    from unfold.guide.verify import verify_section
    from unfold.guide.write import _edges

    brief = await _brief(fx, _plan())
    edge = _edges(brief)[0]
    src, dst = edge.src_name.rsplit(".", 1)[-1], edge.dst_name.rsplit(".", 1)[-1]

    chunk = next(
        c for c in fx.chunks if c.id in {i for f in brief.findings for i in f.evidence} and c.symbol_id
    )
    body = [x for x in chunk.text.split("\n", 1)[1].splitlines() if x.strip()][:3]
    draft = SectionDraft(
        content=SectionContent(
            summary="s",
            blocks=[
                ParagraphBlock(text=f"`{src}` calls `{dst}` [E1]."),
                CodeBlock(language="python", code="\n".join(body)),  # no path, no start_line
                CodeBlock(language="python", code="this_is_not_in_any_file()\n"),  # quotes nothing shown
            ],
        ),
        claims=[
            DraftClaim(text=f"`{src}` calls `{dst}`", kind="project_fact", evidence=["E1"]),
            DraftClaim(text=f"`{src}` and `{dst}` are both mentioned", kind="project_fact", evidence=["E1"]),
            DraftClaim(text="Nothing about an edge here", kind="project_fact", evidence=["E1"]),
        ],
    )
    written = await _write(fx, _Fixed(draft), brief)  # type: ignore[arg-type]

    codes = [b for b in written.content.blocks if isinstance(b, CodeBlock)]
    assert len(codes) == 1  # the unlocatable excerpt is dropped, not shown as repository code
    assert codes[0].path == chunk.path and codes[0].start_line is not None
    lines = fx.texts[chunk.path].splitlines()
    assert lines[codes[0].start_line - 1].split() == body[0].split()
    assert [c.graph_node_ids for c in written.claims] == [[edge.src, edge.dst], [], []]

    # the verifier accepts exactly what the writer anchored
    result = verify_section(written.content, written.claims, written.evidence, fx.texts, RepoMap(fx.ir))
    checks = {(c.check, c.ok) for c in result.deterministic}
    assert ("excerpt_matches", True) in checks and ("edge_exists", True) in checks
    assert not any(
        not c.ok and c.check in {"excerpt_matches", "edge_exists", "line_in_bounds"}
        for c in result.deterministic
    )
