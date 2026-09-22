"""Weighted Reciprocal Rank Fusion (LLD §11, ESD §12). Pure and deterministic.

`score(d) = Σ_s w_s / (k + rank_s(d))` with 1-based ranks. Candidates are normalized by chunk id (the first
occurrence inside one ranking wins). Ties are broken by (best single rank, chunk id) so equal inputs always
produce the same order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class FusedCandidate:
    chunk_id: str
    score: float
    best_rank: int
    sources: dict[str, int] = field(default_factory=dict)  # ranking name -> 1-based rank


def rrf_fuse(
    rankings: Mapping[str, Sequence[str]],
    weights: Mapping[str, float] | None = None,
    *,
    k: int = 60,
) -> list[FusedCandidate]:
    """Fuse ranked chunk-id lists. A ranking with weight ≤ 0 (or missing from `weights`) is ignored.

    `weights=None` means every ranking has weight 1.0.
    """
    if k < 0:
        raise ValueError("rrf k must be >= 0")
    scores: dict[str, float] = {}
    best: dict[str, int] = {}
    sources: dict[str, dict[str, int]] = {}
    for name in sorted(rankings):  # fixed summation order → bit-identical floats across runs
        weight = 1.0 if weights is None else float(weights.get(name, 0.0))
        if weight <= 0.0:
            continue
        seen: set[str] = set()
        rank = 0
        for chunk_id in rankings[name]:
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            rank += 1
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (k + rank)
            best[chunk_id] = min(best.get(chunk_id, rank), rank)
            sources.setdefault(chunk_id, {})[name] = rank
    fused = [
        FusedCandidate(chunk_id=cid, score=score, best_rank=best[cid], sources=sources[cid])
        for cid, score in scores.items()
    ]
    fused.sort(key=lambda c: (-c.score, c.best_rank, c.chunk_id))
    return fused


def merge_variants(
    variants: Sequence[Sequence[str]],
    *,
    primary_weight: float = 1.0,
    variant_weight: float = 0.6,
    k: int = 60,
) -> list[str]:
    """Merge the rankings of several query variants run against one retriever into a single ranking.

    The first variant is the primary query; later variants get `variant_weight`.
    """
    if not variants:
        return []
    if len(variants) == 1:
        return list(dict.fromkeys(variants[0]))
    named = {f"v{i:02d}": list(v) for i, v in enumerate(variants)}
    weights = {name: (primary_weight if i == 0 else variant_weight) for i, name in enumerate(sorted(named))}
    return [c.chunk_id for c in rrf_fuse(named, weights, k=k)]


def max_fused_score(weights: Mapping[str, float], k: int = 60) -> float:
    """Best achievable fused score (rank 1 in every positively weighted ranking)."""
    return sum(w / (k + 1) for w in weights.values() if w > 0)
