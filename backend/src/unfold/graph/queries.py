"""Read-only graph query templates (LLD §9). Parameterised, project-scoped and depth-bounded — nothing else.

Only allow-listed relationship types and clamped integer depths are ever interpolated into Cypher; every other
value (project id, uids, names, limits) is a query parameter. Every MATCH is restricted to `project_id`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from unfold.core.logging import get_logger
from unfold.graph.builder import FULLTEXT_INDEX
from unfold.graph.models import (
    REL_TYPES,
    TRAVERSAL_REL_TYPES,
    DegreeNode,
    EndpointFlow,
    GraphEdge,
    GraphNode,
    GraphOverview,
    ModuleDependency,
    Neighborhood,
    PathResult,
    check_rel_type,
)

if TYPE_CHECKING:
    from unfold.persistence.neo4j_store import Neo4jStore
    from unfold.project_ir.schema import ProjectIR
    from unfold.runtime import Runtime

log = get_logger(__name__)
MAX_DEPTH = 3
MAX_PATH_HOPS = 6
MAX_LIMIT = 500
_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')
_NODE_FIELDS = (
    "{uid: n.uid, labels: labels(n), name: n.name, kind: n.kind, path: n.path, start_line: n.start_line, "
    "end_line: n.end_line, subsystem_id: n.subsystem_id, source: n.source}"
)
# Degree for "top nodes" counts semantic edges only (containment would make every directory a hub).
_DEGREE_RELS = "IMPORTS|CALLS|INHERITS|IMPLEMENTS|USES|DEPENDS_ON|HANDLES|READS_FROM|WRITES_TO|EXPOSES"


def _node_map(var: str) -> str:
    return _NODE_FIELDS.replace("n.", f"{var}.").replace("labels(n)", f"labels({var})")


def _to_node(data: dict[str, Any] | None, score: float | None = None) -> GraphNode | None:
    if not data or not data.get("uid"):
        return None
    labels = [lbl for lbl in (data.get("labels") or []) if lbl != "Entity"]
    return GraphNode(
        uid=data["uid"],
        label=labels[0] if labels else "Entity",
        name=data.get("name") or data["uid"],
        kind=data.get("kind"),
        path=data.get("path"),
        start_line=data.get("start_line"),
        end_line=data.get("end_line"),
        subsystem_id=data.get("subsystem_id"),
        source=data.get("source"),
        score=score,
    )


def _rel_pattern(rel_types: Sequence[str] | None, default: Sequence[str] = TRAVERSAL_REL_TYPES) -> str:
    types = list(rel_types) if rel_types else list(default)
    for t in types:
        check_rel_type(t)
    return "|".join(sorted(set(types)))


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def escape_lucene(text: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", text)


class GraphQueries(Protocol):
    """The read side retrieval and the guide use. `Neo4jGraphQueries` is the default and the parity oracle."""

    async def get_node(self, project_id: str, uid: str) -> GraphNode | None: ...

    async def find_entities(
        self, project_id: str, names: Sequence[str], limit: int = 20
    ) -> list[GraphNode]: ...

    async def neighbors(
        self,
        project_id: str,
        uid: str,
        depth: int = 1,
        rel_types: Sequence[str] | None = None,
        limit: int = 50,
    ) -> Neighborhood: ...

    async def callers(
        self, project_id: str, uid: str, depth: int = 1, limit: int = 50
    ) -> list[GraphNode]: ...

    async def callees(
        self, project_id: str, uid: str, depth: int = 1, limit: int = 50
    ) -> list[GraphNode]: ...

    async def path_between(
        self,
        project_id: str,
        a_uid: str,
        b_uid: str,
        max_hops: int = 4,
        rel_types: Sequence[str] | None = None,
    ) -> PathResult | None: ...

    async def subsystem_members(
        self, project_id: str, subsystem_id: str, limit: int = 200
    ) -> list[GraphNode]: ...

    async def endpoint_flows(self, project_id: str, limit: int = 20) -> list[EndpointFlow]: ...

    async def overview(self, project_id: str, top_k: int = 15) -> GraphOverview: ...

    async def module_dependencies(self, project_id: str, limit: int = 200) -> list[ModuleDependency]: ...


def graph_queries_for(rt: Runtime, ir: ProjectIR | None) -> GraphQueries | None:
    """The configured read side (`GRAPH_BACKEND`); None = graph retrieval off (the recorded degradation path)."""
    backend = rt.settings.graph_backend
    if backend == "neo4j":
        return Neo4jGraphQueries(rt.neo4j)
    if backend == "memory" and ir is not None:
        from unfold.graph.memory import memory_graph_for

        return memory_graph_for(rt, ir)
    if backend == "memory":
        log.warning("graph.memory_without_ir")  # retrieval runs without the graph; the trace records it
    return None


def evict_memory_graph(rt: Runtime, project_id: str) -> None:
    """Drop the process-cached in-memory graph of a project (`graph.memory.memory_graph_for` fills it)."""
    rt.extras.get("memory_graphs", {}).pop(project_id, None)


class Neo4jGraphQueries:
    def __init__(self, neo4j: Neo4jStore) -> None:
        self.neo4j = neo4j

    async def get_node(self, project_id: str, uid: str) -> GraphNode | None:
        rows = await self.neo4j.run(
            f"MATCH (n:Entity {{uid: $uid}}) WHERE n.project_id = $pid RETURN {_node_map('n')} AS n",
            uid=uid,
            pid=project_id,
        )
        return _to_node(rows[0]["n"]) if rows else None

    async def find_entities(self, project_id: str, names: Sequence[str], limit: int = 20) -> list[GraphNode]:
        """Exact (3.0) > case-insensitive / qualname (2.0) > fulltext (≤1.0) name matches, deduplicated."""
        names = [n.strip() for n in names if n and n.strip()][:20]
        if not names:
            return []
        limit = _clamp(limit, 1, MAX_LIMIT)
        rows = await self.neo4j.run(
            "UNWIND $names AS q "
            "MATCH (n:Entity) WHERE n.project_id = $pid AND NOT n:Project "
            "AND (n.name = q OR n.qualname = q OR toLower(n.name) = toLower(q) OR toLower(n.qualname) = toLower(q)) "
            f"RETURN {_node_map('n')} AS n, CASE WHEN n.name = q THEN 3.0 ELSE 2.0 END AS score "
            "ORDER BY score DESC, n.uid LIMIT $limit",
            names=names,
            pid=project_id,
            limit=limit,
        )
        found: dict[str, GraphNode] = {}
        for r in rows:
            node = _to_node(r["n"], float(r["score"]))
            if node and (node.uid not in found or (found[node.uid].score or 0) < (node.score or 0)):
                found[node.uid] = node
        if len(found) < limit:
            query = " OR ".join(escape_lucene(n) for n in names)
            ft_rows = await self.neo4j.run(
                f"CALL db.index.fulltext.queryNodes('{FULLTEXT_INDEX}', $q) YIELD node, score "
                "WHERE node.project_id = $pid AND NOT node:Project "
                f"RETURN {_node_map('node')} AS n, score ORDER BY score DESC, node.uid LIMIT $limit",
                q=query,
                pid=project_id,
                limit=limit,
            )
            top = max((float(r["score"]) for r in ft_rows), default=1.0) or 1.0
            for r in ft_rows:
                node = _to_node(r["n"], round(float(r["score"]) / top, 4))
                if node and node.uid not in found:
                    found[node.uid] = node
        return sorted(found.values(), key=lambda n: (-(n.score or 0.0), n.uid))[:limit]

    async def neighbors(
        self,
        project_id: str,
        uid: str,
        depth: int = 1,
        rel_types: Sequence[str] | None = None,
        limit: int = 50,
    ) -> Neighborhood:
        """Nodes within `depth` (1–3) hops over allow-listed relationships, plus the edges among them."""
        depth = _clamp(depth, 1, MAX_DEPTH)
        limit = _clamp(limit, 1, MAX_LIMIT)
        rels = _rel_pattern(rel_types)
        rows = await self.neo4j.run(
            "MATCH (c:Entity {uid: $uid}) WHERE c.project_id = $pid "
            f"OPTIONAL MATCH p = (c)-[:{rels}*1..{depth}]-(m:Entity) "
            "WHERE m.project_id = $pid AND m.uid <> c.uid AND NOT m:Project "
            "WITH c, m, min(length(p)) AS dist "
            f"RETURN {_node_map('c')} AS c, {_node_map('m')} AS m, dist "
            "ORDER BY dist, m.name, m.uid LIMIT $limit",
            uid=uid,
            pid=project_id,
            limit=limit,
        )
        if not rows:
            return Neighborhood()
        center = _to_node(rows[0]["c"])
        nodes = [
            n for r in rows if (n := _to_node(r["m"], float(r["dist"]) if r["dist"] is not None else None))
        ]
        uids = [uid, *[n.uid for n in nodes]]
        edges = await self._edges_among(project_id, uids, rels)
        return Neighborhood(center=center, nodes=nodes, edges=edges)

    async def _edges_among(self, project_id: str, uids: list[str], rels: str) -> list[GraphEdge]:
        rows = await self.neo4j.run(
            f"MATCH (a:Entity)-[r:{rels}]->(b:Entity) "
            "WHERE a.uid IN $uids AND b.uid IN $uids AND a.project_id = $pid AND b.project_id = $pid "
            "RETURN a.uid AS s, b.uid AS t, type(r) AS type, r.source AS src, r.confidence AS conf "
            "ORDER BY s, t, type LIMIT 2000",
            uids=uids,
            pid=project_id,
        )
        return [
            GraphEdge(source=r["s"], target=r["t"], type=r["type"], provenance=r["src"], confidence=r["conf"])
            for r in rows
        ]

    async def _directional(
        self, project_id: str, uid: str, depth: int, limit: int, outgoing: bool
    ) -> list[GraphNode]:
        depth = _clamp(depth, 1, MAX_DEPTH)
        limit = _clamp(limit, 1, MAX_LIMIT)
        pattern = (
            f"(c)-[:CALLS*1..{depth}]->(m:Entity)" if outgoing else f"(c)<-[:CALLS*1..{depth}]-(m:Entity)"
        )
        rows = await self.neo4j.run(
            "MATCH (c:Entity {uid: $uid}) WHERE c.project_id = $pid "
            f"MATCH p = {pattern} WHERE m.project_id = $pid AND m.uid <> c.uid "
            "WITH m, min(length(p)) AS dist "
            f"RETURN {_node_map('m')} AS m, dist ORDER BY dist, m.name, m.uid LIMIT $limit",
            uid=uid,
            pid=project_id,
            limit=limit,
        )
        return [n for r in rows if (n := _to_node(r["m"], float(r["dist"])))]

    async def callers(self, project_id: str, uid: str, depth: int = 1, limit: int = 50) -> list[GraphNode]:
        """Who calls `uid` (score = hop distance)."""
        return await self._directional(project_id, uid, depth, limit, outgoing=False)

    async def callees(self, project_id: str, uid: str, depth: int = 1, limit: int = 50) -> list[GraphNode]:
        """What `uid` calls (score = hop distance)."""
        return await self._directional(project_id, uid, depth, limit, outgoing=True)

    async def path_between(
        self,
        project_id: str,
        a_uid: str,
        b_uid: str,
        max_hops: int = 4,
        rel_types: Sequence[str] | None = None,
    ) -> PathResult | None:
        """Shortest undirected path (≤6 hops) over allow-listed relationship types, entirely inside the project."""
        hops = _clamp(max_hops, 1, MAX_PATH_HOPS)
        rels = _rel_pattern(rel_types)
        rows = await self.neo4j.run(
            "MATCH (a:Entity {uid: $a}) WHERE a.project_id = $pid "
            "MATCH (b:Entity {uid: $b}) WHERE b.project_id = $pid "
            f"MATCH p = shortestPath((a)-[:{rels}*..{hops}]-(b)) "
            "WHERE all(x IN nodes(p) WHERE x.project_id = $pid) "
            f"RETURN [n IN nodes(p) | {_node_map('n')}] AS nodes, [r IN relationships(p) | type(r)] AS rels",
            a=a_uid,
            b=b_uid,
            pid=project_id,
        )
        if not rows:
            return None
        nodes = [n for d in rows[0]["nodes"] if (n := _to_node(d))]
        return PathResult(nodes=nodes, relationships=list(rows[0]["rels"]))

    async def subsystem_members(
        self, project_id: str, subsystem_id: str, limit: int = 200
    ) -> list[GraphNode]:
        rows = await self.neo4j.run(
            "MATCH (n:Entity) WHERE n.project_id = $pid AND NOT n:Subsystem AND "
            "(n.subsystem_id = $sid OR EXISTS { MATCH (n)-[:PART_OF]->(s:Subsystem {uid: $sid}) }) "
            f"RETURN {_node_map('n')} AS n ORDER BY n.path, n.start_line, n.uid LIMIT $limit",
            pid=project_id,
            sid=subsystem_id,
            limit=_clamp(limit, 1, 2000),
        )
        return [n for r in rows if (n := _to_node(r["n"]))]

    async def endpoint_flows(self, project_id: str, limit: int = 20) -> list[EndpointFlow]:
        """endpoint → handler → callees (≤2 hops, ≤15) → schemas / data stores used along the way."""
        rows = await self.neo4j.run(
            "MATCH (e:APIEndpoint) WHERE e.project_id = $pid "
            "WITH e ORDER BY e.path, e.route, e.method, e.uid LIMIT $limit "
            "CALL (e) { "
            "  OPTIONAL MATCH (e)-[:HANDLES]-(h:Entity) "
            "  WHERE (h:Function OR h:Method) AND h.project_id = $pid "
            "  RETURN h ORDER BY h.uid LIMIT 1 "
            "} "
            "CALL (h) { "
            "  OPTIONAL MATCH (h)-[:CALLS*1..2]->(c:Entity) WHERE c.project_id = $pid AND c <> h "
            "  WITH DISTINCT c ORDER BY c.path, c.start_line "
            "  RETURN collect(c)[..15] AS callees "
            "} "
            "CALL (e, h, callees) { "
            "  UNWIND [x IN [e, h] + callees WHERE x IS NOT NULL] AS actor "
            "  MATCH (actor)-[:USES|READS_FROM|WRITES_TO|DEPENDS_ON|HANDLES|EVIDENCED_BY]-(t:Entity) "
            "  WHERE (t:Schema OR t:DataStore) AND t.project_id = $pid "
            "  WITH DISTINCT t ORDER BY t.name "
            "  RETURN collect(t)[..15] AS touches "
            "} "
            f"RETURN {_node_map('e')} AS e, e.method AS method, e.route AS route, "
            f"CASE WHEN h IS NULL THEN null ELSE {_node_map('h')} END AS h, "
            f"[c IN callees | {_node_map('c')}] AS callees, [t IN touches | {_node_map('t')}] AS touches",
            pid=project_id,
            limit=_clamp(limit, 1, MAX_LIMIT),
        )
        flows: list[EndpointFlow] = []
        for r in rows:
            endpoint = _to_node(r["e"])
            if endpoint is None:
                continue
            flows.append(
                EndpointFlow(
                    endpoint=endpoint,
                    method=r["method"],
                    route=r["route"],
                    handler=_to_node(r["h"]),
                    callees=[n for d in r["callees"] if (n := _to_node(d))],
                    touches=[n for d in r["touches"] if (n := _to_node(d))],
                )
            )
        return flows

    async def overview(self, project_id: str, top_k: int = 15) -> GraphOverview:
        label_rows = await self.neo4j.run(
            "MATCH (n:Entity) WHERE n.project_id = $pid UNWIND labels(n) AS l WITH l WHERE l <> 'Entity' "
            "RETURN l AS label, count(*) AS n ORDER BY label",
            pid=project_id,
        )
        rel_rows = await self.neo4j.run(
            "MATCH (a:Entity)-[r]->(:Entity) WHERE a.project_id = $pid "
            "RETURN type(r) AS type, count(*) AS n ORDER BY type",
            pid=project_id,
        )
        top_rows = await self.neo4j.run(
            "MATCH (n:Entity) WHERE n.project_id = $pid AND NOT n:Project AND NOT n:Library "
            f"WITH n, COUNT {{ (n)-[:{_DEGREE_RELS}]-() }} AS degree WHERE degree > 0 "
            f"RETURN {_node_map('n')} AS n, degree ORDER BY degree DESC, n.uid LIMIT $k",
            pid=project_id,
            k=_clamp(top_k, 1, 100),
        )
        return GraphOverview(
            node_counts={r["label"]: int(r["n"]) for r in label_rows},
            relationship_counts={r["type"]: int(r["n"]) for r in rel_rows if r["type"] in REL_TYPES},
            top_nodes=[
                DegreeNode(node=n, degree=int(r["degree"])) for r in top_rows if (n := _to_node(r["n"]))
            ],
        )

    async def module_dependencies(self, project_id: str, limit: int = 200) -> list[ModuleDependency]:
        """Module → module IMPORTS aggregated from file/symbol-level imports (weight = number of imports)."""
        rows = await self.neo4j.run(
            "MATCH (a:Entity)-[:IMPORTS]->(b:Entity) WHERE a.project_id = $pid AND b.project_id = $pid "
            "WITH CASE WHEN a:Module THEN a.uid ELSE a.module_id END AS sm, "
            "     CASE WHEN b:Module THEN b.uid ELSE b.module_id END AS tm "
            "WHERE sm IS NOT NULL AND tm IS NOT NULL AND sm <> tm "
            "WITH sm, tm, count(*) AS w "
            "MATCH (s:Module {uid: sm}) MATCH (t:Module {uid: tm}) "
            "RETURN s.uid AS su, s.name AS sn, s.path AS sp, t.uid AS tu, t.name AS tn, t.path AS tp, w "
            "ORDER BY w DESC, su, tu LIMIT $limit",
            pid=project_id,
            limit=_clamp(limit, 1, 2000),
        )
        return [
            ModuleDependency(
                source_uid=r["su"],
                source_name=r["sn"],
                source_path=r["sp"],
                target_uid=r["tu"],
                target_name=r["tn"],
                target_path=r["tp"],
                weight=int(r["w"]),
            )
            for r in rows
        ]
