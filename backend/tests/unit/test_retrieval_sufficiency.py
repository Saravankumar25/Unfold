from __future__ import annotations

from unfold.core.ids import chunk_id, content_hash
from unfold.domain.enums import ContentType, RetrievalMode
from unfold.indexing.models import Chunk
from unfold.retrieval.config import RETRIEVAL_CONFIG_VERSION, RetrievalConfig
from unfold.retrieval.models import RetrievalPlan, RetrievalResult, RetrievalTrace, RetrievedItem
from unfold.retrieval.planner import plan_query
from unfold.retrieval.sufficiency import assess, follow_up_plan, query_keys

PID = "22222222-2222-2222-2222-222222222222"


def _chunk(path: str, text: str, *, start: int = 1, ctype: ContentType = ContentType.SOURCE) -> Chunk:
    body = f"{path} :: x\n{text}"
    return Chunk(
        id=chunk_id(PID, path, start, start + 9, "function"),
        project_id=PID,
        path=path,
        start_line=start,
        end_line=start + 9,
        kind="function",
        content_type=ctype,
        text=body,
        content_hash=content_hash(body),
    )


def _item(chunk: Chunk, *, rerank: float | None = 3.0, fused: float = 0.05, expansion: bool = False) -> RetrievedItem:
    return RetrievedItem(
        chunk=chunk,
        score=rerank if rerank is not None else fused,
        fused_score=fused,
        rerank_score=rerank,
        sources={"lexical": 1},
        via_expansion=expansion,
    )


def _result(plan: RetrievalPlan, items: list[RetrievedItem]) -> RetrievalResult:
    return RetrievalResult(plan=plan, items=items, trace=RetrievalTrace(config_version=RETRIEVAL_CONFIG_VERSION))


def test_sufficient_when_identifier_terms_and_types_are_covered() -> None:
    plan = plan_query(PID, "Where is verifyAccessToken defined?", RetrievalMode.CHAT)
    c = _chunk("src/auth.py", "def verifyAccessToken(token):\n    return jwt.decode(token)")
    ok, details = assess(plan, _result(plan, [_item(c)]))
    assert ok, details
    assert details["coverage"] == 1.0
    assert details["missing_identifiers"] == []
    assert details["reasons"] == []


def test_no_results_is_insufficient() -> None:
    plan = plan_query(PID, "Where is verifyAccessToken defined?")
    ok, details = assess(plan, _result(plan, []))
    assert not ok
    assert details["reasons"] == ["no_results"]
    assert details["missing_identifiers"] == ["verifyAccessToken"]


def test_missing_identifier_and_low_coverage() -> None:
    plan = plan_query(PID, "Where are verifyAccessToken and refresh rotation implemented?")
    c = _chunk("src/other.py", "def unrelated():\n    pass")
    ok, details = assess(plan, _result(plan, [_item(c)]))
    assert not ok
    assert "missing_identifiers" in details["reasons"]
    assert "low_term_coverage" in details["reasons"]
    assert "rotation" in details["missing_terms"]


def test_expansion_items_do_not_count_towards_coverage() -> None:
    plan = plan_query(PID, "Where is verifyAccessToken defined?")
    main = _chunk("src/other.py", "def unrelated():\n    pass")
    extra = _chunk("src/auth.py", "def verifyAccessToken(t): ...", start=20)
    ok, details = assess(plan, _result(plan, [_item(main), _item(extra, expansion=True)]))
    assert not ok
    assert details["missing_identifiers"] == ["verifyAccessToken"]


def test_low_rerank_score_is_insufficient() -> None:
    plan = plan_query(PID, "Where is verifyAccessToken defined?")
    c = _chunk("src/auth.py", "def verifyAccessToken(token): ...")
    ok, details = assess(plan, _result(plan, [_item(c, rerank=-7.5)]))
    assert not ok
    assert details["reasons"] == ["low_top_score"]
    assert details["reranked"] is True


def test_unreranked_uses_fused_ratio() -> None:
    plan = plan_query(PID, "Where is verifyAccessToken defined?")
    c = _chunk("src/auth.py", "def verifyAccessToken(token): ...")
    weak = _result(plan, [_item(c, rerank=None, fused=0.001)])
    ok, details = assess(plan, weak)
    assert not ok and "low_top_score" in details["reasons"]
    strong = _result(plan, [_item(c, rerank=None, fused=2.0 / 61)])
    ok, details = assess(plan, strong)
    assert ok, details
    assert details["reranked"] is False


def test_missing_primary_evidence_type() -> None:
    plan = plan_query(PID, "Give me an overview of this project")  # desired: docs, source, config
    assert plan.desired_evidence_types[0] == "docs"
    c = _chunk("src/main.py", "overview project main entry")
    ok, details = assess(plan, _result(plan, [_item(c)]))
    assert not ok
    assert "missing_primary_evidence_type" in details["reasons"]
    assert "docs" in details["missing_evidence_types"]


def test_missing_named_path() -> None:
    plan = plan_query(PID, "What does src/auth/service.py do?")
    c = _chunk("src/auth/other.py", "service auth")
    ok, details = assess(plan, _result(plan, [_item(c)]))
    assert not ok
    assert details["missing_paths"] == ["src/auth/service.py"]
    ok2, _ = assess(plan, _result(plan, [_item(_chunk("src/auth/service.py", "service auth"))]))
    assert ok2


def test_follow_up_targets_gaps_and_is_bounded() -> None:
    plan = plan_query(PID, "Where are verifyAccessToken and refresh rotation implemented?")
    c = _chunk("src/other.py", "def unrelated():\n    pass")
    _, details = assess(plan, _result(plan, [_item(c)]))
    issued = query_keys(plan)
    fu = follow_up_plan(plan, details, round_no=1, issued_queries=issued)
    assert fu is not None
    assert fu.lexical_queries and "verifyAccessToken" in fu.lexical_queries[0]
    assert "rotation" in fu.lexical_queries[0]
    assert fu.graph_seed_entities == ["verifyAccessToken"]
    assert fu.structural_filters["followup_round"] == 1
    assert fu.target_symbols == []
    assert fu.breadth == "medium"  # narrow → medium
    # never repeats itself
    issued |= query_keys(fu)
    assert follow_up_plan(plan, details, round_no=2, issued_queries=issued) is None
    # bounded by config
    assert follow_up_plan(plan, details, round_no=3, config=RetrievalConfig(max_followup_rounds=2)) is None


def test_follow_up_for_missing_content_type_filters_dense() -> None:
    plan = plan_query(PID, "Give me an overview of this project")
    _, details = assess(plan, _result(plan, [_item(_chunk("src/main.py", "main"))]))
    details["missing_terms"] = []
    fu = follow_up_plan(plan, details, round_no=1, issued_queries=query_keys(plan))
    assert fu is not None
    assert fu.structural_filters["content_types"][0] == "docs"
    assert fu.semantic_queries == [plan.query]


def test_follow_up_none_when_nothing_is_missing() -> None:
    plan = plan_query(PID, "Where is verifyAccessToken defined?")
    c = _chunk("src/auth.py", "def verifyAccessToken(token): ...")
    ok, details = assess(plan, _result(plan, [_item(c)]))
    assert ok
    assert follow_up_plan(plan, details, round_no=1, issued_queries=query_keys(plan)) is None
