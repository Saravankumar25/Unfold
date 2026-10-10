"""Guide rubric scorers (task 3.9): tiny synthetic guides must separate good, hollow, fabricated and duplicated."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from unfold.domain.enums import ClaimKind, ClaimStatus
from unfold.domain.models import ClaimRecord
from unfold.evaluation import rubric
from unfold.evaluation.rubric import IRSummary, Named
from unfold.guide.models import (
    BulletsBlock,
    CalloutBlock,
    CodeBlock,
    GuideSection,
    ParagraphBlock,
    SectionContent,
)

IR = IRSummary(
    project="demo",
    files={"app/main.py": 30, "app/services/retriever.py": 60, "app/api/routes/query.py": 25},
    symbols=["reciprocal_rank_fusion", "answer_query", "HybridRetriever", "RRF_K", "main"],
    components=[
        Named(name="app/services", kind="module", aliases=["services"]),
        Named(name="redis", kind="datastore"),
    ],
    entry_points=[Named(name="POST /query", kind="endpoint", aliases=["/query", "answer_query"])],
)
RRF = "def reciprocal_rank_fusion(lists):\n    return fuse(lists, RRF_K)"
CHUNKS: dict[str, dict[str, Any]] = {
    "c1": {
        "path": "app/services/retriever.py",
        "start_line": 10,
        "end_line": 20,
        "text": "retriever.py :: x\n" + RRF,
    },
}
EVIDENCE = {"E1": {"chunk_id": "c1", "path": "app/services/retriever.py", "start_line": 10, "end_line": 20}}


def claim(
    text: str, status: ClaimStatus, *, cited: bool = True, kind: ClaimKind = ClaimKind.PROJECT_FACT
) -> ClaimRecord:
    return ClaimRecord(text=text, kind=kind, status=status, evidence_ids=["c1"] if cited else [])


def section(
    key: str, blocks: list[Any], claims: list[ClaimRecord], evidence: dict[str, Any] | None = None
) -> GuideSection:
    return GuideSection(
        key=key,
        title=key.title(),
        position=0,
        status="ready",
        content=SectionContent(summary=f"About {key}.", blocks=blocks),
        claims=claims,
        evidence=evidence if evidence is not None else {},
    )


def good() -> list[GuideSection]:
    return [
        section(
            "retrieval",
            [
                ParagraphBlock(
                    text="First `answer_query` in `app/api/routes/query.py` handles POST /query [E1]. "
                    "Then `HybridRetriever` calls `reciprocal_rank_fusion` with `RRF_K` [E1]."
                ),
                BulletsBlock(
                    items=[
                        "`HybridRetriever` fetches the lexical hits.",
                        "`reciprocal_rank_fusion` merges them.",
                    ],
                    ordered=True,
                ),
                CodeBlock(language="python", code=RRF, path="app/services/retriever.py", start_line=10),
                CalloutBlock(
                    tone="warning", text="If redis is down the cache fails and `answer_query` skips it."
                ),
                ParagraphBlock(
                    text="The result is stored in redis. The ranking of ties is not covered by the evidence."
                ),
            ],
            [claim("`reciprocal_rank_fusion` uses `RRF_K`", ClaimStatus.VERIFIED)],
            EVIDENCE,
        ),
        section(
            "services",
            [
                ParagraphBlock(
                    text="The app/services module holds `HybridRetriever`. Its `main` entry wires the queue "
                    "and database state before answering [E1]."
                ),
            ],
            [claim("`HybridRetriever` is in `app/services/retriever.py`", ClaimStatus.VERIFIED)],
            EVIDENCE,
        ),
    ]


def hollow() -> list[GuideSection]:
    text = (
        "This module provides important functionality for the system. It is designed for scalability and "
        "maintainability. Developers should follow best practices when working with it."
    )
    return [
        section(
            k,
            [ParagraphBlock(text=text.replace("module", f"{k} module"))],
            [
                claim("The system is scalable", ClaimStatus.UNSUPPORTED, cited=False),
                claim("It follows best practices", ClaimStatus.INFERRED),
            ],
        )
        for k in ("overview", "design")
    ]


def fabricated() -> list[GuideSection]:
    return [
        section(
            "ghost",
            [
                ParagraphBlock(
                    text="`answer_query` lives in app/ghost.py:10-20 and is shown in [E1] and [E9]."
                ),
                CodeBlock(code="def not_real(): pass", path="app/ghost.py", start_line=5),
                CodeBlock(code=RRF, path="app/services/retriever.py", start_line=500),
            ],
            [claim("`ghost` exists", ClaimStatus.VERIFIED)],
            {"E1": {"chunk_id": "x", "path": "app/ghost.py", "start_line": 1, "end_line": 900}},
        )
    ]


def duplicated() -> list[GuideSection]:
    a = good()[0]
    return [a, a.model_copy(update={"key": "retrieval-again"}), good()[1]]


def test_good_guide_scores_high_everywhere() -> None:
    r = rubric.score_guide(good(), IR, CHUNKS)
    s = r["scores"]
    assert s["coverage"] == 4.0
    assert s["groundedness"] == 4.0
    assert s["citation_accuracy"] == 4.0
    assert s["redundancy"] == 4.0
    assert s["uncertainty_honesty"] == 4.0
    assert s["specificity"] >= 3.0
    assert s["depth_proxy"] >= 2.5
    assert r["raw"]["citation_accuracy"]["excerpt_ok"] == 1
    assert r["length"]["chapters"] == 2 and r["length"]["words"] > 0


def test_hollow_guide_is_separated_from_good() -> None:
    g, h = rubric.score_guide(good(), IR, CHUNKS)["scores"], rubric.score_guide(hollow(), IR, {})["scores"]
    for dim in ("coverage", "groundedness", "specificity", "depth_proxy", "uncertainty_honesty"):
        assert h[dim] < g[dim] - 1.5, dim
    assert h["specificity"] == 0.0 and h["coverage"] == 0.0 and h["uncertainty_honesty"] == 0.0


def test_fabricated_paths_and_lines_fail_citation_accuracy() -> None:
    r = rubric.citation_accuracy(fabricated(), IR, CHUNKS)
    assert r.score == 0.0
    raw = r.raw
    assert raw["citations"] == 5  # E1, E9, app/ghost.py:10-20, two code blocks
    assert raw["excerpts"] == 2 and raw["excerpt_ok"] == 1  # real code, but line 500 is past the 60-line file
    assert raw["resolve_rate"] == 0.0


def test_duplicated_chapters_raise_redundancy() -> None:
    clean = rubric.redundancy(good()).score
    dup = rubric.redundancy(duplicated())
    assert clean == 4.0
    assert dup.score <= 2.0
    assert dup.raw["max_repeated_share"] == 1.0


def test_gaps_stated_offset_unverified_facts() -> None:
    sections = good()
    sections[0].claims.append(claim("`RRF_K` is 60", ClaimStatus.UNSUPPORTED, cited=False))
    r = rubric.uncertainty_honesty(sections)
    assert r.raw == {
        "unverified_facts_left": 1,
        "gaps_stated": 1,
        "statements_qualified_or_removed": 0,
        "claims_not_stated": 0,
    }
    assert r.score == 2.0


def test_a_statement_qualified_by_the_verifier_is_a_gap_not_a_fact_left_standing() -> None:
    """The reviser's own marker counts as a stated gap, so honesty is not penalised for using it; a claim it
    converted to an inference is no longer an unverified project fact."""
    sections = good()
    sections[0].content.blocks.append(  # type: ignore[union-attr]
        ParagraphBlock(text="Not confirmed in the code: it retries on failure [E1].")
    )
    sections[0].claims.append(
        claim("it retries", ClaimStatus.INFERRED, kind=ClaimKind.INFERENCE)  # converted by the reviser
    )
    sections[0].quality = {"gaps": 2}
    r = rubric.uncertainty_honesty(sections)
    assert r.raw["unverified_facts_left"] == 0 and r.raw["gaps_stated"] == 2  # one from `good()`
    assert r.raw["statements_qualified_or_removed"] == 2
    assert r.score == 4.0


def test_a_guide_that_says_what_it_does_not_show_is_not_marked_down_for_the_wording() -> None:
    sections = good()
    unsupported = claim("`RRF_K` is 60", ClaimStatus.UNSUPPORTED, cited=False)
    sections[0].claims.append(unsupported)
    sections[0].content.blocks.append(  # type: ignore[union-attr]
        ParagraphBlock(
            text="The code does not show how ties are broken, and the retry policy is not established."
        )
    )
    assert rubric.uncertainty_honesty(sections).raw["gaps_stated"] == 3


def test_empty_guide_scores_zero_without_dividing() -> None:
    r = rubric.score_guide([], IR, {})
    assert set(r["scores"].values()) == {0.0} or r["scores"]["uncertainty_honesty"] == 4.0
    assert r["scores"]["coverage"] == 0.0 and r["length"]["chapters"] == 0


def test_cli_scores_a_stored_guide_directory(tmp_path: Path) -> None:
    d = tmp_path / "demo"
    d.mkdir()
    (d / "guide.json").write_text(json.dumps({"sections": [s.model_dump(mode="json") for s in good()]}))
    (d / "ir.json").write_text(IR.model_dump_json())
    (d / "chunks.json").write_text(json.dumps(CHUNKS))
    out = tmp_path / "scores.json"
    assert rubric.main([str(d), "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["demo"]["scores"]["groundedness"] == 4.0
    sheet = json.loads((d / "review-sheet.json").read_text())
    assert [c["key"] for c in sheet["chapters"]] == ["retrieval", "services"]
    assert sheet["chapters"][0]["readability"] is None  # human field, never filled by code


@pytest.mark.parametrize("fn", [rubric.groundedness, rubric.redundancy, rubric.uncertainty_honesty])
def test_scorers_accept_failed_chapters(fn: Any) -> None:
    failed = GuideSection(key="x", title="X", position=0, status="failed", content=None)
    assert 0.0 <= fn([failed]).score <= 4.0


def test_elided_excerpt_passes_but_invented_line_fails() -> None:
    path = "app/services/retriever.py"
    elided = CodeBlock(code=RRF.splitlines()[1], path=path)  # first line dropped
    invented = CodeBlock(code=RRF.splitlines()[0] + "\n    return 42", path=path)
    ok = rubric.citation_accuracy([section("a", [elided], [])], IR, CHUNKS)
    bad = rubric.citation_accuracy([section("a", [invented], [])], IR, CHUNKS)
    assert ok.raw["excerpt_ok"] == 1 and bad.raw["excerpt_ok"] == 0


def test_route_alias_matches_inside_a_versioned_path() -> None:
    text = "The POST /api/v1/query endpoint is served from `app/api/routes/`."
    sec = section("a", [ParagraphBlock(text=text)], [])
    assert rubric.coverage([sec], IR).raw["entry_points_named"] == 1
    assert rubric.specificity([sec], IR).raw["naming_a_symbol"] == 1


def test_stating_a_gap_does_not_cost_specificity_or_redundancy() -> None:
    """A sentence about what the evidence does not show is neither generic advice nor a repeated explanation; the
    same gap sentence in every chapter must not read as redundancy, nor lower the share of specific sentences."""
    gap = "The evidence does not show how the retry policy is configured."
    base = good()
    with_gaps = good()
    for sec in with_gaps:
        sec.content.blocks.append(ParagraphBlock(text=gap))  # type: ignore[union-attr]
    for dim, fn in (
        ("specificity", lambda s: rubric.specificity(s, IR)),
        ("redundancy", rubric.redundancy),
    ):
        assert fn(with_gaps).score == fn(base).score, dim
    assert rubric.uncertainty_honesty(with_gaps).raw["gaps_stated"] == (
        rubric.uncertainty_honesty(base).raw["gaps_stated"] + len(with_gaps)
    )


def test_repeating_an_ordinary_sentence_across_chapters_still_counts_as_redundancy() -> None:
    sentence = "The HybridRetriever merges lexical and dense hits before the answer is built for the reader."
    sections = good()
    for sec in sections:
        sec.content.blocks.append(ParagraphBlock(text=sentence))  # type: ignore[union-attr]
    assert rubric.redundancy(sections).score < rubric.redundancy(good()).score
