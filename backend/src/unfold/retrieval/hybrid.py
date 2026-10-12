"""Hybrid retrieval orchestration (LLD §11, ESD §12, AI infra §15-§24).

```
plan ─┬─ lexical (BM25, k=40) ─┐
      ├─ dense (Qdrant, k=40) ─┤
      ├─ graph (seeds, k=20) ──┼─ weighted RRF (k=60) → hydrate → cross-encoder rerank top 30
      └─ structural (IR, k=20) ┘        → sufficiency (≤2 follow-up rounds) → graph expansion (≤6 chunks)
                                        → graph paths (workflow / architecture)
```

Degradation is explicit: a retriever / reranker / graph step that fails is recorded in `trace.degraded` (with
the exception type in `trace.errors`) and the pipeline continues with what is left — never with fabricated
results. If *every* first-stage retriever fails, `RetrievalFailed` is raised.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Awaitable, Sequence
from typing import TYPE_CHECKING

from unfold.core.errors import InputError
from unfold.core.logging import get_logger
from unfold.domain.enums import RetrievalIntent, RetrievalMode
from unfold.graph.models import GraphNode
from unfold.graph.queries import GraphQueries, graph_queries_for
from unfold.indexing.dense import DenseStore, dense_store_for
from unfold.indexing.lexical import LexicalIndexCache
from unfold.indexing.models import Chunk
from unfold.indexing.reranker import CrossEncoderReranker, reranker_for
from unfold.observability.tracing import retrieval_latency, set_attrs, span
from unfold.ports import BlobStore, ChunkStore
from unfold.project_ir.schema import ProjectIR
from unfold.retrieval.config import CHUNKABLE_LABELS, DEFAULT_CONFIG, FINAL_K_BY_BREADTH, RetrievalConfig
from unfold.retrieval.errors import RetrievalFailed
from unfold.retrieval.fusion import FusedCandidate, rrf_fuse
from unfold.retrieval.models import (
    GraphPath,
    RetrievalPlan,
    RetrievalResult,
    RetrievalTrace,
    RetrievedItem,
)
from unfold.retrieval.planner import SubsystemLike, plan_query
from unfold.retrieval.retrievers import (
    DenseRetriever,
    GraphRetriever,
    LexicalRetriever,
    SourceHits,
    StructuralRetriever,
)
from unfold.retrieval.sufficiency import assess, follow_up_plan, query_keys

if TYPE_CHECKING:
    from unfold.runtime import Runtime

log = get_logger(__name__)

HYDRATE_SLACK = 10  # extra candidates hydrated in case some fused ids are stale (index newer than chunks)
PATH_INTENTS = frozenset({RetrievalIntent.WORKFLOW, RetrievalIntent.ARCHITECTURE})
_NON_SEED_PREFIXES = ("file:", "module:", "dependency:", "framework:")


def lexical_cache_for(runtime: Runtime) -> LexicalIndexCache:
    """Process-wide BM25 artifact cache kept in `Runtime.extras`."""
    cache = runtime.extras.get("lexical_cache")
    if not isinstance(cache, LexicalIndexCache):
        cache = LexicalIndexCache()
        runtime.extras["lexical_cache"] = cache
    return cache


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 2)


def _node_ref(n: GraphNode) -> dict[str, str | None]:
    return {"uid": n.uid, "name": n.name, "label": n.label, "path": n.path}


class HybridRetriever:
    def __init__(
        self,
        *,
        project_id: str,
        chunks: ChunkStore,
        lexical_cache: LexicalIndexCache,
        objects: BlobStore,
        lexical_key: str | None,
        dense: DenseStore | None,
        reranker: CrossEncoderReranker | None,
        graph: GraphQueries | None,
        ir: ProjectIR | None,
        config: RetrievalConfig = DEFAULT_CONFIG,
    ) -> None:
        self.project_id = project_id
        self.chunks = chunks
        self.config = config
        self.ir = ir
        self.reranker = reranker
        self.graph = graph
        self.lexical = (
            LexicalRetriever(lexical_cache, objects, lexical_key, config) if lexical_key is not None else None
        )
        self.dense = DenseRetriever(dense, config) if dense is not None else None
        self.graph_retriever = GraphRetriever(graph, chunks, config) if graph is not None else None
        self.structural = StructuralRetriever(ir, chunks, config) if ir is not None else None

    @classmethod
    def for_project(
        cls,
        runtime: Runtime,
        project_id: str,
        ir: ProjectIR | None,
        lexical_key: str | None,
        *,
        config: RetrievalConfig = DEFAULT_CONFIG,
    ) -> HybridRetriever:
        """Wire every retriever from the process `Runtime` (singleton embedder / reranker / BM25 cache)."""
        return cls(
            project_id=project_id,
            chunks=runtime.chunks,
            lexical_cache=lexical_cache_for(runtime),
            objects=runtime.objects,
            lexical_key=lexical_key,
            dense=dense_store_for(runtime),
            reranker=reranker_for(runtime),
            graph=graph_queries_for(runtime, ir),
            ir=ir,
            config=config,
        )

    # --- public API -------------------------------------------------------------------------------------------

    def plan(
        self,
        query: str,
        mode: RetrievalMode = RetrievalMode.CHAT,
        *,
        subsystems: Sequence[SubsystemLike] | None = None,
        intent: RetrievalIntent | None = None,
    ) -> RetrievalPlan:
        return plan_query(self.project_id, query, mode, ir=self.ir, subsystems=subsystems, intent=intent)

    async def search(
        self,
        query: str,
        mode: RetrievalMode = RetrievalMode.CHAT,
        *,
        k: int | None = None,
        subsystems: Sequence[SubsystemLike] | None = None,
        intent: RetrievalIntent | None = None,
    ) -> RetrievalResult:
        """Plan + retrieve in one call (`k` defaults by plan breadth)."""
        plan = self.plan(query, mode, subsystems=subsystems, intent=intent)
        return await self.retrieve(plan, k=k or FINAL_K_BY_BREADTH[plan.breadth])

    async def retrieve(self, plan: RetrievalPlan, *, k: int = 12) -> RetrievalResult:
        if plan.project_id != self.project_id:
            raise InputError("Retrieval plan belongs to a different project.")
        if k <= 0:
            raise InputError("k must be positive.")
        started = time.perf_counter()
        cfg = self.config
        with span(
            "retrieval.retrieve",
            project_id=plan.project_id,
            intent=plan.intent.value,
            mode=plan.mode.value,
            breadth=plan.breadth,
            k=k,
            config_version=cfg.version,
        ) as sp:
            trace = RetrievalTrace(config_version=cfg.version)
            rankings: dict[str, list[str]] = {}
            weights: dict[str, float] = {}
            seeds: list[GraphNode] = []
            rerank_cache: dict[str, float] = {}

            attempted, failed = await self._run_round(plan, 0, rankings, weights, seeds, trace)
            if attempted and failed == attempted:
                raise RetrievalFailed(
                    "Every retriever failed for this query.",
                    detail={"degraded": list(trace.degraded), "errors": dict(trace.errors)},
                )
            items = await self._rank(plan, rankings, weights, k, trace, rerank_cache)
            result = RetrievalResult(plan=plan, items=items, trace=trace)
            sufficient, details = assess(plan, result, config=cfg)

            issued = query_keys(plan)
            rounds = 1
            while not sufficient and rounds <= cfg.max_followup_rounds:
                follow = follow_up_plan(plan, details, round_no=rounds, issued_queries=issued, config=cfg)
                if follow is None:
                    break
                issued |= query_keys(follow)
                await self._run_round(follow, rounds, rankings, weights, seeds, trace)
                rounds += 1
                result.items = await self._rank(plan, rankings, weights, k, trace, rerank_cache)
                sufficient, details = assess(plan, result, config=cfg)

            if self.graph is not None and "graph" not in trace.degraded and result.items:
                expanded = await self._expand(plan, result.items, trace)
                result.items.extend(expanded)
                if plan.intent in PATH_INTENTS:
                    result.graph_paths = await self._paths(plan, result.items, seeds, trace)

            trace.rounds = rounds
            trace.sufficiency = details
            result.sufficient = sufficient
            trace.latency_ms["total"] = _ms(started)
            retrieval_latency.record(
                time.perf_counter() - started, {"intent": plan.intent.value, "mode": plan.mode.value}
            )
            set_attrs(
                sp,
                items=len(result.items),
                rounds=rounds,
                sufficient=sufficient,
                degraded=",".join(trace.degraded),
                latency_ms=trace.latency_ms["total"],
            )
            log.info(
                "retrieval.done",
                project_id=plan.project_id,
                intent=plan.intent.value,
                items=len(result.items),
                rounds=rounds,
                sufficient=sufficient,
                degraded=trace.degraded,
                counts=trace.per_source_counts,
                latency_ms=trace.latency_ms["total"],
            )
            return result

    # --- stages -----------------------------------------------------------------------------------------------

    def _degrade(self, trace: RetrievalTrace, component: str, reason: str) -> None:
        if component not in trace.degraded:
            trace.degraded.append(component)
        trace.errors.setdefault(component, reason)

    async def _timed(
        self, name: str, call: Awaitable[SourceHits]
    ) -> tuple[str, SourceHits | BaseException, float]:
        t = time.perf_counter()
        try:
            return name, await call, _ms(t)
        except Exception as exc:  # a failing source degrades retrieval; it must not abort it
            return name, exc, _ms(t)

    async def _run_round(
        self,
        plan: RetrievalPlan,
        round_no: int,
        rankings: dict[str, list[str]],
        weights: dict[str, float],
        seeds: list[GraphNode],
        trace: RetrievalTrace,
    ) -> tuple[int, int]:
        """Run the applicable retrievers concurrently; returns (attempted, failed)."""
        cfg = self.config
        base = cfg.weights_for(plan.intent)
        suffix = "" if round_no == 0 else f"#r{round_no + 1}"
        decay = cfg.followup_weight_decay**round_no
        calls: list[Awaitable[tuple[str, SourceHits | BaseException, float]]] = []

        if plan.lexical_queries or round_no == 0:
            if self.lexical is None:
                self._degrade(trace, "lexical", "no_lexical_index")
            else:
                calls.append(self._timed("lexical", self.lexical.search(plan)))
        if plan.semantic_queries or round_no == 0:
            if self.dense is None:
                self._degrade(trace, "dense", "no_dense_index")
            else:
                calls.append(self._timed("dense", self.dense.search(plan)))
        if plan.graph_seed_entities or plan.target_symbols:
            if self.graph_retriever is None:
                self._degrade(trace, "graph", "no_graph")
            elif "graph" not in trace.degraded:
                calls.append(self._timed("graph", self.graph_retriever.search(plan)))
        if round_no == 0:
            if self.structural is None:
                self._degrade(trace, "structural", "no_project_ir")
            else:
                calls.append(self._timed("structural", self.structural.search(plan)))

        failed = 0
        for name, outcome, ms in await asyncio.gather(*calls):
            key = name + suffix
            trace.latency_ms[key] = ms
            if isinstance(outcome, BaseException):
                failed += 1
                self._degrade(trace, name, type(outcome).__name__)
                log.warning(
                    "retrieval.source_failed",
                    source=name,
                    round=round_no + 1,
                    error=type(outcome).__name__,
                    message=str(outcome)[:200],
                )
                continue
            trace.per_source_counts[key] = len(outcome.hits)
            rankings[key] = outcome.ids
            weights[key] = base[name] * decay
            if outcome.nodes:
                known = {n.uid for n in seeds}
                seeds.extend(n for n in outcome.nodes if n.uid not in known)
        return len(calls), failed

    async def _rank(
        self,
        plan: RetrievalPlan,
        rankings: dict[str, list[str]],
        weights: dict[str, float],
        k: int,
        trace: RetrievalTrace,
        rerank_cache: dict[str, float],
    ) -> list[RetrievedItem]:
        cfg = self.config
        t = time.perf_counter()
        fused = rrf_fuse(rankings, weights, k=cfg.rrf_k)
        trace.stage_counts["fused"] = len(fused)
        pool_n = max(cfg.rerank_top, k)
        pool = fused[: pool_n + HYDRATE_SLACK]
        chunks = {c.id: c for c in await self.chunks.get_many(plan.project_id, [c.chunk_id for c in pool])}
        stale = sum(1 for c in pool if c.chunk_id not in chunks)
        if stale:
            trace.stage_counts["stale_candidates"] = stale
            log.warning("retrieval.stale_candidates", project_id=plan.project_id, count=stale)
        cands: list[tuple[FusedCandidate, Chunk]] = [
            (c, chunks[c.chunk_id]) for c in pool if c.chunk_id in chunks
        ][:pool_n]
        trace.stage_counts["hydrated"] = len(cands)
        trace.latency_ms["fuse"] = _ms(t)

        head = cands[: cfg.rerank_top]
        rerank: dict[str, float] = {}
        if head:
            if self.reranker is None:
                self._degrade(trace, "reranker", "no_reranker")
            elif "reranker" not in trace.degraded:
                t = time.perf_counter()
                todo = [(c, ch) for c, ch in head if c.chunk_id not in rerank_cache]
                try:
                    scores = await self.reranker.rerank(
                        plan.query, [ch.text[: cfg.rerank_max_chars] for _, ch in todo]
                    )
                    rerank_cache.update({c.chunk_id: s for (c, _), s in zip(todo, scores, strict=True)})
                    rerank = {c.chunk_id: rerank_cache[c.chunk_id] for c, _ in head}
                except Exception as exc:  # reranker failure → fused order, recorded (LLD §11)
                    self._degrade(trace, "reranker", type(exc).__name__)
                    log.warning("retrieval.rerank_failed", error=type(exc).__name__)
                trace.latency_ms["rerank"] = round(trace.latency_ms.get("rerank", 0.0) + _ms(t), 2)
        trace.stage_counts["reranked"] = len(rerank)

        position = {c.chunk_id: i for i, (c, _) in enumerate(cands)}
        if rerank:
            head_sorted = sorted(head, key=lambda p: (-rerank[p[0].chunk_id], position[p[0].chunk_id]))
            ordered = head_sorted + cands[len(head) :]
        else:
            ordered = cands
        items: list[RetrievedItem] = []
        for c, ch in ordered[:k]:
            rs = rerank.get(c.chunk_id)
            items.append(
                RetrievedItem(
                    chunk=ch,
                    score=rs if rs is not None else c.score,
                    fused_score=c.score,
                    rerank_score=rs,
                    sources=dict(c.sources),
                )
            )
        return items

    async def _expand(
        self, plan: RetrievalPlan, items: list[RetrievedItem], trace: RetrievalTrace
    ) -> list[RetrievedItem]:
        """Depth-bounded neighbourhood expansion over allow-listed relationships (≤ expansion_max_chunks)."""
        assert self.graph is not None
        cfg = self.config
        t = time.perf_counter()
        seed_uids: list[str] = []
        for it in items[: cfg.expansion_seed_items]:
            uid = next((u for u in it.chunk.graph_node_ids if not u.startswith(_NON_SEED_PREFIXES)), None)
            if uid and uid not in seed_uids:
                seed_uids.append(uid)
        if not seed_uids:
            return []
        depth = cfg.expansion_depth_for(plan.intent)
        try:
            hoods = await asyncio.gather(
                *[
                    self.graph.neighbors(
                        plan.project_id,
                        uid,
                        depth=depth,
                        rel_types=cfg.expansion_rel_types,
                        limit=cfg.expansion_neighbor_limit,
                    )
                    for uid in seed_uids
                ]
            )
            candidates: list[str] = []
            for group in itertools.zip_longest(*[h.nodes for h in hoods]):
                for n in group:
                    if n is not None and n.label in CHUNKABLE_LABELS and n.uid not in seed_uids:
                        candidates.append(n.uid)
            candidates = list(dict.fromkeys(candidates))
            found = await self.chunks.for_symbols(plan.project_id, candidates) if candidates else []
        except Exception as exc:  # expansion is an enrichment: degrade, keep the ranked items
            self._degrade(trace, "graph_expansion", type(exc).__name__)
            log.warning("retrieval.expansion_failed", error=type(exc).__name__)
            return []
        have = {it.chunk.id for it in items}
        have_hash = {it.chunk.content_hash for it in items}
        by_uid: dict[str, list[Chunk]] = {}
        for c in found:
            if c.symbol_id:
                by_uid.setdefault(c.symbol_id, []).append(c)
        out: list[RetrievedItem] = []
        for uid in candidates:
            if len(out) >= cfg.expansion_max_chunks:
                break
            best = sorted(by_uid.get(uid, []), key=lambda c: (-c.importance, c.start_line, c.id))
            chosen = next((c for c in best if c.id not in have and c.content_hash not in have_hash), None)
            if chosen is None:
                continue
            have.add(chosen.id)
            have_hash.add(chosen.content_hash)
            out.append(
                RetrievedItem(
                    chunk=chosen,
                    score=0.0,
                    fused_score=0.0,
                    sources={"expansion": len(out) + 1},
                    via_expansion=True,
                )
            )
        trace.stage_counts["expanded"] = len(out)
        trace.latency_ms["expand"] = _ms(t)
        return out

    async def _paths(
        self,
        plan: RetrievalPlan,
        items: list[RetrievedItem],
        seeds: list[GraphNode],
        trace: RetrievalTrace,
    ) -> list[GraphPath]:
        """Shortest paths between the top entities (bounded) for workflow / architecture questions."""
        assert self.graph is not None
        cfg = self.config
        t = time.perf_counter()
        entities: list[str] = []
        for uid in [
            *[n.uid for n in seeds if n.label in CHUNKABLE_LABELS],
            *[
                next((u for u in it.chunk.graph_node_ids if not u.startswith(_NON_SEED_PREFIXES)), "")
                for it in items
                if not it.via_expansion
            ],
        ]:
            if uid and uid not in entities:
                entities.append(uid)
            if len(entities) >= cfg.path_max_entities:
                break
        pairs = list(itertools.combinations(entities, 2))[: cfg.path_max_pairs]
        if not pairs:
            return []
        try:
            results = await asyncio.gather(
                *[
                    self.graph.path_between(plan.project_id, a, b, max_hops=cfg.path_max_hops)
                    for a, b in pairs
                ]
            )
        except Exception as exc:
            self._degrade(trace, "graph_paths", type(exc).__name__)
            log.warning("retrieval.paths_failed", error=type(exc).__name__)
            return []
        paths: list[GraphPath] = []
        seen: set[tuple[str, ...]] = set()
        for res in results:
            if res is None or len(res.nodes) < 2:
                continue
            sig = tuple(n.uid for n in res.nodes)
            if sig in seen or tuple(reversed(sig)) in seen:
                continue
            seen.add(sig)
            paths.append(
                GraphPath(nodes=[_node_ref(n) for n in res.nodes], relationships=list(res.relationships))
            )
        trace.stage_counts["graph_paths"] = len(paths)
        trace.latency_ms["paths"] = _ms(t)
        return paths
