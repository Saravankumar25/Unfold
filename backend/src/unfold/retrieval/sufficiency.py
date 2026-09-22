"""Deterministic context-sufficiency check + bounded follow-up planning (LLD §11, AI infra §24).

`assess(plan, result)` answers "is this evidence enough to generate from?" without a model call, using:
- top score — cross-encoder logit when reranked, otherwise the top fused score relative to the best achievable
  fused score (rank-1 agreement);
- query-term coverage — salient query terms (planner `terms`) present in the top items' text;
- explicit identifiers / paths — every one the user named must appear in the evidence;
- desired evidence types — the primary desired content type must be present.

`follow_up_plan(...)` turns the gaps into a narrower plan (missing terms / identifiers / content types). The
hybrid retriever runs at most `RetrievalConfig.max_followup_rounds` of them.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from unfold.indexing.tokenize import tokenize, tokenize_query
from unfold.retrieval.config import DEFAULT_CONFIG, RetrievalConfig
from unfold.retrieval.fusion import max_fused_score
from unfold.retrieval.models import RetrievalPlan, RetrievalResult, RetrievedItem

COVERAGE_WINDOW = 12  # items whose text counts towards coverage
_BREADTH_UP = {"narrow": "medium", "medium": "broad", "broad": "broad"}


def _terms(plan: RetrievalPlan) -> list[str]:
    raw = plan.structural_filters.get("terms")
    if isinstance(raw, list):
        return [str(t) for t in raw if str(t)]
    return tokenize_query(plan.query)


def _identifiers(plan: RetrievalPlan) -> list[str]:
    raw = plan.structural_filters.get("identifiers")
    return [str(t) for t in raw if str(t)] if isinstance(raw, list) else []


def _paths(plan: RetrievalPlan) -> list[str]:
    raw = plan.structural_filters.get("paths")
    return [str(t) for t in raw if str(t)] if isinstance(raw, list) else []


def _vocabulary(items: Iterable[RetrievedItem]) -> set[str]:
    vocab: set[str] = set()
    for it in items:
        vocab.update(tokenize(it.chunk.text))
        vocab.update(tokenize(it.chunk.path))
        if it.chunk.symbol_name:
            vocab.update(tokenize(it.chunk.symbol_name))
    return vocab


def assess(
    plan: RetrievalPlan, result: RetrievalResult, *, config: RetrievalConfig = DEFAULT_CONFIG
) -> tuple[bool, dict[str, Any]]:
    """(sufficient, details). Pure and deterministic."""
    primary = [i for i in result.items if not i.via_expansion][:COVERAGE_WINDOW]
    details: dict[str, Any] = {"item_count": len(result.items), "reasons": []}
    reasons: list[str] = details["reasons"]
    if not primary:
        reasons.append("no_results")
        details.update(
            coverage=0.0,
            missing_terms=_terms(plan),
            missing_identifiers=_identifiers(plan),
            missing_paths=_paths(plan),
            missing_evidence_types=list(plan.desired_evidence_types),
            score_ok=False,
            top_score=None,
            reranked=False,
        )
        return False, details

    # 1. top score
    reranked_scores = [i.rerank_score for i in primary if i.rerank_score is not None]
    if reranked_scores:
        top = max(reranked_scores)
        score_ok = top >= config.min_rerank_score
        details.update(reranked=True, top_score=round(top, 4))
    else:
        best = max_fused_score(config.weights_for(plan.intent), config.rrf_k) or 1.0
        top = max(i.fused_score for i in primary)
        ratio = top / best
        score_ok = ratio >= config.min_fused_ratio
        details.update(reranked=False, top_score=round(top, 6), top_fused_ratio=round(ratio, 4))
    details["score_ok"] = score_ok
    if not score_ok:
        reasons.append("low_top_score")

    # 2. coverage of salient query terms
    vocab = _vocabulary(primary)
    terms = _terms(plan)
    missing_terms = [t for t in terms if t not in vocab]
    coverage = 1.0 if not terms else (len(terms) - len(missing_terms)) / len(terms)
    details.update(coverage=round(coverage, 4), missing_terms=missing_terms)
    if coverage < config.min_term_coverage:
        reasons.append("low_term_coverage")

    # 3. explicit identifiers and paths
    missing_ids = [
        ident for ident in _identifiers(plan) if (toks := tokenize_query(ident)) and toks[0] not in vocab
    ]
    item_paths = [i.chunk.path.lower() for i in primary]
    missing_paths = [
        p
        for p in _paths(plan)
        if not any(ip == p.lower().strip("/") or ip.endswith("/" + p.lower().strip("/")) for ip in item_paths)
    ]
    details.update(missing_identifiers=missing_ids, missing_paths=missing_paths)
    if missing_ids:
        reasons.append("missing_identifiers")
    if missing_paths:
        reasons.append("missing_paths")

    # 4. evidence types
    present = {i.chunk.content_type.value for i in result.items}
    missing_types = [t for t in plan.desired_evidence_types if t not in present]
    details["missing_evidence_types"] = missing_types
    if plan.desired_evidence_types and plan.desired_evidence_types[0] in missing_types:
        reasons.append("missing_primary_evidence_type")

    return not reasons, details


def follow_up_plan(
    plan: RetrievalPlan,
    details: dict[str, Any],
    *,
    round_no: int,
    issued_queries: set[str] | None = None,
    config: RetrievalConfig = DEFAULT_CONFIG,
) -> RetrievalPlan | None:
    """Narrow plan targeting the gaps found by `assess`, or None when there is nothing new to ask.

    `round_no` is the 1-based number of the follow-up round being planned; `issued_queries` holds every
    lexical / semantic query already run (lowercased) so a round never repeats itself.
    """
    if round_no > config.max_followup_rounds:
        return None
    seen = issued_queries or set()
    missing_ids = [str(x) for x in details.get("missing_identifiers") or []]
    missing_paths = [str(x) for x in details.get("missing_paths") or []]
    missing_terms = [str(x) for x in details.get("missing_terms") or []]
    missing_types = [str(x) for x in details.get("missing_evidence_types") or []]
    # Drop sub-tokens of identifiers already listed (they are covered by the identifier query).
    id_tokens = set(tokenize_query(" ".join(missing_ids)))
    terms = [t for t in missing_terms if t not in id_tokens]

    lexical: list[str] = []
    semantic: list[str] = []
    gap = " ".join(dict.fromkeys([*missing_ids, *missing_paths, *terms]))
    if gap and gap.lower() not in seen:
        lexical.append(gap)
    if terms or missing_ids:
        sem = f"{plan.query} {' '.join(dict.fromkeys([*missing_ids, *terms]))}".strip()
        if sem.lower() not in seen:
            semantic.append(sem)
    content_types: list[str] = []
    if missing_types:
        content_types = missing_types
        if not semantic and plan.query.lower() + "|types" not in seen:
            semantic.append(plan.query)
    if not lexical and not semantic:
        return None
    filters = dict(plan.structural_filters)
    filters["followup_round"] = round_no
    if content_types:
        filters["content_types"] = content_types
    else:
        filters.pop("content_types", None)
    return plan.model_copy(
        update={
            "lexical_queries": lexical,
            "semantic_queries": semantic,
            "target_symbols": [],
            "graph_seed_entities": list(dict.fromkeys([*missing_ids]))[:6],
            "structural_filters": filters,
            "breadth": _BREADTH_UP[plan.breadth],
        },
        deep=True,
    )


def query_keys(plan: RetrievalPlan) -> set[str]:
    """Keys of the queries a plan issues (to feed `issued_queries`)."""
    keys = {q.lower() for q in [*plan.lexical_queries, *plan.semantic_queries]}
    if plan.structural_filters.get("content_types"):
        keys.add(plan.query.lower() + "|types")
    return keys
