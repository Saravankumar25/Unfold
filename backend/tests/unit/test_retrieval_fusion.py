from __future__ import annotations

import math

import pytest

from unfold.retrieval.fusion import max_fused_score, merge_variants, rrf_fuse


def test_rrf_scores_match_formula() -> None:
    fused = rrf_fuse({"lexical": ["a", "b"], "dense": ["b", "c"]}, {"lexical": 1.0, "dense": 2.0}, k=60)
    scores = {c.chunk_id: c.score for c in fused}
    assert math.isclose(scores["a"], 1.0 / 61)
    assert math.isclose(scores["b"], 1.0 / 62 + 2.0 / 61)
    assert math.isclose(scores["c"], 2.0 / 62)
    assert [c.chunk_id for c in fused] == ["b", "c", "a"]
    b = next(c for c in fused if c.chunk_id == "b")
    assert b.sources == {"lexical": 2, "dense": 1}
    assert b.best_rank == 1


def test_weights_change_the_winner() -> None:
    rankings = {"lexical": ["x", "y"], "dense": ["y", "x"]}
    assert rrf_fuse(rankings, {"lexical": 2.0, "dense": 1.0})[0].chunk_id == "x"
    assert rrf_fuse(rankings, {"lexical": 1.0, "dense": 2.0})[0].chunk_id == "y"


def test_ties_break_by_best_rank_then_chunk_id() -> None:
    fused = rrf_fuse({"a": ["p", "q"], "b": ["q", "p"]}, None, k=60)
    assert fused[0].score == fused[1].score
    assert [c.chunk_id for c in fused] == ["p", "q"]  # same best rank (1) → chunk id order
    # z: 1/(0+1) = 1.0 with best rank 1; w: 2/(0+2) = 1.0 with best rank 2 → z first although "w" < "z".
    fused2 = rrf_fuse({"s1": ["z"], "s2": ["pad", "w"]}, {"s1": 1.0, "s2": 2.0}, k=0)
    by_id = {c.chunk_id: c for c in fused2}
    assert by_id["z"].score == by_id["w"].score
    assert [c.chunk_id for c in fused2] == ["pad", "z", "w"]


def test_duplicates_within_a_ranking_count_once() -> None:
    fused = rrf_fuse({"lexical": ["a", "a", "b"]}, None, k=60)
    by_id = {c.chunk_id: c for c in fused}
    assert math.isclose(by_id["a"].score, 1 / 61)
    assert by_id["b"].sources == {"lexical": 2}


def test_zero_or_missing_weight_ignores_the_ranking() -> None:
    fused = rrf_fuse({"lexical": ["a"], "graph": ["g"]}, {"lexical": 1.0, "graph": 0.0})
    assert [c.chunk_id for c in fused] == ["a"]
    fused = rrf_fuse({"lexical": ["a"], "other": ["o"]}, {"lexical": 1.0})
    assert [c.chunk_id for c in fused] == ["a"]


def test_fusion_is_deterministic_and_order_independent() -> None:
    r1 = {"lexical": ["a", "b", "c"], "dense": ["c", "d"], "graph": ["d", "a"]}
    r2 = {"graph": ["d", "a"], "dense": ["c", "d"], "lexical": ["a", "b", "c"]}
    w = {"lexical": 1.3, "dense": 0.8, "graph": 1.0}
    assert rrf_fuse(r1, w) == rrf_fuse(r2, w)


def test_empty_and_invalid() -> None:
    assert rrf_fuse({}, None) == []
    assert rrf_fuse({"lexical": []}, None) == []
    with pytest.raises(ValueError):
        rrf_fuse({"a": ["x"]}, None, k=-1)


def test_merge_variants_prefers_primary() -> None:
    assert merge_variants([]) == []
    assert merge_variants([["a", "a", "b"]]) == ["a", "b"]
    merged = merge_variants([["a", "b"], ["b", "c"]], variant_weight=0.6)
    assert merged[0] == "b"  # in both lists
    assert merged.index("a") < merged.index("c")  # primary rank 1 beats variant rank 2


def test_max_fused_score() -> None:
    assert math.isclose(max_fused_score({"a": 1.0, "b": 2.0, "c": 0.0}, 60), 3.0 / 61)
