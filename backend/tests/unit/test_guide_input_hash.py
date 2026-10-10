"""Per-chapter input hash (investigation engine section 3.8): what invalidates a chapter and what does not."""

from __future__ import annotations

import pytest

from unfold.domain.enums import Proof
from unfold.guide import input_hash as ih
from unfold.guide.input_hash import chapter_input_hash, evidence_ids
from unfold.guide.models import ChapterPlan, Finding, ResearchBrief, ResearchSpent

SPEC = ChapterPlan(
    key="retrieval",
    title="Retrieval",
    kind="core",
    purpose="How search works",
    target_minutes=5,
    questions=["How is a query planned?", "How are hits fused?"],
    targets=["file:a", "file:b"],
    depends_on=["overview"],
)
BRIEF = ResearchBrief(
    chapter_key="retrieval",
    questions=["q"],
    findings=[
        Finding(question_id=0, statement="s1", evidence=["c2", "c1"], proof=Proof.STATIC),
        Finding(question_id=0, statement="s2", evidence=["c1", "c3"], proof=Proof.HEURISTIC),
    ],
    flows=[],
    tests=[],
    open_questions=[],
    coverage=1.0,
    spent=ResearchSpent(),
)
HASHES = {"c1": "h1", "c2": "h2", "c3": "h3", "other": "zz"}


def _hash(
    spec: ChapterPlan = SPEC, brief: ResearchBrief = BRIEF, hashes: dict[str, str] = HASHES, **kw: object
) -> str:
    args: dict[str, object] = {"technical_level": "intermediate", "instruction": None, "revision_rounds": 2}
    args.update(kw)
    return chapter_input_hash(spec, brief, hashes, **args)  # type: ignore[arg-type]


def test_hash_is_stable_and_names_each_evidence_chunk_once() -> None:
    assert _hash() == _hash()
    assert evidence_ids(BRIEF) == ["c1", "c2", "c3"]


def test_changed_inputs_change_the_hash() -> None:
    base = _hash()
    assert _hash(spec=SPEC.model_copy(update={"objective": "something else"})) != base
    assert _hash(spec=SPEC.model_copy(update={"questions": ["only one"]})) != base
    assert _hash(hashes={**HASHES, "c2": "changed"}) != base  # evidence content changed
    assert _hash(technical_level="beginner") != base
    assert _hash(instruction="short please") != base
    assert _hash(revision_rounds=1) != base


def test_unrelated_chunks_and_dependencies_do_not_invalidate_a_chapter() -> None:
    base = _hash()
    assert _hash(hashes={**HASHES, "other": "different"}) == base  # not evidence of this chapter
    assert _hash(spec=SPEC.model_copy(update={"depends_on": []})) == base  # ordering only


@pytest.mark.parametrize(
    "name", ["PLANNER_VERSION", "WRITER_PROMPT_VERSION", "CRITIQUE_PROMPT_VERSION", "REWRITE_PROMPT_VERSION"]
)
def test_a_prompt_version_bump_invalidates(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _hash()
    monkeypatch.setattr(ih, name, "bumped")
    assert _hash() != base
