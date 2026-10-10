"""The four first-stage retrievers of the hybrid pipeline (LLD §11, AI infra §13-§17).

Each returns `SourceHits`: a ranked list of `(chunk_id, score)` for one source. Scores are source-native (BM25,
cosine, graph proximity, structural overlap) and only used for tracing — fusion is rank-based.

- lexical     BM25 artifact via `LexicalIndexCache` (every lexical query variant, merged by inner RRF)
- dense       Qdrant via `DenseIndex.search` (semantic variants; optional subsystem / content-type filters)
- graph       seed names → `GraphQueries.find_entities` → `neighbors` → chunks by symbol id / file
- structural  Project IR lookups: target symbols, routes → handlers, named files, endpoints / schemas /
              services / infra / data stores / queues / AI components matching the plan

Failures propagate as exceptions; `HybridRetriever` turns them into `degraded` entries (never fake hits).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from unfold.graph.models import GraphNode
from unfold.graph.queries import GraphQueries
from unfold.indexing.dense import DenseIndex
from unfold.indexing.lexical import LexicalIndexCache
from unfold.indexing.models import Chunk
from unfold.indexing.store import ChunkRepository
from unfold.indexing.tokenize import tokenize_query
from unfold.ports import BlobStore
from unfold.project_ir.schema import Entity, ProjectIR
from unfold.retrieval.config import (
    CHUNKABLE_LABELS,
    DEFAULT_CONFIG,
    GRAPH_RETRIEVAL_REL_TYPES,
    RetrievalConfig,
)
from unfold.retrieval.fusion import merge_variants
from unfold.retrieval.models import RetrievalPlan

MAX_PATH_LOOKUPS = 8
CHUNKS_PER_FILE = 6
CHUNKS_PER_ENTITY_PATH = 2
ENTITIES_PER_KIND = 8
_ROUTE_PARAM = re.compile(r"\{[^}]*\}|<[^>]*>|:[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class SourceHits:
    source: str
    hits: list[tuple[str, float]] = field(default_factory=list)  # ranked (chunk id, source score)
    nodes: list[GraphNode] = field(default_factory=list)  # graph seeds (graph retriever only)
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ids(self) -> list[str]:
        return [cid for cid, _ in self.hits]


def _merge_scored(
    variants: Sequence[Sequence[tuple[str, float]]], k: int, config: RetrievalConfig
) -> list[tuple[str, float]]:
    best: dict[str, float] = {}
    for variant in variants:
        for cid, score in variant:
            best[cid] = max(best.get(cid, score), score)
    order = merge_variants(
        [[cid for cid, _ in v] for v in variants], variant_weight=config.variant_weight, k=config.rrf_k
    )
    return [(cid, best[cid]) for cid in order[:k]]


def _order_chunks(chunks: Iterable[Chunk]) -> list[Chunk]:
    return sorted(chunks, key=lambda c: (-c.importance, c.start_line, c.id))


# --- lexical ----------------------------------------------------------------------------------------------------


class LexicalRetriever:
    def __init__(
        self,
        cache: LexicalIndexCache,
        objects: BlobStore,
        key: str,
        config: RetrievalConfig = DEFAULT_CONFIG,
    ) -> None:
        self.cache = cache
        self.objects = objects
        self.key = key
        self.config = config

    async def search(self, plan: RetrievalPlan, k: int | None = None) -> SourceHits:
        k = k or self.config.k_lexical
        queries = [q for q in plan.lexical_queries if q.strip()] or [plan.query]
        artifact = await self.cache.get(self.objects, self.key)
        # BM25 scoring is numpy work over the whole corpus: keep it off the event loop.
        variants = [await asyncio.to_thread(artifact.search, q, k) for q in queries]
        return SourceHits(
            "lexical", _merge_scored(variants, k, self.config), detail={"queries": len(queries)}
        )


# --- dense ------------------------------------------------------------------------------------------------------


class DenseRetriever:
    def __init__(self, dense: DenseIndex, config: RetrievalConfig = DEFAULT_CONFIG) -> None:
        self.dense = dense
        self.config = config

    async def search(self, plan: RetrievalPlan, k: int | None = None) -> SourceHits:
        k = k or self.config.k_dense
        queries = [q for q in plan.semantic_queries if q.strip()] or [plan.query]
        raw_types = plan.structural_filters.get("content_types")
        content_types = [str(t) for t in raw_types] if isinstance(raw_types, list) and raw_types else None
        calls = [self.dense.search(plan.project_id, q, k=k, content_types=content_types) for q in queries]
        if plan.target_subsystems and not plan.structural_filters.get("followup_round"):
            calls.append(
                self.dense.search(
                    plan.project_id,
                    queries[0],
                    k=k,
                    content_types=content_types,
                    subsystem_ids=plan.target_subsystems,
                )
            )
        variants = await asyncio.gather(*calls)
        return SourceHits(
            "dense",
            _merge_scored(variants, k, self.config),
            detail={"queries": len(calls), "content_types": content_types or []},
        )


# --- graph ------------------------------------------------------------------------------------------------------


def _interleave(groups: Sequence[Sequence[GraphNode]]) -> list[GraphNode]:
    out: list[GraphNode] = []
    longest = max((len(g) for g in groups), default=0)
    for i in range(longest):
        for g in groups:
            if i < len(g):
                out.append(g[i])
    return out


class GraphRetriever:
    def __init__(
        self, graph: GraphQueries, chunks: ChunkRepository, config: RetrievalConfig = DEFAULT_CONFIG
    ) -> None:
        self.graph = graph
        self.chunks = chunks
        self.config = config

    async def search(self, plan: RetrievalPlan, k: int | None = None) -> SourceHits:
        k = k or self.config.k_graph
        pid = plan.project_id
        cfg = self.config
        found: list[GraphNode] = []
        if plan.graph_seed_entities:
            found = await self.graph.find_entities(pid, plan.graph_seed_entities, limit=cfg.graph_seed_limit)
        found = [n for n in found if n.label not in {"Library", "Framework", "Project"}]

        # Seeds in priority order: IR-matched target symbols, then name matches by score.
        seed_uids = list(dict.fromkeys([*plan.target_symbols, *[n.uid for n in found]]))
        expand = seed_uids[: cfg.graph_seed_expand]
        hoods = await asyncio.gather(
            *[
                self.graph.neighbors(
                    pid, uid, depth=1, rel_types=GRAPH_RETRIEVAL_REL_TYPES, limit=cfg.graph_neighbor_limit
                )
                for uid in expand
            ]
        )
        neighbor_nodes = _interleave([h.nodes for h in hoods])

        labels: dict[str, str] = {n.uid: n.label for n in found}
        for h in hoods:
            for n in h.nodes:
                labels.setdefault(n.uid, n.label)
        ordered: list[str] = list(dict.fromkeys([*seed_uids, *[n.uid for n in neighbor_nodes]]))
        chunkable = [
            uid
            for uid in ordered
            if uid in plan.target_symbols or labels.get(uid) in CHUNKABLE_LABELS or uid not in labels
        ]
        by_symbol: dict[str, list[Chunk]] = {}
        for c in await self.chunks.for_symbols(pid, chunkable):
            if c.symbol_id:
                by_symbol.setdefault(c.symbol_id, []).append(c)
        file_seeds = [n for n in found if n.label == "File" and n.path][: cfg.graph_file_limit]
        file_chunks = await asyncio.gather(*[self.chunks.by_path(pid, str(n.path)) for n in file_seeds])
        by_file = {n.uid: _order_chunks(cs)[:2] for n, cs in zip(file_seeds, file_chunks, strict=True)}

        hits: list[tuple[str, float]] = []
        seen: set[str] = set()
        for pos, uid in enumerate(ordered):
            for c in _order_chunks(by_symbol.get(uid, [])) + by_file.get(uid, []):
                if c.id in seen:
                    continue
                seen.add(c.id)
                hits.append((c.id, round(1.0 / (1 + pos), 6)))
            if len(hits) >= k:
                break
        return SourceHits(
            "graph",
            hits[:k],
            nodes=found,
            detail={"seeds": len(seed_uids), "neighbors": len(neighbor_nodes)},
        )


# --- structural -------------------------------------------------------------------------------------------------


def _norm_route(route: str) -> str:
    return _ROUTE_PARAM.sub("*", route.strip().rstrip("/").lower()) or "/"


@dataclass
class _Ref:
    ids: list[str] = field(default_factory=list)  # entity ids resolved via chunks.symbol_id
    path: str | None = None  # path-based resolution
    start: int | None = None
    end: int | None = None
    per_path: int = CHUNKS_PER_ENTITY_PATH
    score: float = 0.0


def _entity_text(e: Entity) -> str:
    parts: list[str] = [e.name, e.path or ""]
    for attr in ("route", "method", "technology", "category", "service_kind", "resource_kind", "provider"):
        v = getattr(e, attr, None)
        if isinstance(v, str):
            parts.append(v)
    for attr in ("component_kind", "schema_kind", "image"):
        v = getattr(e, attr, None)
        if isinstance(v, str):
            parts.append(v)
    fields = getattr(e, "fields", None)
    if isinstance(fields, list):
        parts.extend(str(f.get("name", "")) for f in fields if isinstance(f, dict))
    return " ".join(parts)


class StructuralRetriever:
    """Similarity-free lookups over the Project IR (AI infra §17)."""

    def __init__(
        self, ir: ProjectIR, chunks: ChunkRepository, config: RetrievalConfig = DEFAULT_CONFIG
    ) -> None:
        self.ir = ir
        self.chunks = chunks
        self.config = config

    def _kind_entities(self, kind: str) -> Sequence[Entity]:
        ir = self.ir
        groups: dict[str, Sequence[Entity]] = {
            "endpoints": ir.endpoints,
            "schemas": ir.schemas,
            "services": ir.services,
            "infrastructure": ir.infrastructure,
            "data_stores": ir.data_stores,
            "queues": ir.queues,
            "ai_components": ir.ai_components,
        }
        return groups.get(kind, ())

    def _entity_ref(self, kind: str, e: Entity, score: float) -> _Ref:
        if kind == "endpoints":
            handler = getattr(e, "handler_id", None)
            return _Ref(ids=[i for i in (handler, e.id) if i], score=score)
        if kind == "schemas":
            sym = getattr(e, "symbol_id", None)
            return _Ref(ids=[i for i in (sym, e.id) if i], score=score)
        return _Ref(path=e.path, start=e.start_line, end=e.end_line, score=score)

    def plan_refs(self, plan: RetrievalPlan) -> list[_Ref]:
        """Ranked references for a plan (pure; exposed for unit tests)."""
        f = plan.structural_filters
        refs: list[_Ref] = []
        if plan.target_symbols:
            refs.append(_Ref(ids=list(plan.target_symbols), score=3.0))

        routes = [str(r) for r in f.get("routes") or []]
        methods = {str(m).upper() for m in f.get("http_methods") or []}
        if routes:
            wanted = {_norm_route(r) for r in routes}
            for e in self.ir.endpoints:
                if _norm_route(e.route) in wanted and (not methods or e.method.upper() in methods):
                    refs.append(self._entity_ref("endpoints", e, 2.5))

        known_paths = {fi.path for fi in self.ir.files}
        for p in [str(x) for x in f.get("paths") or []]:
            if p in known_paths:
                refs.append(_Ref(path=p, per_path=CHUNKS_PER_FILE, score=2.0))

        terms = {str(t) for t in [*(f.get("terms") or []), *(f.get("expansions") or [])]}
        ranked: list[tuple[float, str, str, _Ref]] = []
        fill: list[_Ref] = []
        for kind in [str(x) for x in f.get("entity_kinds") or []]:
            entities = self._kind_entities(kind)
            scored: list[tuple[float, str, str, _Ref]] = []
            zero: list[_Ref] = []
            for ent in entities:
                overlap = len(terms & set(tokenize_query(_entity_text(ent)))) if terms else 0
                if overlap:
                    ref = self._entity_ref(kind, ent, float(overlap))
                    scored.append((float(overlap), ent.path or "", ent.id, ref))
                elif len(entities) <= ENTITIES_PER_KIND:
                    zero.append(self._entity_ref(kind, ent, 0.0))
            scored.sort(key=lambda s: (-s[0], s[1], s[2]))
            ranked.extend(scored[:ENTITIES_PER_KIND])
            fill.extend(zero)
        ranked.sort(key=lambda s: (-s[0], s[1], s[2]))
        refs.extend(r for *_, r in ranked)
        refs.extend(fill)
        return refs

    async def search(self, plan: RetrievalPlan, k: int | None = None) -> SourceHits:
        k = k or self.config.k_structural
        pid = plan.project_id
        refs = self.plan_refs(plan)
        if not refs:
            return SourceHits("structural", detail={"refs": 0})
        ids = list(dict.fromkeys(i for r in refs for i in r.ids))
        paths = list(dict.fromkeys(r.path for r in refs if r.path))[:MAX_PATH_LOOKUPS]
        sym_chunks, path_chunks = await asyncio.gather(
            self.chunks.for_symbols(pid, ids),
            asyncio.gather(*[self.chunks.by_path(pid, p) for p in paths]),
        )
        by_symbol: dict[str, list[Chunk]] = {}
        for c in sym_chunks:
            if c.symbol_id:
                by_symbol.setdefault(c.symbol_id, []).append(c)
        by_path = dict(zip(paths, path_chunks, strict=True))

        hits: list[tuple[str, float]] = []
        seen: set[str] = set()

        def add(chunks: Iterable[Chunk], score: float) -> None:
            for c in chunks:
                if c.id not in seen:
                    seen.add(c.id)
                    hits.append((c.id, score))

        for r in refs:
            if len(hits) >= k:
                break
            for i in r.ids:
                add(_order_chunks(by_symbol.get(i, [])), r.score)
            if r.path and r.path in by_path:
                file_chunks = by_path[r.path]
                if r.start is not None:
                    lo, hi = r.start, r.end or r.start
                    overlapping = [c for c in file_chunks if c.start_line <= hi and c.end_line >= lo]
                    add(overlapping[: r.per_path] or _order_chunks(file_chunks)[:1], r.score)
                else:
                    add(_order_chunks(file_chunks)[: r.per_path], r.score)
        return SourceHits("structural", hits[:k], detail={"refs": len(refs)})
