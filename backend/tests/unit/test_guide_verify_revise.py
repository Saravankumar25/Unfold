"""Deterministic guide verification and bounded revision (phase 3B task 3.7), on real analyzer output.

A tiny repo is analysed (parsed, never executed); chapters are hand-written so each defect is isolated."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from unfold.core.errors import GenerationError
from unfold.domain.enums import ClaimKind, ClaimStatus, Proof
from unfold.domain.models import ClaimRecord, Provenance
from unfold.evaluation.corpus import load_fixture
from unfold.guide.models import (
    Block,
    CodeBlock,
    Finding,
    ParagraphBlock,
    ResearchBrief,
    ResearchSpent,
    SectionContent,
)
from unfold.guide.revise import (
    RevisionItem,
    apply_revision,
    plan_revision,
    qualify_or_remove,
    run_revision_loop,
    unsupported_blocks,
)
from unfold.guide.verification_models import NOT_CONFIRMED, Unsupported, VerificationResult
from unfold.guide.verify import cited_paths, verify_section
from unfold.project_ir.schema import ProjectIR, RelationshipIR
from unfold.understand.repomap import RepoMap

FILES = {
    "pkg/__init__.py": "",
    "pkg/a.py": "from pkg.b import b\n\n\ndef a():\n    return b()\n",
    "pkg/b.py": "def b():\n    return 1\n",
    "pkg/c.py": "def c():\n    return 2\n",
}
EVIDENCE: dict[str, dict[str, object]] = {
    "E1": {"chunk_id": "c1", "path": "pkg/a.py", "start_line": 4, "end_line": 5, "symbol": "a"}
}
GOOD_CODE = CodeBlock(language="python", code="def a():\n    return b()", path="pkg/a.py", start_line=4)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> tuple[ProjectIR, dict[str, str]]:
    root: Path = tmp_path_factory.mktemp("verify")
    for rel, text in FILES.items():
        (root / "repo" / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / "repo" / rel).write_text(text, encoding="utf-8")
    return load_fixture("repo", root=root).ir, dict(FILES)


def _ids(rm: RepoMap, *names: str) -> list[str]:
    out = []
    for n in names:
        ref = rm.symbol(n)
        assert ref is not None, n
        out.append(ref.id)
    return out


def _para(text: str) -> ParagraphBlock:
    return ParagraphBlock(text=text)


def _content(*blocks: Block) -> SectionContent:
    return SectionContent(summary="s", blocks=list(blocks))


def _verify(
    world: tuple[ProjectIR, dict[str, str]],
    content: SectionContent,
    claims: Sequence[ClaimRecord] = (),
    **kw: int,
) -> VerificationResult:
    ir, texts = world
    return verify_section(content, claims, EVIDENCE, texts, RepoMap(ir), **kw)


def _failed(r: VerificationResult) -> set[tuple[str, str | None]]:
    return {(c.check, c.block) for c in r.deterministic if not c.ok}


# ---- the five checks -------------------------------------------------------------------------------------------


def test_clean_section_is_accepted(world: tuple[ProjectIR, dict[str, str]]) -> None:
    rm = RepoMap(world[0])
    a, b = _ids(rm, "a", "b")
    claim = ClaimRecord(
        text="`a` calls `b`", status=ClaimStatus.VERIFIED, evidence_ids=["c1"], graph_node_ids=[a, b]
    )
    content = _content(_para("`a` calls `b` [E1], see pkg/a.py:4-5."), GOOD_CODE)
    r = _verify(world, content, [claim])
    assert r.verdict == "accept" and r.failing_blocks == []
    assert {c.check for c in r.deterministic} >= {
        "path_exists",
        "line_in_bounds",
        "symbol_exists",
        "excerpt_matches",
        "edge_exists",
    }
    assert all(c.ok for c in r.deterministic)
    assert cited_paths(content) == {"pkg/a.py"}


def test_fabricated_path_fails_path_exists_and_asks_for_revision(
    world: tuple[ProjectIR, dict[str, str]],
) -> None:
    content = _content(_para("Fine [E1]."), _para("The loader lives in pkg/loader.py:3-9."))
    r = _verify(world, content)
    assert _failed(r) == {("path_exists", "b1")}
    assert r.failing_blocks == ["b1"] and r.verdict == "revise"
    assert (
        _verify(world, content, round_no=2).verdict == "accept_with_gaps"
    )  # at the cap: gaps, not another round


def test_out_of_range_line_fails_line_in_bounds_only(world: tuple[ProjectIR, dict[str, str]]) -> None:
    r = _verify(world, _content(_para("See pkg/a.py:3-90.")))
    assert _failed(r) == {("line_in_bounds", "b0")}


def test_altered_excerpt_fails_excerpt_matches_but_whitespace_and_elision_pass(
    world: tuple[ProjectIR, dict[str, str]],
) -> None:
    altered = CodeBlock(code="def a():\n    return c()", path="pkg/a.py", start_line=4)
    assert _failed(_verify(world, _content(altered))) == {("excerpt_matches", "b0")}
    loose = CodeBlock(code="def a():\n  return   b()\n", path="pkg/a.py", start_line=4)
    elided = CodeBlock(code="from pkg.b import b\n    return b()", path="pkg/a.py")
    assert _verify(world, _content(loose, elided)).verdict == "accept"


def test_unknown_evidence_label_and_ghost_symbol_fail(world: tuple[ProjectIR, dict[str, str]]) -> None:
    ir, texts = world
    ev = {"E1": {**EVIDENCE["E1"], "symbol": "ghost_function"}}
    r = verify_section(_content(_para("Seen [E1] and [E9].")), [], ev, texts, RepoMap(ir))
    assert _failed(r) == {("symbol_exists", "b0"), ("path_exists", "b0")}


def test_nonexistent_edge_fails_edge_exists_and_is_placed_in_the_block(
    world: tuple[ProjectIR, dict[str, str]],
) -> None:
    a, c = _ids(RepoMap(world[0]), "a", "c")
    claim = ClaimRecord(text="a calls c", graph_node_ids=[a, c])
    r = _verify(world, _content(_para("Intro."), _para("`a` calls `c` directly.")), [claim])
    assert _failed(r) == {("edge_exists", "b1")}
    assert next(c for c in r.deterministic if c.check == "edge_exists").detail == "no such edge"


def test_inferred_only_edge_is_not_accepted_as_fact(world: tuple[ProjectIR, dict[str, str]]) -> None:
    ir, texts = world
    rm = RepoMap(ir)
    a, c = _ids(rm, "a", "c")
    inferred = RelationshipIR(
        source_id=a,
        target_id=c,
        type="CALLS",
        provenance=Provenance(source="inferred", analyzer="test", confidence=0.2),
        proof=Proof.INFERRED,
    )
    ir2 = ir.model_copy(update={"relationships": [*ir.relationships, inferred]})
    claim = ClaimRecord(text="a calls c", graph_node_ids=[a, c])
    r = verify_section(_content(_para("`a` calls `c`.")), [claim], EVIDENCE, texts, RepoMap(ir2))
    edge = next(x for x in r.deterministic if x.check == "edge_exists")
    assert not edge.ok and "inferred" in (edge.detail or "")
    # the heuristic a -> b edge from the analyzer is fine
    b = _ids(rm, "b")[0]
    ok = verify_section(
        _content(_para("`a` calls `b`.")), [ClaimRecord(text="x", graph_node_ids=[a, b])], EVIDENCE, texts, rm
    )
    assert ok.verdict == "accept"


def test_ungrounded_fact_claims_fail_and_missing_file_text_is_a_wiring_error(
    world: tuple[ProjectIR, dict[str, str]],
) -> None:
    bad = ClaimRecord(text="Retries use exponential backoff", status=ClaimStatus.UNSUPPORTED)
    r = _verify(
        world, _content(_para("Retries use exponential backoff in the client."), _para("Other.")), [bad]
    )
    assert _failed(r) == {("claim_supported", "b0")}
    suggestion = ClaimRecord(text="Add caching", kind=ClaimKind.IMPROVEMENT, status=ClaimStatus.PROPOSED)
    assert _verify(world, _content(_para("Add caching.")), [suggestion]).verdict == "accept"
    with pytest.raises(GenerationError):
        verify_section(_content(GOOD_CODE), [], EVIDENCE, {}, RepoMap(world[0]))


# ---- revision ---------------------------------------------------------------------------------------------------


def test_apply_revision_replaces_only_named_blocks() -> None:
    blocks = [_para("one"), _para("two"), _para("three")]
    content = _content(*blocks)
    out = apply_revision(content, {"b1": _para("TWO")})
    assert out.blocks[0] is blocks[0] and out.blocks[2] is blocks[2]
    assert [b.text for b in out.blocks] == ["one", "TWO", "three"]  # type: ignore[union-attr]
    with pytest.raises(GenerationError):
        apply_revision(content, {"b7": _para("x")})


def test_qualify_or_remove() -> None:
    content = _content(_para("cited, partly wrong"), _para("no evidence"), GOOD_CODE, _para("fine"))
    out = qualify_or_remove(
        content,
        [
            Unsupported(block="b0", has_evidence=True),
            Unsupported(block="b1", has_evidence=False),
            Unsupported(block="b2", has_evidence=True),
        ],
    )
    assert [b.text for b in out.blocks] == [f"{NOT_CONFIRMED} cited, partly wrong", "fine"]  # type: ignore[union-attr]
    again = qualify_or_remove(out, [Unsupported(block="b0", has_evidence=True)])
    assert again.blocks[0].text.count(NOT_CONFIRMED) == 1  # type: ignore[union-attr]


def test_plan_revision_picks_the_findings_that_bear_on_the_block(
    world: tuple[ProjectIR, dict[str, str]],
) -> None:
    brief = ResearchBrief(
        chapter_key="k",
        questions=["q"],
        findings=[
            Finding(
                question_id=0,
                statement="The loader reads pkg/loader.py at start",
                evidence=["ch-loader"],
                proof=Proof.STATIC,
            ),
            Finding(
                question_id=0, statement="Unrelated retry policy", evidence=["ch-retry"], proof=Proof.STATIC
            ),
        ],
        flows=[],
        tests=[],
        open_questions=[],
        coverage=1.0,
        spent=ResearchSpent(),
    )
    content = _content(_para("Fine [E1]."), _para("The loader lives in pkg/loader.py:3-9."))
    r = _verify(world, content)
    (item,) = plan_revision(r, content, brief)
    assert item.block == "b1" and item.evidence_ids == ["ch-loader"]
    assert [f.check for f in item.failures] == ["path_exists"]
    assert "loader" in item.query


def _runner(world: tuple[ProjectIR, dict[str, str]], fixes: Mapping[str, Block] | None, calls: list[int]):  # type: ignore[no-untyped-def]
    async def verify(content: SectionContent, rnd: int) -> VerificationResult:
        return _verify(world, content, round_no=rnd)

    async def rewrite(content: SectionContent, items: Sequence[RevisionItem]) -> Mapping[str, Block]:
        calls.append(len(items))
        out: dict[str, Block] = {}
        for it in items[:1] if fixes is None else items:  # `None`: fix one block per round
            out[it.block] = _para("Now correct [E1].")
        out["b0"] = _para("a rewrite of a block that was fine")  # must be ignored
        return out

    return verify, rewrite


async def test_loop_accepts_when_a_round_fixes_everything(world: tuple[ProjectIR, dict[str, str]]) -> None:
    calls: list[int] = []
    verify, rewrite = _runner(world, {}, calls)
    content = _content(_para("Fine [E1]."), _para("See pkg/nope.py:1-2."), _para("See pkg/none.py:1-2."))
    out = await run_revision_loop(verify, rewrite, content)
    assert out.stopped_by == "accept" and out.rounds == 1 and out.verification.verdict == "accept"
    assert out.content.blocks[0] is content.blocks[0]  # b0 was not failing: the rewrite for it was ignored
    assert [b.text for b in out.content.blocks[1:]] == ["Now correct [E1]."] * 2  # type: ignore[union-attr]
    assert calls == [2] and out.gaps == ()


async def test_loop_stops_at_the_cap_and_qualifies_what_is_left(
    world: tuple[ProjectIR, dict[str, str]],
) -> None:
    calls: list[int] = []
    verify, rewrite = _runner(world, None, calls)  # fixes one failing block per round: 3 failures, 2 rounds
    content = _content(
        _para("Fine [E1]."),
        _para("Real [E1] but also pkg/one.py:1-2."),
        _para("Cites pkg/two.py:1-2 only."),
        _para("Cites pkg/three.py:1-2 only."),
    )
    out = await run_revision_loop(verify, rewrite, content, max_rounds=2)
    assert len(calls) == 2 and out.rounds == 2 and out.stopped_by == "cap"
    assert out.verification.verdict == "accept_with_gaps"
    assert out.gaps == ("b3",)  # b1 and b2 were rewritten; b3 cited nothing real and is removed
    texts = [b.text for b in out.content.blocks]  # type: ignore[union-attr]
    assert texts == ["Fine [E1].", "Now correct [E1].", "Now correct [E1]."]


async def test_loop_stops_when_a_round_does_not_improve(world: tuple[ProjectIR, dict[str, str]]) -> None:
    calls: list[int] = []

    async def verify(content: SectionContent, rnd: int) -> VerificationResult:
        return _verify(world, content, round_no=rnd)

    async def rewrite(content: SectionContent, items: Sequence[RevisionItem]) -> Mapping[str, Block]:
        calls.append(1)
        return {i.block: _para("Still wrong: pkg/gone.py:1-2.") for i in items}

    content = _content(_para("Real [E1], and pkg/gone.py:1-2 too."))
    out = await run_revision_loop(verify, rewrite, content, max_rounds=2)
    assert calls == [1] and out.stopped_by == "no_improvement" and out.rounds == 1
    assert out.verification.verdict == "accept_with_gaps" and out.gaps == ("b0",)
    assert out.content.blocks[0].text == f"{NOT_CONFIRMED} Real [E1], and pkg/gone.py:1-2 too."  # type: ignore[union-attr]


async def test_loop_never_calls_the_model_for_a_clean_chapter(
    world: tuple[ProjectIR, dict[str, str]],
) -> None:
    calls: list[int] = []
    verify, rewrite = _runner(world, None, calls)
    out = await run_revision_loop(verify, rewrite, _content(_para("Fine [E1].")))
    assert calls == [] and out.rounds == 0 and out.stopped_by == "accept"
    assert unsupported_blocks(out.verification) == []


# ---- unsupported statements never stay standing as fact ------------------------------------------------------------


async def _no_fix(content: SectionContent, items: Sequence[RevisionItem]) -> Mapping[str, Block]:
    return {}


def _claims_verifier(world: tuple[ProjectIR, dict[str, str]], claims: list[ClaimRecord]):  # type: ignore[no-untyped-def]
    async def verify(content: SectionContent, rnd: int) -> VerificationResult:
        return _verify(world, content, claims, round_no=rnd)

    return verify


async def test_an_unsupported_uncited_sentence_is_removed_and_its_claim_goes_with_it(
    world: tuple[ProjectIR, dict[str, str]],
) -> None:
    good = ClaimRecord(text="`a` calls `b`", status=ClaimStatus.VERIFIED, evidence_ids=["c1"])
    bad = ClaimRecord(text="Retries use exponential backoff", status=ClaimStatus.UNSUPPORTED)
    content = _content(
        _para("`a` calls `b` [E1]. Retries use exponential backoff in the client. `b` returns one [E1].")
    )
    out = await run_revision_loop(_claims_verifier(world, [good, bad]), _no_fix, content, max_rounds=2)

    assert out.verification.verdict == "accept_with_gaps" and out.stopped_by == "no_improvement"
    assert [b.text for b in out.content.blocks] == ["`a` calls `b` [E1]. `b` returns one [E1]."]  # type: ignore[union-attr]
    assert out.dispositions == {bad.text: "removed"}
    # what is left verifies clean once the removed statement's claim is dropped with it
    after = _verify(world, out.content, [good])
    assert after.verdict == "accept" and not [c for c in after.deterministic if not c.ok]


async def test_a_cited_but_unsupported_sentence_is_marked_in_place_not_dropped(
    world: tuple[ProjectIR, dict[str, str]],
) -> None:
    bad = ClaimRecord(text="`a` retries failed calls", status=ClaimStatus.INFERRED, evidence_ids=["c1"])
    content = _content(_para("Intro to the module. `a` retries failed calls [E1]. Closing remark."))
    out = await run_revision_loop(_claims_verifier(world, [bad]), _no_fix, content, max_rounds=1)

    assert [b.text for b in out.content.blocks] == [  # type: ignore[union-attr]
        "Intro to the module.",
        f"{NOT_CONFIRMED} `a` retries failed calls [E1].",
        "Closing remark.",
    ]
    assert out.dispositions == {bad.text: "qualified"}
    # the qualified paragraph is what the frontend marks: it starts with the exact prefix
    assert out.content.blocks[1].text.startswith(NOT_CONFIRMED)  # type: ignore[union-attr]


async def test_bullets_are_fixed_item_by_item(world: tuple[ProjectIR, dict[str, str]]) -> None:
    from unfold.guide.models import BulletsBlock

    bad = ClaimRecord(text="Uses a global lock", status=ClaimStatus.UNSUPPORTED)
    cited = ClaimRecord(text="`a` returns what `b` returns", status=ClaimStatus.INFERRED, evidence_ids=["c1"])
    content = _content(
        BulletsBlock(
            items=["`a` is the entry [E1].", "Uses a global lock", "`a` returns what `b` returns [E1]."]
        )
    )
    out = await run_revision_loop(_claims_verifier(world, [bad, cited]), _no_fix, content, max_rounds=1)
    (block,) = out.content.blocks
    assert block.items == [  # type: ignore[union-attr]
        "`a` is the entry [E1].",
        f"{NOT_CONFIRMED} `a` returns what `b` returns [E1].",
    ]
    assert out.dispositions == {bad.text: "removed", cited.text: "qualified"}


async def test_a_failure_that_is_not_a_claim_falls_back_to_the_whole_block(
    world: tuple[ProjectIR, dict[str, str]],
) -> None:
    bad = ClaimRecord(text="The loader reads pkg/loader.py", status=ClaimStatus.UNSUPPORTED)
    content = _content(_para("The loader reads pkg/loader.py at start. See pkg/nope.py:1-2."))
    out = await run_revision_loop(_claims_verifier(world, [bad]), _no_fix, content, max_rounds=1)
    assert out.content.blocks == []  # no real citation at all: removed whole
    assert out.dispositions == {bad.text: "removed"}
