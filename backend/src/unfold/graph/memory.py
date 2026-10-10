"""In-memory project graph: the Neo4j graph rebuilt from the IR, queried in Python (ADR 0013).

Nodes and relationships come from `graph.builder.ir_graph` (the very function the Neo4j writer uses), subsystem ids
from `communities.detect` + `entity_subsystems` (what the pipeline writes with `set_subsystem`). The ten query
methods mirror `Neo4jGraphQueries` clause by clause: same filters, same orderings, same clamps. Known differences
(documented, measured in `tests/integration/test_retrieval_backend_parity.py`):

- `find_entities` fulltext tier: Neo4j scores with Lucene BM25 over every project in the index; here BM25 over this
  project only with a simplified standard-analyzer tokenizer. Exact / case-insensitive tiers are identical.
- `path_between`: when several shortest paths exist Neo4j returns an arbitrary one; here the lexicographically
  smallest by uid (same length, same endpoints).
- ties inside `ORDER BY` keys that Cypher leaves unspecified are broken by uid.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections import OrderedDict, defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from unfold.core.logging import get_logger
from unfold.graph.builder import ir_graph
from unfold.graph.communities import detect, entity_subsystems
from unfold.graph.models import (
    REL_TYPES,
    DegreeNode,
    EndpointFlow,
    GraphEdge,
    GraphNode,
    GraphOverview,
    ModuleDependency,
    Neighborhood,
    PathResult,
)
from unfold.graph.queries import (
    _DEGREE_RELS,
    MAX_DEPTH,
    MAX_LIMIT,
    MAX_PATH_HOPS,
    _clamp,
    _rel_pattern,
    _to_node,
)
from unfold.project_ir.schema import ProjectIR

if TYPE_CHECKING:
    from unfold.runtime import Runtime

_CACHE_MAX = 8
log = get_logger(__name__)
_WORD_RUN = re.compile(r"\w+")
_DEGREE_SET = frozenset(_DEGREE_RELS.split("|"))
_TOUCH_RELS = frozenset({"USES", "READS_FROM", "WRITES_TO", "DEPENDS_ON", "HANDLES", "EVIDENCED_BY"})
_BM25_K1, _BM25_B = 1.2, 0.75


def _tokens(text: str) -> list[str]:
    """Lucene standard analyzer, approximately (UAX#29): runs of letters, digits and `_`; a single `.` or `'`
    joins two runs only between two letters or two digits (so `a.b` is one token, `Foo.__init__` is two)."""
    text = text.lower()
    out: list[str] = []
    prev_end = -1
    for m in _WORD_RUN.finditer(text):
        word = m.group()
        if out and m.start() == prev_end + 1 and text[prev_end] in ".'’":
            a, b = out[-1][-1], word[0]
            if (a.isalpha() and b.isalpha()) or (a.isdigit() and b.isdigit()):
                out[-1] += text[prev_end] + word
                prev_end = m.end()
                continue
        out.append(word)
        prev_end = m.end()
    return out


def _nulls_last(value: Any) -> tuple[bool, Any]:
    return (value is None, value if value is not None else 0)


@dataclass
class _Node:
    uid: str
    labels: list[str] = field(default_factory=list)
    props: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "labels": ["Entity", *self.labels],
            **{k: self.props.get(k) for k in ("name", "kind", "path", "start_line", "end_line")},
            "subsystem_id": self.props.get("subsystem_id"),
            "source": self.props.get("source"),
        }

    def has(self, label: str) -> bool:
        return label in self.labels


class MemoryGraph:
    """One project's graph. Satisfies `graph.queries.GraphQueries`; `project_id` mismatches return nothing."""

    def __init__(
        self,
        project_id: str,
        nodes: dict[str, _Node],
        edges: dict[tuple[str, str, str], dict[str, Any]],
    ) -> None:
        self.project_id = project_id
        self.nodes = nodes
        self.edges = edges
        self._out: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self._in: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for t, s, d in edges:
            self._out[t][s].add(d)
            self._in[t][d].add(s)
        self._fulltext: _FullText | None = None
        self._names: _NameIndex | None = None

    @classmethod
    def from_ir(cls, ir: ProjectIR, subsystems: dict[str, str] | None = None) -> MemoryGraph:
        g = ir_graph(ir)
        nodes: dict[str, _Node] = {}
        for label in sorted(g.by_label):  # same order the Neo4j writer MERGEs labels in
            for row in g.by_label[label]:
                node = nodes.setdefault(row["uid"], _Node(row["uid"]))
                if label not in node.labels:
                    node.labels.append(label)
                node.props.update(row["props"])
        mapping = entity_subsystems(ir, detect(ir)) if subsystems is None else subsystems
        for uid, sid in mapping.items():
            if uid in nodes:
                nodes[uid].props["subsystem_id"] = sid
        merged = {k: v for k, v in g.inferred.items() if k not in g.det}
        merged.update(g.det)  # deterministic facts are never replaced by inferred ones
        edges = {k: {"source": v.get("source"), "confidence": v.get("confidence")} for k, v in merged.items()}
        return cls(ir.project.project_id, nodes, edges)

    # --- helpers ---------------------------------------------------------------------------------------------

    def _gn(self, uid: str, score: float | None = None) -> GraphNode | None:
        return _to_node(self.nodes[uid].as_dict(), score)

    def _nodes(self, items: Iterable[tuple[str, float | None]]) -> list[GraphNode]:
        return [n for uid, score in items if (n := self._gn(uid, score))]

    def _owns(self, project_id: str) -> bool:
        return project_id == self.project_id

    def _bfs(self, start: str, rels: Iterable[str], depth: int, *, directed: str | None) -> dict[str, int]:
        """Hop distance from `start` over `rels`; directed: None (undirected), 'out' or 'in'."""
        rel_list = list(rels)
        dist = {start: 0}
        frontier = deque([start])
        while frontier:
            cur = frontier.popleft()
            if dist[cur] >= depth:
                continue
            for t in rel_list:
                nxt: set[str] = set()
                if directed in (None, "out"):
                    nxt |= self._out[t].get(cur, set())
                if directed in (None, "in"):
                    nxt |= self._in[t].get(cur, set())
                for n in nxt:
                    if n not in dist:
                        dist[n] = dist[cur] + 1
                        frontier.append(n)
        return dist

    # --- the ten GraphQueries methods -------------------------------------------------------------------------

    async def get_node(self, project_id: str, uid: str) -> GraphNode | None:
        if not self._owns(project_id) or uid not in self.nodes:
            return None
        return self._gn(uid)

    async def find_entities(self, project_id: str, names: Sequence[str], limit: int = 20) -> list[GraphNode]:
        names = [n.strip() for n in names if n and n.strip()][:20]
        if not names or not self._owns(project_id):
            return []
        return await asyncio.to_thread(self._find_entities, names, _clamp(limit, 1, MAX_LIMIT))

    def _name_index(self) -> _NameIndex:
        if self._names is None:
            self._names = _NameIndex(self.nodes)
        return self._names

    def _find_entities(self, names: list[str], limit: int) -> list[GraphNode]:
        idx = self._name_index()
        rows: list[tuple[float, str]] = []
        for q in names:
            exact = idx.name.get(q, [])
            rows += [(3.0, uid) for uid in exact]
            second = {
                *idx.qualname.get(q, []),
                *idx.name_lower.get(q.lower(), []),
                *idx.qual_lower.get(q.lower(), []),
            }
            rows += [(2.0, uid) for uid in second - set(exact)]
        rows.sort(key=lambda r: (-r[0], r[1]))
        scored: dict[str, float] = {}
        for score, uid in rows[:limit]:  # LIMIT applies to rows before de-duplication, as in the Cypher
            if scored.get(uid, 0.0) < score:
                scored[uid] = score
        if len(scored) < limit:
            query_terms = {t for n in names for t in _tokens(n)}
            for uid, score in self._fulltext_scores(query_terms)[:limit]:
                scored.setdefault(uid, score)
        ordered = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        return self._nodes((uid, s) for uid, s in ordered)

    def _fulltext_scores(self, terms: set[str]) -> list[tuple[str, float]]:
        if self._fulltext is None:
            self._fulltext = _FullText(self.nodes)
        raw = self._fulltext.score(terms)
        if not raw:
            return []
        top = max(s for _, s in raw) or 1.0
        return [(uid, round(s / top, 4)) for uid, s in raw]

    async def neighbors(
        self,
        project_id: str,
        uid: str,
        depth: int = 1,
        rel_types: Sequence[str] | None = None,
        limit: int = 50,
    ) -> Neighborhood:
        depth = _clamp(depth, 1, MAX_DEPTH)
        limit = _clamp(limit, 1, MAX_LIMIT)
        rels = _rel_pattern(rel_types)  # validates the allow-list like the Neo4j implementation
        if not self._owns(project_id) or uid not in self.nodes:
            return Neighborhood()
        dist = self._bfs(uid, rels.split("|"), depth, directed=None)
        found = [
            (d, self.nodes[m].props.get("name"), m)
            for m, d in dist.items()
            if m != uid and not self.nodes[m].has("Project")
        ]
        found.sort(key=lambda r: (r[0], _nulls_last(r[1]), r[2]))
        nodes = self._nodes((m, float(d)) for d, _, m in found[:limit])
        uids = {uid, *[n.uid for n in nodes]}
        edges = sorted(
            (s, d, t) for t in rels.split("|") for s in uids for d in self._out[t].get(s, ()) if d in uids
        )[:2000]
        return Neighborhood(
            center=self._gn(uid),
            nodes=nodes,
            edges=[
                GraphEdge(
                    source=s,
                    target=d,
                    type=t,
                    provenance=self.edges[(t, s, d)].get("source"),
                    confidence=self.edges[(t, s, d)].get("confidence"),
                )
                for s, d, t in edges
            ],
        )

    def _directional(
        self, project_id: str, uid: str, depth: int, limit: int, direction: str
    ) -> list[GraphNode]:
        depth = _clamp(depth, 1, MAX_DEPTH)
        limit = _clamp(limit, 1, MAX_LIMIT)
        if not self._owns(project_id) or uid not in self.nodes:
            return []
        dist = self._bfs(uid, ["CALLS"], depth, directed=direction)
        found = sorted(
            ((d, self.nodes[m].props.get("name"), m) for m, d in dist.items() if m != uid),
            key=lambda r: (r[0], _nulls_last(r[1]), r[2]),
        )
        return self._nodes((m, float(d)) for d, _, m in found[:limit])

    async def callers(self, project_id: str, uid: str, depth: int = 1, limit: int = 50) -> list[GraphNode]:
        return self._directional(project_id, uid, depth, limit, "in")

    async def callees(self, project_id: str, uid: str, depth: int = 1, limit: int = 50) -> list[GraphNode]:
        return self._directional(project_id, uid, depth, limit, "out")

    async def path_between(
        self,
        project_id: str,
        a_uid: str,
        b_uid: str,
        max_hops: int = 4,
        rel_types: Sequence[str] | None = None,
    ) -> PathResult | None:
        hops = _clamp(max_hops, 1, MAX_PATH_HOPS)
        rels = _rel_pattern(rel_types).split("|")
        if not self._owns(project_id) or a_uid not in self.nodes or b_uid not in self.nodes:
            return None
        if a_uid == b_uid:
            return None  # `shortestPath((a)-[*..n]-(a))` yields no row in Neo4j 5
        prev: dict[str, tuple[str, str]] = {}
        seen = {a_uid}
        frontier = [a_uid]
        for _ in range(hops):
            nxt: dict[str, tuple[str, str]] = {}
            for cur in sorted(frontier):
                for t in sorted(rels):
                    for n in sorted(self._out[t].get(cur, set()) | self._in[t].get(cur, set())):
                        if n not in seen and n not in nxt:
                            nxt[n] = (cur, t)
            prev.update(nxt)
            seen |= set(nxt)
            if b_uid in nxt:
                chain = [b_uid]
                types: list[str] = []
                while chain[-1] != a_uid:
                    p, t = prev[chain[-1]]
                    chain.append(p)
                    types.append(t)
                chain.reverse()
                types.reverse()
                return PathResult(nodes=self._nodes((u, None) for u in chain), relationships=types)
            frontier = sorted(nxt)
            if not frontier:
                break
        return None

    async def subsystem_members(
        self, project_id: str, subsystem_id: str, limit: int = 200
    ) -> list[GraphNode]:
        if not self._owns(project_id):
            return []
        part_of = {
            s
            for s in self._in["PART_OF"].get(subsystem_id, set())
            if subsystem_id in self.nodes and self.nodes[subsystem_id].has("Subsystem")
        }
        members = [
            n
            for n in self.nodes.values()
            if not n.has("Subsystem") and (n.props.get("subsystem_id") == subsystem_id or n.uid in part_of)
        ]
        members.sort(
            key=lambda n: (
                _nulls_last(n.props.get("path")),
                _nulls_last(n.props.get("start_line")),
                n.uid,
            )
        )
        return [g for n in members[: _clamp(limit, 1, 2000)] if (g := self._gn(n.uid))]

    async def endpoint_flows(self, project_id: str, limit: int = 20) -> list[EndpointFlow]:
        if not self._owns(project_id):
            return []
        endpoints = sorted(
            (n for n in self.nodes.values() if n.has("APIEndpoint")),
            key=lambda n: (*(_nulls_last(n.props.get(k)) for k in ("path", "route", "method")), n.uid),
        )[: _clamp(limit, 1, MAX_LIMIT)]
        flows: list[EndpointFlow] = []
        for e in endpoints:
            handlers = sorted(
                h
                for h in self._out["HANDLES"].get(e.uid, set()) | self._in["HANDLES"].get(e.uid, set())
                if self.nodes[h].has("Function") or self.nodes[h].has("Method")
            )
            h_uid = handlers[0] if handlers else None
            callee_uids: list[str] = []
            if h_uid is not None:
                reach = self._bfs(h_uid, ["CALLS"], 2, directed="out")
                callee_uids = sorted(
                    (m for m in reach if m != h_uid),
                    key=lambda m: (
                        _nulls_last(self.nodes[m].props.get("path")),
                        _nulls_last(self.nodes[m].props.get("start_line")),
                        m,
                    ),
                )[:15]
            actors = [e.uid, *([h_uid] if h_uid else []), *callee_uids]
            touched = {
                t
                for a in actors
                for rel in _TOUCH_RELS
                for t in self._out[rel].get(a, set()) | self._in[rel].get(a, set())
                if self.nodes[t].has("Schema") or self.nodes[t].has("DataStore")
            }
            touches = sorted(touched, key=lambda t: (_nulls_last(self.nodes[t].props.get("name")), t))[:15]
            endpoint = self._gn(e.uid)
            if endpoint is None:
                continue
            flows.append(
                EndpointFlow(
                    endpoint=endpoint,
                    method=e.props.get("method"),
                    route=e.props.get("route"),
                    handler=self._gn(h_uid) if h_uid else None,
                    callees=self._nodes((u, None) for u in callee_uids),
                    touches=self._nodes((u, None) for u in touches),
                )
            )
        return flows

    async def overview(self, project_id: str, top_k: int = 15) -> GraphOverview:
        if not self._owns(project_id):
            return GraphOverview()
        label_counts: dict[str, int] = defaultdict(int)
        for n in self.nodes.values():
            for lbl in n.labels:
                label_counts[lbl] += 1
        rel_counts: dict[str, int] = defaultdict(int)
        degree: dict[str, int] = defaultdict(int)
        for t, s, d in self.edges:
            rel_counts[t] += 1
            if t in _DEGREE_SET:
                degree[s] += 1
                if d != s:
                    degree[d] += 1
        top = sorted(
            (
                (uid, deg)
                for uid, deg in degree.items()
                if deg > 0 and not self.nodes[uid].has("Project") and not self.nodes[uid].has("Library")
            ),
            key=lambda r: (-r[1], r[0]),
        )[: _clamp(top_k, 1, 100)]
        return GraphOverview(
            node_counts=dict(sorted(label_counts.items())),
            relationship_counts={t: c for t, c in sorted(rel_counts.items()) if t in REL_TYPES},
            top_nodes=[DegreeNode(node=gn, degree=deg) for uid, deg in top if (gn := self._gn(uid))],
        )

    async def module_dependencies(self, project_id: str, limit: int = 200) -> list[ModuleDependency]:
        if not self._owns(project_id):
            return []
        weights: dict[tuple[str, str], int] = defaultdict(int)
        for t, s, d in self.edges:
            if t != "IMPORTS":
                continue
            sm = s if self.nodes[s].has("Module") else self.nodes[s].props.get("module_id")
            tm = d if self.nodes[d].has("Module") else self.nodes[d].props.get("module_id")
            if sm is None or tm is None or sm == tm:
                continue
            weights[(sm, tm)] += 1
        rows = sorted(
            ((sm, tm, w) for (sm, tm), w in weights.items() if self._is_module(sm) and self._is_module(tm)),
            key=lambda r: (-r[2], r[0], r[1]),
        )[: _clamp(limit, 1, 2000)]
        out: list[ModuleDependency] = []
        for sm, tm, w in rows:
            sp, tp = self.nodes[sm].props, self.nodes[tm].props
            out.append(
                ModuleDependency(
                    source_uid=sm,
                    source_name=sp.get("name"),
                    source_path=sp.get("path"),
                    target_uid=tm,
                    target_name=tp.get("name"),
                    target_path=tp.get("path"),
                    weight=w,
                )
            )
        return out

    def _is_module(self, uid: str) -> bool:
        return uid in self.nodes and self.nodes[uid].has("Module")


class _NameIndex:
    """Exact and case-insensitive name / qualname lookups (the first two tiers of `find_entities`)."""

    def __init__(self, nodes: dict[str, _Node]) -> None:
        self.name: dict[str, list[str]] = defaultdict(list)
        self.qualname: dict[str, list[str]] = defaultdict(list)
        self.name_lower: dict[str, list[str]] = defaultdict(list)
        self.qual_lower: dict[str, list[str]] = defaultdict(list)
        for uid, node in nodes.items():
            if node.has("Project"):
                continue
            name, qual = node.props.get("name"), node.props.get("qualname")
            if isinstance(name, str):
                self.name[name].append(uid)
                self.name_lower[name.lower()].append(uid)
            if isinstance(qual, str):
                self.qualname[qual].append(uid)
                self.qual_lower[qual.lower()].append(uid)


class _FullText:
    """BM25 (Lucene defaults) over `name` and `qualname`, each its own field like the Neo4j fulltext index."""

    def __init__(self, nodes: dict[str, _Node]) -> None:
        # field -> term -> [(uid, term frequency, field length)]
        self.post: dict[str, dict[str, list[tuple[str, int, int]]]] = {"name": {}, "qualname": {}}
        self.docs = {"name": 0, "qualname": 0}
        total_len = {"name": 0, "qualname": 0}
        for uid, node in nodes.items():
            if node.has("Project"):
                continue
            for f in self.post:
                text = node.props.get(f)
                toks = _tokens(text) if isinstance(text, str) else []
                if not toks:
                    continue
                self.docs[f] += 1
                total_len[f] += len(toks)
                counts: dict[str, int] = defaultdict(int)
                for tok in toks:
                    counts[tok] += 1
                for tok, tf in counts.items():
                    self.post[f].setdefault(tok, []).append((uid, tf, len(toks)))
        self.avg_len = {f: total_len[f] / self.docs[f] if self.docs[f] else 1.0 for f in self.post}

    def score(self, terms: set[str]) -> list[tuple[str, float]]:
        scores: dict[str, float] = defaultdict(float)
        for f, postings in self.post.items():
            for term in terms:
                hits = postings.get(term, [])
                if not hits:
                    continue
                idf = math.log(1 + (self.docs[f] - len(hits) + 0.5) / (len(hits) + 0.5))
                for uid, tf, length in hits:
                    norm = _BM25_K1 * (1 - _BM25_B + _BM25_B * length / self.avg_len[f])
                    scores[uid] += idf * tf * (_BM25_K1 + 1) / (tf + norm)
        return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


class LazyMemoryGraph:
    """`GraphQueries` that builds the graph on first use, in a worker thread, once per project and process.

    `HybridRetriever.for_project` is synchronous, so construction cannot happen there without blocking the event
    loop (Louvain community detection is CPU-bound). The cache is an LRU keyed by project id; an entry is reused
    only while the loaded IR is the same object (the IR snapshot cache evicts and reloads independently).
    """

    def __init__(self, runtime: Runtime, ir: ProjectIR) -> None:
        self._rt = runtime
        self._ir = ir

    async def _graph(self) -> MemoryGraph:
        extras = self._rt.extras
        cache: OrderedDict[str, tuple[ProjectIR, MemoryGraph]] = extras.setdefault(
            "memory_graphs", OrderedDict()
        )
        pid = self._ir.project.project_id
        hit = cache.get(pid)
        if hit is not None and hit[0] is self._ir:
            cache.move_to_end(pid)
            return hit[1]
        lock: asyncio.Lock = extras.setdefault("memory_graphs_lock", asyncio.Lock())
        async with lock:
            hit = cache.get(pid)
            if hit is not None and hit[0] is self._ir:
                return hit[1]
            graph = await asyncio.to_thread(MemoryGraph.from_ir, self._ir)
            cache[pid] = (self._ir, graph)
            cache.move_to_end(pid)
            while len(cache) > _CACHE_MAX:
                cache.popitem(last=False)
            log.info("graph.memory_built", project_id=pid, nodes=len(graph.nodes), edges=len(graph.edges))
            return graph

    async def get_node(self, project_id: str, uid: str) -> GraphNode | None:
        return await (await self._graph()).get_node(project_id, uid)

    async def find_entities(self, project_id: str, names: Sequence[str], limit: int = 20) -> list[GraphNode]:
        return await (await self._graph()).find_entities(project_id, names, limit)

    async def neighbors(
        self,
        project_id: str,
        uid: str,
        depth: int = 1,
        rel_types: Sequence[str] | None = None,
        limit: int = 50,
    ) -> Neighborhood:
        return await (await self._graph()).neighbors(project_id, uid, depth, rel_types, limit)

    async def callers(self, project_id: str, uid: str, depth: int = 1, limit: int = 50) -> list[GraphNode]:
        return await (await self._graph()).callers(project_id, uid, depth, limit)

    async def callees(self, project_id: str, uid: str, depth: int = 1, limit: int = 50) -> list[GraphNode]:
        return await (await self._graph()).callees(project_id, uid, depth, limit)

    async def path_between(
        self,
        project_id: str,
        a_uid: str,
        b_uid: str,
        max_hops: int = 4,
        rel_types: Sequence[str] | None = None,
    ) -> PathResult | None:
        return await (await self._graph()).path_between(project_id, a_uid, b_uid, max_hops, rel_types)

    async def subsystem_members(
        self, project_id: str, subsystem_id: str, limit: int = 200
    ) -> list[GraphNode]:
        return await (await self._graph()).subsystem_members(project_id, subsystem_id, limit)

    async def endpoint_flows(self, project_id: str, limit: int = 20) -> list[EndpointFlow]:
        return await (await self._graph()).endpoint_flows(project_id, limit)

    async def overview(self, project_id: str, top_k: int = 15) -> GraphOverview:
        return await (await self._graph()).overview(project_id, top_k)

    async def module_dependencies(self, project_id: str, limit: int = 200) -> list[ModuleDependency]:
        return await (await self._graph()).module_dependencies(project_id, limit)


def memory_graph_for(runtime: Runtime, ir: ProjectIR) -> LazyMemoryGraph:
    return LazyMemoryGraph(runtime, ir)
