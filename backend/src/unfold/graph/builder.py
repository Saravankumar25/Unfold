"""Neo4j graph builder (LLD §9, ESD §10). IR → `:Entity:<Label>` nodes + provenance-carrying relationships.

Writes are batched `UNWIND … MERGE` (≤1000 rows per transaction), one statement per label / relationship type
because labels and types cannot be parameters — they are validated against the allow-lists in `graph.models`
before being interpolated. Deterministic facts are written with `SET` (idempotent overwrite); inferred facts use
`MERGE … ON CREATE SET` (and only refresh rows that are themselves inferred) so they never overwrite
deterministic ones.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from unfold.core.logging import get_logger
from unfold.graph.models import (
    INFERRED_NODE_LABELS,
    REL_TYPES,
    GraphWriteStats,
    InferredNode,
    InferredRelationship,
    check_label,
    check_rel_type,
)
from unfold.project_ir.schema import Entity, ProjectIR

if TYPE_CHECKING:
    from unfold.persistence.neo4j_store import Neo4jStore

log = get_logger(__name__)

BATCH_SIZE = 1000
FULLTEXT_INDEX = "entity_name_ft"
_MAX_STR = 300

SYMBOL_LABELS: dict[str, str | None] = {
    "class": "Class",
    "interface": "Class",
    "function": "Function",
    "component": "Function",
    "method": "Method",
    "variable": None,  # module-level variables are not graph entities (too fine-grained, AI infra §18)
}

# Extra small scalar attributes copied to nodes when present.
_EXTRA_ATTRS = (
    "language",
    "tier",
    "role",
    "qualname",
    "signature",
    "method",
    "route",
    "framework",
    "technology",
    "category",
    "schema_kind",
    "service_kind",
    "image",
    "resource_kind",
    "provider",
    "component_kind",
    "ecosystem",
    "version",
    "dev",
    "manifest_path",
    "lines",
    "size_bytes",
    "exported",
    "module_id",
    "file_id",
    "parent_id",
    "handler_id",
    "symbol_id",
)


def project_uid(project_id: str) -> str:
    return f"project:{project_id}"


def _batches[T](rows: Sequence[T], size: int = BATCH_SIZE) -> Iterable[Sequence[T]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def _scalar(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[:_MAX_STR]
    if isinstance(value, bool | int | float):
        return value
    if (
        isinstance(value, list)
        and all(isinstance(v, str | int | float | bool) for v in value)
        and len(value) <= 50
    ):
        return value
    return None


def _node_props(entity: Entity, kind: str, project_id: str, analysis_version: str) -> dict[str, Any]:
    props: dict[str, Any] = {
        "project_id": project_id,
        "name": entity.name,
        "kind": kind,
        "path": entity.path,
        "start_line": entity.start_line,
        "end_line": entity.end_line,
        "source": entity.provenance.source,
        "analyzer": entity.provenance.analyzer,
        "confidence": entity.provenance.confidence,
        "analysis_version": analysis_version,
    }
    for attr in _EXTRA_ATTRS:
        value = getattr(entity, attr, None)
        if value is not None:
            scalar = _scalar(value)
            if scalar is not None:
                props[attr] = scalar
    docstring = getattr(entity, "docstring", None)
    if isinstance(docstring, str) and docstring.strip():
        props["doc"] = docstring.strip().splitlines()[0][:200]
    return {k: v for k, v in props.items() if v is not None}


def _ir_nodes(ir: ProjectIR) -> list[tuple[str, str, dict[str, Any]]]:
    """(label, uid, props) for every IR entity that becomes a graph node."""
    pid, ver = ir.project.project_id, ir.analysis_version
    out: list[tuple[str, str, dict[str, Any]]] = []
    file_module = {f.id: f.module_id for f in ir.files}

    def add(label: str, entity: Entity, kind: str, extra: dict[str, Any] | None = None) -> None:
        props = _node_props(entity, kind, pid, ver)
        if extra:
            props.update({k: v for k, v in extra.items() if v is not None})
        out.append((label, entity.id, props))

    for m in ir.modules:
        add("Module", m, "module")
    for f in ir.files:
        add("File", f, "file")
    for s in ir.symbols:
        label = SYMBOL_LABELS.get(s.kind)
        if label:
            add(label, s, s.kind, {"module_id": file_module.get(s.file_id)})
    for e in ir.endpoints:
        add("APIEndpoint", e, "endpoint")
    for sc in ir.schemas:
        add("Schema", sc, "schema")
    for d in ir.data_stores:
        add("DataStore", d, "datastore")
    for sv in ir.services:
        add("Service", sv, "service")
    for q in ir.queues:
        add("Queue", q, "queue")
    for inf in ir.infrastructure:
        add("InfrastructureResource", inf, "infra")
    for ai in ir.ai_components:
        add("AIComponent", ai, "ai")
    for dep in ir.dependencies:
        add("Library", dep, "dependency")
    for fw in ir.frameworks:
        add("Framework", fw, "framework")
    return out


def _derived_relationships(ir: ProjectIR, node_ids: set[str]) -> list[tuple[str, str, str]]:
    """Structural edges implied by IR fields (containment, handler, schema symbol) — (type, source, target)."""
    pid = ir.project.project_id
    out: list[tuple[str, str, str]] = []
    contained: set[str] = {r.target_id for r in ir.relationships if r.type == "CONTAINS"}
    module_paths = {m.id: m.path.strip("/") for m in ir.modules}

    for m in ir.modules:
        for fid in m.file_ids:
            out.append(("CONTAINS", m.id, fid))
            contained.add(fid)
    for f in ir.files:
        if f.module_id and f.module_id in node_ids:
            out.append(("CONTAINS", f.module_id, f.id))
            contained.add(f.id)
    # Nested modules: the nearest ancestor module by path contains the child module.
    by_path = {p: mid for mid, p in module_paths.items()}
    for mid, path in module_paths.items():
        parts = path.split("/") if path else []
        for cut in range(len(parts) - 1, 0, -1):
            parent = by_path.get("/".join(parts[:cut]))
            if parent and parent != mid:
                out.append(("CONTAINS", parent, mid))
                contained.add(mid)
                break
    file_by_path = {f.path: f.id for f in ir.files}
    sym_ids = {s.id for s in ir.symbols}
    for s in ir.symbols:
        parent = s.parent_id if s.parent_id in sym_ids else s.file_id
        if parent:
            out.append(("CONTAINS", parent, s.id))
            contained.add(s.id)
    for e in ir.endpoints:
        if e.handler_id:
            out.append(("HANDLES", e.handler_id, e.id))
        if e.path:
            exposing = file_by_path.get(e.path)
            if exposing:
                out.append(("EXPOSES", exposing, e.id))
    for sc in ir.schemas:
        if sc.symbol_id:
            out.append(("EVIDENCED_BY", sc.id, sc.symbol_id))
    project = project_uid(pid)
    for m in ir.modules:
        if m.id not in contained:
            out.append(("CONTAINS", project, m.id))
    for f in ir.files:
        if f.id not in contained:
            out.append(("CONTAINS", project, f.id))
    return out


@dataclass
class IRGraph:
    """What the IR contributes to the graph, before any store: shared by the Neo4j writer and `graph.memory`."""

    by_label: dict[str, list[dict[str, Any]]]  # label -> [{uid, props}]
    node_ids: set[str]
    det: dict[tuple[str, str, str], dict[str, Any]]  # (type, source, target) -> props
    inferred: dict[tuple[str, str, str], dict[str, Any]]
    skipped: int  # relationships dropped: endpoint missing or type not allow-listed


def ir_graph(ir: ProjectIR) -> IRGraph:
    pid, ver = ir.project.project_id, ir.analysis_version
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_label["Project"].append(
        {
            "uid": project_uid(pid),
            "props": {
                "project_id": pid,
                "name": ir.project.name,
                "kind": "project",
                "source": "deterministic",
                "analyzer": "graph_builder",
                "analysis_version": ver,
                "input_type": ir.project.input_type,
            },
        }
    )
    for label, uid, props in _ir_nodes(ir):
        by_label[label].append({"uid": uid, "props": props})
    node_ids = {row["uid"] for rows in by_label.values() for row in rows}

    skipped = 0
    # Relationships: IR deterministic ones win over derived ones for the same (type, source, target).
    det: dict[tuple[str, str, str], dict[str, Any]] = {}
    inferred: dict[tuple[str, str, str], dict[str, Any]] = {}
    for t, s, d in _derived_relationships(ir, node_ids):
        det.setdefault(
            (t, s, d),
            {
                "source": "deterministic",
                "confidence": 1.0,
                "analyzer": "graph_builder",
                "analysis_version": ver,
                "evidence_ids": [],
            },
        )
    for r in ir.relationships:
        if r.type not in REL_TYPES or r.source_id not in node_ids or r.target_id not in node_ids:
            skipped += 1
            continue
        rprops: dict[str, Any] = {
            "source": r.provenance.source,
            "confidence": r.provenance.confidence,
            "analyzer": r.provenance.analyzer,
            "analysis_version": ver,
            "evidence_ids": list(r.provenance.evidence_ids)[:50],
        }
        for k, v in r.properties.items():
            scalar = _scalar(v)
            if scalar is not None and k not in rprops:
                rprops[k] = scalar
        rkey: tuple[str, str, str] = (r.type, r.source_id, r.target_id)
        (inferred if r.provenance.source == "inferred" else det)[rkey] = rprops

    for key in list(det):
        if key[1] not in node_ids or key[2] not in node_ids:
            del det[key]
            skipped += 1
    return IRGraph(by_label, node_ids, det, inferred, skipped)


class GraphBuilder:
    def __init__(self, neo4j: Neo4jStore) -> None:
        self.neo4j = neo4j

    async def ensure_schema(self) -> None:
        await self.neo4j.run(
            "CREATE CONSTRAINT entity_uid IF NOT EXISTS FOR (n:Entity) REQUIRE n.uid IS UNIQUE"
        )
        await self.neo4j.run("CREATE INDEX entity_project IF NOT EXISTS FOR (n:Entity) ON (n.project_id)")
        await self.neo4j.run("CREATE INDEX entity_name IF NOT EXISTS FOR (n:Entity) ON (n.name)")
        await self.neo4j.run(
            f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS FOR (n:Entity) ON EACH [n.name, n.qualname]"
        )
        await self.neo4j.run("CALL db.awaitIndexes(60)")

    # --- deterministic write --------------------------------------------------------------------------------

    async def write(self, ir: ProjectIR) -> GraphWriteStats:
        pid = ir.project.project_id
        g = ir_graph(ir)
        stats = GraphWriteStats(skipped_relationships=g.skipped)
        det, inferred = g.det, g.inferred

        for label in sorted(g.by_label):
            n = await self._merge_nodes(label, g.by_label[label])
            stats.nodes_by_label[label] = n
            stats.nodes += n

        for group, deterministic in ((det, True), (inferred, False)):
            by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for (t, s, d), props in sorted(group.items()):
                by_type[t].append({"src": s, "tgt": d, "props": props})
            for t in sorted(by_type):
                n = await self._merge_rels(pid, t, by_type[t], deterministic=deterministic)
                stats.relationships_by_type[t] = stats.relationships_by_type.get(t, 0) + n
                stats.relationships += n

        log.info(
            "graph.write",
            project_id=pid,
            nodes=stats.nodes,
            relationships=stats.relationships,
            skipped=stats.skipped_relationships,
        )
        return stats

    async def _merge_nodes(self, label: str, rows: list[dict[str, Any]]) -> int:
        check_label(label)
        cypher = (
            "UNWIND $rows AS row "
            "MERGE (n:Entity {uid: row.uid}) "
            f"SET n:`{label}`, n += row.props "
            "RETURN count(n) AS n"
        )
        total = 0
        for batch in _batches(rows):
            res = await self.neo4j.run(cypher, rows=list(batch))
            total += int(res[0]["n"]) if res else 0
        return total

    async def _merge_rels(
        self, project_id: str, rel_type: str, rows: list[dict[str, Any]], *, deterministic: bool
    ) -> int:
        check_rel_type(rel_type)
        if deterministic:
            write = "MERGE (a)-[r:`{t}`]->(b) SET r += row.props"
        else:
            write = (
                "MERGE (a)-[r:`{t}`]->(b) ON CREATE SET r += row.props "
                "ON MATCH SET r += CASE WHEN r.source = 'inferred' THEN row.props ELSE {{}} END"
            )
        cypher = (
            "UNWIND $rows AS row "
            "MATCH (a:Entity {uid: row.src}) WHERE a.project_id = $pid "
            "MATCH (b:Entity {uid: row.tgt}) WHERE b.project_id = $pid "
            + write.format(t=rel_type)
            + " RETURN count(r) AS n"
        )
        total = 0
        for batch in _batches(rows):
            res = await self.neo4j.run(cypher, rows=list(batch), pid=project_id)
            total += int(res[0]["n"]) if res else 0
        return total

    # --- inferred write (Phase B) -----------------------------------------------------------------------------

    async def write_inferred(
        self, project_id: str, nodes: Sequence[InferredNode], rels: Sequence[InferredRelationship]
    ) -> GraphWriteStats:
        """Subsystem/Concept nodes + PART_OF (etc.) edges. Never overwrites deterministic nodes or edges."""
        stats = GraphWriteStats()
        by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in nodes:
            check_label(node.label, INFERRED_NODE_LABELS)
            props = {k: _scalar(v) for k, v in node.properties.items() if _scalar(v) is not None}
            props.update(
                {
                    "project_id": project_id,
                    "name": node.name,
                    "kind": node.label.lower(),
                    "source": "inferred",
                }
            )
            by_label[node.label].append({"uid": node.uid, "props": props})
        for label in sorted(by_label):
            cypher = (
                "UNWIND $rows AS row "
                "MERGE (n:Entity {uid: row.uid}) "
                f"ON CREATE SET n:`{label}`, n += row.props "
                "ON MATCH SET n += CASE WHEN n.source = 'inferred' AND n.project_id = row.props.project_id "
                "THEN row.props ELSE {} END "
                "RETURN count(n) AS n"
            )
            n = 0
            for batch in _batches(by_label[label]):
                res = await self.neo4j.run(cypher, rows=list(batch))
                n += int(res[0]["n"]) if res else 0
            stats.nodes_by_label[label] = n
            stats.nodes += n

        by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for rel in rels:
            check_rel_type(rel.type)
            by_type[rel.type].append(
                {
                    "src": rel.source_uid,
                    "tgt": rel.target_uid,
                    "props": {
                        "source": "inferred",
                        "confidence": rel.confidence,
                        "analyzer": rel.analyzer,
                        "analysis_version": "inferred",
                        "evidence_ids": list(rel.evidence_ids)[:50],
                    },
                }
            )
        for t in sorted(by_type):
            n = await self._merge_rels(project_id, t, by_type[t], deterministic=False)
            stats.relationships_by_type[t] = n
            stats.relationships += n
        return stats

    async def set_subsystem(self, project_id: str, mapping: dict[str, str]) -> int:
        """Set `subsystem_id` on entity nodes (entity uid → subsystem id)."""
        rows = [{"uid": uid, "sid": sid} for uid, sid in sorted(mapping.items())]
        cypher = (
            "UNWIND $rows AS row MATCH (n:Entity {uid: row.uid}) WHERE n.project_id = $pid "
            "SET n.subsystem_id = row.sid RETURN count(n) AS n"
        )
        total = 0
        for batch in _batches(rows):
            res = await self.neo4j.run(cypher, rows=list(batch), pid=project_id)
            total += int(res[0]["n"]) if res else 0
        return total

    async def delete_project(self, project_id: str) -> int:
        total = 0
        while True:
            res = await self.neo4j.run(
                "MATCH (n:Entity {project_id: $pid}) WITH n LIMIT $limit DETACH DELETE n RETURN count(*) AS n",
                pid=project_id,
                limit=BATCH_SIZE,
            )
            n = int(res[0]["n"]) if res else 0
            total += n
            if n == 0:
                return total
