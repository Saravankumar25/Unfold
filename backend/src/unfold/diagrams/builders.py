"""Deterministic DiagramSpec builders (HLD D8, ESD §22, LLD §17).

Every builder reads the Project IR (and, for sequences, a `WorkflowFinding`) and returns a `DiagramSpec`, or `None`
when the repository gives too little signal for that diagram to teach anything. No LLM is involved: facts come from
the IR, and anything the builder had to guess (pipeline ordering, actors not found in the IR, type-name relations)
is marked `inferred=True` so the web and PDF render it dashed.

Iteration is always over sorted/declared order so the same IR yields byte-identical specs (stable diagram ids come
from `core.ids.diagram_id(project_id, kind, key)`).
"""

from __future__ import annotations

import itertools
import re
from collections import Counter, defaultdict, deque
from collections.abc import Iterable
from pathlib import PurePosixPath

from unfold.agents.models import WorkflowFinding
from unfold.core.ids import diagram_id
from unfold.diagrams.inputs import SubsystemView
from unfold.diagrams.models import MAX_NODES, DiagramEdge, DiagramGroup, DiagramNode, DiagramSpec
from unfold.diagrams.validate import validate_spec
from unfold.project_ir.schema import (
    AIComponentIR,
    EndpointIR,
    Entity,
    FileIR,
    ModuleIR,
    ProjectIR,
    RelationshipIR,
    SchemaIR,
    ServiceIR,
    SymbolIR,
)

__all__ = [
    "ai_pipeline",
    "architecture",
    "build_all",
    "data_model",
    "deployment",
    "module_dependencies",
    "request_flow",
    "workflow_sequence",
]

_VERBS = {
    "USES": "uses",
    "DEPENDS_ON": "depends on",
    "READS_FROM": "reads",
    "WRITES_TO": "writes",
    "PUBLISHES_TO": "publishes",
    "CONSUMES_FROM": "consumes",
    "CALLS": "calls",
    "IMPORTS": "imports",
    "AUTHENTICATES_WITH": "authenticates with",
    "DEPLOYS_TO": "deploys to",
    "CONFIGURES": "configures",
    "HANDLES": "handles",
    "EXPOSES": "exposes",
}
_ARCH_TYPES = frozenset(
    {
        "USES",
        "DEPENDS_ON",
        "READS_FROM",
        "WRITES_TO",
        "PUBLISHES_TO",
        "CONSUMES_FROM",
        "CALLS",
        "IMPORTS",
        "AUTHENTICATES_WITH",
    }
)
_DATA_TYPES = frozenset({"READS_FROM", "WRITES_TO", "USES", "PUBLISHES_TO", "CONSUMES_FROM"})
_DATA_KINDS = frozenset({"datastore", "schema", "queue", "ai"})
_FRONTEND_EXT = frozenset(
    {".tsx", ".jsx", ".vue", ".svelte", ".html", ".css", ".scss", ".sass", ".less", ".dart", ".swift", ".kt"}
)
_FRONTEND_DIRS = frozenset({"frontend", "client", "web", "ui", "mobile", "webapp", "www", "public", "static"})
_HOSTED_AI = (
    "openai",
    "anthropic",
    "claude",
    "gemini",
    "google",
    "groq",
    "cohere",
    "mistral",
    "voyage",
    "bedrock",
    "azure",
    "together",
    "replicate",
    "huggingface_hub",
    "perplexity",
    "deepseek",
)
_DATA_SERVICE_KINDS = frozenset({"database", "cache"})
_HUMAN_ACTORS = frozenset(
    {
        "user",
        "users",
        "client",
        "browser",
        "customer",
        "admin",
        "developer",
        "operator",
        "visitor",
        "enduser",
        "caller",
        "human",
    }
)

_PIPELINE_STAGES: list[tuple[str, str]] = [
    ("ingest", "Ingest"),
    ("chunk", "Chunk"),
    ("embed", "Embed"),
    ("index", "Index"),
    ("retrieve", "Retrieve"),
    ("rerank", "Rerank"),
    ("training", "Train"),
    ("agent", "Agent"),
    ("generate", "Generate"),
]
_STAGE_BY_KIND = {
    "dataset": "ingest",
    "loader": "ingest",
    "embedding": "embed",
    "vector_search": "retrieve",
    "reranker": "rerank",
    "llm_client": "generate",
    "inference": "generate",
    "agent": "agent",
    "training": "training",
}
_STAGE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("rerank", ("rerank", "cross_encoder", "crossencoder")),
    ("chunk", ("chunk", "splitter", "split_text")),
    ("embed", ("embed", "encoder")),
    ("index", ("index", "upsert", "vectorstore", "vector_store")),
    ("retrieve", ("retriev", "search", "query")),
    ("ingest", ("ingest", "loader", "dataset", "reader", "crawl", "scrape")),
    ("training", ("train", "finetune", "fine_tune")),
    ("agent", ("agent", "graph", "tool")),
    ("generate", ("generat", "llm", "completion", "chat", "answer", "infer")),
]


# ---- shared helpers ----------------------------------------------------------------------------------------


class _Index:
    """Lookups over one ProjectIR, built once per builder call."""

    def __init__(self, ir: ProjectIR) -> None:
        self.ir = ir
        self.entities: dict[str, Entity] = ir.entity_index()
        self.files: dict[str, FileIR] = {f.id: f for f in ir.files}
        self.file_by_path: dict[str, FileIR] = {f.path: f for f in ir.files}
        self.symbols: dict[str, SymbolIR] = {s.id: s for s in ir.symbols}
        self.modules: dict[str, ModuleIR] = {m.id: m for m in ir.modules}
        self.module_of_file: dict[str, str] = {}
        for m in sorted(ir.modules, key=lambda m: m.id):
            for fid in m.file_ids:
                self.module_of_file.setdefault(fid, m.id)
        for f in ir.files:
            if f.module_id and f.module_id in self.modules:
                self.module_of_file[f.id] = f.module_id

    def kind(self, entity_id: str) -> str | None:
        e = self.entities.get(entity_id)
        return None if e is None else str(getattr(e, "kind", "entity"))

    def file_of(self, entity_id: str) -> str | None:
        """The file an entity lives in (files map to themselves)."""
        if entity_id in self.files:
            return entity_id
        sym = self.symbols.get(entity_id)
        if sym is not None:
            return sym.file_id
        e = self.entities.get(entity_id)
        if e is None:
            return None
        if isinstance(e, EndpointIR) and e.handler_id and e.handler_id in self.symbols:
            return self.symbols[e.handler_id].file_id
        if isinstance(e, SchemaIR) and e.symbol_id and e.symbol_id in self.symbols:
            return self.symbols[e.symbol_id].file_id
        if isinstance(e, ServiceIR) and e.entrypoint_file_id in self.files:
            return e.entrypoint_file_id
        if e.path and e.path in self.file_by_path:
            return self.file_by_path[e.path].id
        return None

    def module_of(self, entity_id: str) -> str | None:
        if entity_id in self.modules:
            return entity_id
        fid = self.file_of(entity_id)
        return None if fid is None else self.module_of_file.get(fid)

    def location(self, entity_id: str) -> str | None:
        e = self.entities.get(entity_id)
        if e is None:
            return None
        path = e.path
        if path is None:
            fid = self.file_of(entity_id)
            path = self.files[fid].path if fid else None
        if path is None:
            return None
        return f"{path}:{e.start_line}" if e.start_line else path


def _verb(rel_type: str) -> str:
    return _VERBS.get(rel_type, rel_type.lower().replace("_", " "))


def _agg_label(types: Counter[str]) -> str:
    ordered = sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))
    names = [t for t, _ in ordered]
    if "READS_FROM" in names and "WRITES_TO" in names:
        names = ["READS_WRITES", *[n for n in names if n not in ("READS_FROM", "WRITES_TO")]]
    verbs = ["reads/writes" if n == "READS_WRITES" else _verb(n) for n in names[:2]]
    return ", ".join(verbs)


class _EdgeAgg:
    """Aggregates many IR relationships into one diagram edge per (source, target)."""

    def __init__(self) -> None:
        self._types: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        self._all_inferred: dict[tuple[str, str], bool] = {}
        self._order: list[tuple[str, str]] = []

    def add(self, source: str, target: str, rel_type: str, inferred: bool) -> None:
        if source == target:
            return
        key = (source, target)
        if key not in self._types:
            self._order.append(key)
            self._all_inferred[key] = inferred
        else:
            self._all_inferred[key] = self._all_inferred[key] and inferred
        self._types[key][rel_type] += 1

    def edges(self) -> list[DiagramEdge]:
        out: list[DiagramEdge] = []
        for key in sorted(self._order):
            types = self._types[key]
            dominant = sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            out.append(
                DiagramEdge(
                    source=key[0],
                    target=key[1],
                    label=_agg_label(types),
                    kind=dominant,
                    inferred=self._all_inferred[key],
                )
            )
        return out


def _inferred(rel: RelationshipIR) -> bool:
    return rel.provenance.source == "inferred"


def _norm(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", name.lower())


def _spec(ir: ProjectIR, project_id: str | None, kind: str, key: str, **fields: object) -> DiagramSpec:
    pid = project_id or ir.project.project_id
    return DiagramSpec.model_validate({"id": diagram_id(pid, kind, key), "kind": kind, **fields})


def _groups(nodes: Iterable[DiagramNode], labels: dict[str, str]) -> list[DiagramGroup]:
    used = {n.group for n in nodes if n.group}
    return [DiagramGroup(id=g, label=label) for g, label in labels.items() if g in used]


# ---- architecture ------------------------------------------------------------------------------------------

_TIER_LABELS = {
    "client": "Client",
    "backend": "Backend",
    "ai": "AI components",
    "data": "Data",
    "external": "External services",
}


def _is_frontend_path(path: str) -> bool:
    p = PurePosixPath(path)
    return p.suffix.lower() in _FRONTEND_EXT or any(part.lower() in _FRONTEND_DIRS for part in p.parts[:-1])


def architecture(
    ir: ProjectIR, subsystems: list[SubsystemView], *, project_id: str | None = None
) -> DiagramSpec | None:
    idx = _Index(ir)
    nodes: list[DiagramNode] = []
    owner: dict[str, str] = {}  # entity id -> diagram node id

    path_owner: dict[str, str] = {}
    for sub in sorted(subsystems, key=lambda s: s.key):
        node_id = f"subsystem:{sub.key}"
        paths = sorted(set(sub.member_file_paths))
        frontend = sum(1 for p in paths if _is_frontend_path(p))
        tier = "client" if paths and frontend * 2 > len(paths) else "backend"
        nodes.append(
            DiagramNode(
                id=node_id, label=sub.title, kind="subsystem", group=tier, detail=f"{len(paths)} files"
            )
        )
        for p in paths:
            path_owner.setdefault(p, node_id)

    store_names = {_norm(d.technology) for d in ir.data_stores} | {_norm(d.name) for d in ir.data_stores}
    for svc in sorted(ir.services, key=lambda s: s.id):
        is_data = svc.service_kind in _DATA_SERVICE_KINDS
        if is_data and any(s and (s in _norm(svc.name) or s in _norm(svc.image or "")) for s in store_names):
            continue  # already represented by a data store node
        detail = svc.image or (
            idx.files[svc.entrypoint_file_id].path if svc.entrypoint_file_id in idx.files else None
        )
        nodes.append(
            DiagramNode(
                id=svc.id,
                label=svc.name,
                kind="datastore" if is_data else "service",
                group="data" if is_data else "backend",
                ref_uid=svc.id,
                detail=detail,
            )
        )
        owner[svc.id] = svc.id
    for ds in sorted(ir.data_stores, key=lambda d: d.id):
        nodes.append(
            DiagramNode(
                id=ds.id,
                label=ds.name,
                kind="datastore",
                group="data",
                ref_uid=ds.id,
                detail=f"{ds.technology} ({ds.category})",
                inferred=ds.provenance.source == "inferred",
            )
        )
        owner[ds.id] = ds.id
    for q in sorted(ir.queues, key=lambda q: q.id):
        nodes.append(
            DiagramNode(
                id=q.id,
                label=q.name,
                kind="queue",
                group="data",
                ref_uid=q.id,
                detail=q.technology,
                inferred=q.provenance.source == "inferred",
            )
        )
        owner[q.id] = q.id
    for ai in sorted(ir.ai_components, key=lambda a: a.id):
        tech = (ai.technology or "").lower()
        hosted = bool(tech) and any(h in tech for h in _HOSTED_AI)
        nodes.append(
            DiagramNode(
                id=ai.id,
                label=ai.name,
                kind="external" if hosted else "ai",
                group="external" if hosted else "ai",
                ref_uid=ai.id,
                detail=" · ".join(x for x in (ai.component_kind, ai.technology) if x),
                inferred=ai.provenance.source == "inferred",
            )
        )
        owner[ai.id] = ai.id

    def resolve(entity_id: str) -> str | None:
        if entity_id in owner:
            return owner[entity_id]
        mod = idx.modules.get(entity_id)
        if mod is not None:
            votes = Counter(
                path_owner[idx.files[f].path]
                for f in mod.file_ids
                if f in idx.files and idx.files[f].path in path_owner
            )
            return sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if votes else None
        fid = idx.file_of(entity_id)
        if fid is not None:
            path = idx.files[fid].path
            if path in path_owner:
                return path_owner[path]
            for svc in ir.services:
                if svc.entrypoint_file_id == fid and svc.id in owner:
                    return owner[svc.id]
        return None

    agg = _EdgeAgg()
    for rel in ir.relationships:
        if rel.type not in _ARCH_TYPES:
            continue
        s, t = resolve(rel.source_id), resolve(rel.target_id)
        if s is not None and t is not None:
            agg.add(s, t, rel.type, _inferred(rel))
    svc_by_name = {s.name: s.id for s in ir.services}
    for svc in ir.services:
        for dep in svc.depends_on:
            target = svc_by_name.get(dep, dep)
            s, t = owner.get(svc.id), owner.get(target)
            if s is not None and t is not None:
                agg.add(s, t, "DEPENDS_ON", svc.provenance.source == "inferred")

    edges = agg.edges()
    if len(nodes) < 2 or (not edges and len(nodes) < 3):
        return None
    return _spec(
        ir,
        project_id,
        "architecture",
        "system",
        title=f"{ir.project.name} architecture",
        description="Subsystems, services, data stores and external services, with relationships from the "
        "code aggregated to that level.",
        nodes=nodes,
        edges=edges,
        groups=_groups(nodes, _TIER_LABELS),
    )


# ---- module dependencies -----------------------------------------------------------------------------------


def module_dependencies(ir: ProjectIR, *, project_id: str | None = None) -> DiagramSpec | None:
    idx = _Index(ir)
    weights: Counter[tuple[str, str]] = Counter()
    inferred_only: dict[tuple[str, str], bool] = {}
    for rel in ir.relationships:
        if rel.type != "IMPORTS":
            continue
        s, t = idx.module_of(rel.source_id), idx.module_of(rel.target_id)
        if s is None or t is None or s == t:
            continue
        weights[(s, t)] += 1
        inferred_only[(s, t)] = inferred_only.get((s, t), True) and _inferred(rel)
    if not weights:
        return None
    neighbours: dict[str, set[str]] = defaultdict(set)
    for s, t in weights:
        neighbours[s].add(t)
        neighbours[t].add(s)

    def mod_path(mid: str) -> str:
        m = idx.modules[mid]
        return m.path or m.name

    ranked = sorted(neighbours, key=lambda m: (-len(neighbours[m]), mod_path(m), m))[:MAX_NODES]
    keep = set(ranked)
    tops = {mid: (PurePosixPath(mod_path(mid)).parts or ("(root)",))[0] for mid in ranked}
    use_groups = len(set(tops.values())) > 1
    nodes = [
        DiagramNode(
            id=mid,
            label=mod_path(mid),
            kind="module",
            group=f"dir:{tops[mid]}" if use_groups else None,
            ref_uid=mid,
            detail=f"{len(idx.modules[mid].file_ids)} files",
        )
        for mid in sorted(ranked, key=lambda m: (mod_path(m), m))
    ]
    edges = [
        DiagramEdge(
            source=s,
            target=t,
            label=f"{n} imports" if n > 1 else None,
            kind="IMPORTS",
            inferred=inferred_only[(s, t)],
        )
        for (s, t), n in sorted(weights.items())
        if s in keep and t in keep
    ]
    if len(nodes) < 2 or not edges:
        return None
    group_labels = {f"dir:{top}": f"{top}/" for top in sorted(set(tops.values()))}
    return _spec(
        ir,
        project_id,
        "module_dependencies",
        "modules",
        title="Module dependencies",
        description=f"Import relationships between the {len(nodes)} most connected modules.",
        nodes=nodes,
        edges=edges,
        groups=_groups(nodes, group_labels),
    )


# ---- data model --------------------------------------------------------------------------------------------


def _field_line(field: dict[str, object]) -> str:
    name = str(field.get("name", "?"))
    ftype = field.get("type")
    return f"{name}: {ftype}" if ftype else name


def _relation_target(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("target", "model", "to", "schema", "table"):
            v = value.get(key)
            if isinstance(v, str):
                return v
    return None


def data_model(ir: ProjectIR, *, project_id: str | None = None) -> DiagramSpec | None:
    schemas = sorted(ir.schemas, key=lambda s: (s.path or "", s.name, s.id))
    if not schemas or (len(schemas) == 1 and not schemas[0].fields):
        return None
    by_name: dict[str, str] = {}
    by_norm: dict[str, str] = {}
    for s in schemas:
        by_name.setdefault(s.name, s.id)
        by_norm.setdefault(_norm(s.name), s.id)
    kinds = sorted({s.schema_kind for s in schemas})
    use_groups = len(kinds) > 1
    nodes: list[DiagramNode] = []
    for s in schemas:
        lines = [_field_line(f) for f in s.fields[:8]]
        if len(s.fields) > 8:
            lines.append(f"… +{len(s.fields) - 8} more")
        nodes.append(
            DiagramNode(
                id=s.id,
                label=s.name,
                kind="schema",
                group=s.schema_kind if use_groups else None,
                ref_uid=s.id,
                detail="\n".join(lines) or None,
                inferred=s.provenance.source == "inferred",
            )
        )
    edges: list[DiagramEdge] = []
    seen: set[tuple[str, str, str]] = set()

    def add(src: str, tgt: str, label: str, kind: str, inferred: bool) -> None:
        key = (src, tgt, label)
        if src != tgt and key not in seen:
            seen.add(key)
            edges.append(DiagramEdge(source=src, target=tgt, label=label, kind=kind, inferred=inferred))

    for s in schemas:
        for f in s.fields:
            fname = str(f.get("name", ""))
            explicit = _relation_target(f.get("relation"))
            if explicit is not None:
                target = by_name.get(explicit) or by_norm.get(_norm(explicit))
                if target is not None:
                    add(s.id, target, fname, "relation", False)
                    continue
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(f.get("type") or "")):
                target = by_name.get(token)
                if target is not None and target != s.id:
                    add(s.id, target, fname, "type_reference", True)
                    break
    ids = {s.id for s in schemas}
    for rel in sorted(ir.relationships, key=lambda r: (r.source_id, r.target_id, r.type)):
        if (
            rel.source_id in ids
            and rel.target_id in ids
            and rel.type not in ("CONTAINS", "PART_OF", "EVIDENCED_BY")
        ):
            add(rel.source_id, rel.target_id, _verb(rel.type), rel.type, _inferred(rel))
    return _spec(
        ir,
        project_id,
        "data_model",
        "schemas",
        title="Data model",
        description="Schemas with their key fields; edges follow declared relations and field types.",
        nodes=nodes,
        edges=edges,
        groups=_groups(nodes, {k: k.replace("_", " ") for k in kinds}),
    )


# ---- request flow ------------------------------------------------------------------------------------------

_FLOW_DEPTH = 3
_FLOW_FANOUT = 6
_FLOW_MAX_SYMBOLS = 24


def _handler_of(ep: EndpointIR, idx: _Index, handles: dict[str, str]) -> str | None:
    if ep.handler_id and ep.handler_id in idx.symbols:
        return ep.handler_id
    return handles.get(ep.id)


def request_flow(ir: ProjectIR, *, project_id: str | None = None) -> DiagramSpec | None:
    idx = _Index(ir)
    if not ir.endpoints:
        return None
    calls: dict[str, list[str]] = defaultdict(list)
    call_inferred: dict[tuple[str, str], bool] = {}
    handles: dict[str, str] = {}
    data_links: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)
    for rel in sorted(ir.relationships, key=lambda r: (r.source_id, r.target_id, r.type)):
        if rel.type == "CALLS" and rel.source_id in idx.symbols and rel.target_id in idx.symbols:
            if rel.target_id not in calls[rel.source_id]:
                calls[rel.source_id].append(rel.target_id)
            call_inferred[(rel.source_id, rel.target_id)] = _inferred(rel)
        elif rel.type in ("HANDLES", "EXPOSES"):
            a, b = rel.source_id, rel.target_id
            if idx.kind(a) == "endpoint" and b in idx.symbols:
                handles.setdefault(a, b)
            elif idx.kind(b) == "endpoint" and a in idx.symbols:
                handles.setdefault(b, a)
        elif rel.type in _DATA_TYPES and idx.kind(rel.target_id) in _DATA_KINDS:
            data_links[rel.source_id].append((rel.target_id, rel.type, _inferred(rel)))

    def data_of(sym_id: str) -> list[tuple[str, str, bool]]:
        out = list(data_links.get(sym_id, []))
        fid = idx.symbols[sym_id].file_id
        out += [(t, ty, True) for t, ty, _ in data_links.get(fid, [])]
        return out

    def walk(handler: str) -> tuple[list[tuple[str, int]], list[tuple[str, str]]]:
        order: list[tuple[str, int]] = [(handler, 0)]
        seen = {handler}
        call_edges: list[tuple[str, str]] = []
        queue: deque[tuple[str, int]] = deque([(handler, 0)])
        while queue:
            sym, depth = queue.popleft()
            if depth >= _FLOW_DEPTH:
                continue
            for callee in sorted(calls.get(sym, []), key=lambda c: (idx.symbols[c].qualname, c))[
                :_FLOW_FANOUT
            ]:
                if callee not in seen:
                    if len(seen) >= _FLOW_MAX_SYMBOLS:
                        continue
                    seen.add(callee)
                    order.append((callee, depth + 1))
                    queue.append((callee, depth + 1))
                call_edges.append((sym, callee))
        return order, call_edges

    scored: list[tuple[int, str, EndpointIR, str]] = []
    for ep in ir.endpoints:
        handler = _handler_of(ep, idx, handles)
        if handler is None:
            continue
        order, _ = walk(handler)
        data_targets = {t for sym, _ in order for t, _, _ in data_of(sym)}
        scored.append((len(order) + 2 * len(data_targets), f"{ep.route} {ep.method}", ep, handler))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], x[1], x[2].id))
    top_score = scored[0][0]
    chosen = [scored[0]] + [s for s in scored[1:2] if s[0] > 1 and s[0] * 4 >= top_score * 3]

    nodes: dict[str, DiagramNode] = {}
    edges: list[DiagramEdge] = []
    nodes["actor:client"] = DiagramNode(id="actor:client", label="Client", kind="actor", group="api")
    for _, _, ep, handler in chosen:
        nodes[ep.id] = DiagramNode(
            id=ep.id,
            label=f"{ep.method} {ep.route}",
            kind="endpoint",
            group="api",
            ref_uid=ep.id,
            detail=ep.framework or idx.location(ep.id),
        )
        edges.append(DiagramEdge(source="actor:client", target=ep.id, label="request", kind="request"))
        order, call_edges = walk(handler)
        for sym_id, depth in order:
            sym = idx.symbols[sym_id]
            nodes.setdefault(
                sym_id,
                DiagramNode(
                    id=sym_id,
                    label=sym.qualname,
                    kind="handler" if depth == 0 else sym.kind,
                    group="logic",
                    ref_uid=sym_id,
                    detail=idx.location(sym_id),
                    inferred=sym.provenance.source == "inferred",
                ),
            )
        edges.append(DiagramEdge(source=ep.id, target=handler, label="handled by", kind="HANDLES"))
        visited = {s for s, _ in order}
        for a, b in call_edges:
            if a in visited and b in visited:
                edges.append(
                    DiagramEdge(
                        source=a,
                        target=b,
                        label="calls",
                        kind="CALLS",
                        inferred=call_inferred.get((a, b), False),
                    )
                )
        for sym_id, _ in order:
            for target, rel_type, inferred in data_of(sym_id):
                ent = idx.entities[target]
                nodes.setdefault(
                    target,
                    DiagramNode(
                        id=target,
                        label=ent.name,
                        kind=idx.kind(target) or "component",
                        group="data",
                        ref_uid=target,
                        detail=idx.location(target),
                        inferred=ent.provenance.source == "inferred",
                    ),
                )
                edges.append(
                    DiagramEdge(
                        source=sym_id, target=target, label=_verb(rel_type), kind=rel_type, inferred=inferred
                    )
                )
    node_list = list(nodes.values())
    names = ", ".join(f"{c[2].method} {c[2].route}" for c in chosen)
    return _spec(
        ir,
        project_id,
        "request_flow",
        "main",
        title="Request flow",
        description=f"How {names} is handled: handler, the functions it calls (up to {_FLOW_DEPTH} levels) and "
        "the data it touches.",
        nodes=node_list,
        edges=edges,
        groups=_groups(node_list, {"api": "API", "logic": "Application logic", "data": "Data"}),
    )


# ---- deployment --------------------------------------------------------------------------------------------

_DEPLOY_INFRA_PREFIXES = ("k8s", "kubernetes", "terraform", "helm", "cloud", "serverless", "ecs", "lambda")


def deployment(ir: ProjectIR, *, project_id: str | None = None) -> DiagramSpec | None:
    idx = _Index(ir)
    nodes: list[DiagramNode] = []
    node_ids: set[str] = set()
    ported: list[ServiceIR] = []
    for svc in sorted(ir.services, key=lambda s: (s.name, s.id)):
        tier = (
            "data"
            if svc.service_kind in _DATA_SERVICE_KINDS
            else "edge"
            if svc.service_kind == "proxy"
            else "app"
        )
        parts = [
            p
            for p in (
                f"image {svc.image}" if svc.image else None,
                f"ports {', '.join(svc.ports)}" if svc.ports else None,
            )
            if p
        ]
        nodes.append(
            DiagramNode(
                id=svc.id,
                label=svc.name,
                kind="datastore" if tier == "data" else "service",
                group=tier,
                ref_uid=svc.id,
                detail=" · ".join(parts) or svc.service_kind,
                inferred=svc.provenance.source == "inferred",
            )
        )
        node_ids.add(svc.id)
        if svc.ports:
            ported.append(svc)
    for res in sorted(ir.infrastructure, key=lambda r: (r.resource_kind, r.name, r.id)):
        if not res.resource_kind.lower().startswith(_DEPLOY_INFRA_PREFIXES):
            continue
        group = f"infra:{res.provider or 'other'}"
        nodes.append(
            DiagramNode(
                id=res.id,
                label=res.name,
                kind="infra",
                group=group,
                ref_uid=res.id,
                detail=res.resource_kind,
                inferred=res.provenance.source == "inferred",
            )
        )
        node_ids.add(res.id)
    if len(nodes) < 2 and not ported:
        return None

    agg = _EdgeAgg()
    by_name = {s.name: s.id for s in ir.services}
    for svc in ir.services:
        for dep in svc.depends_on:
            target = by_name.get(dep, dep)
            if target in node_ids:
                agg.add(svc.id, target, "DEPENDS_ON", svc.provenance.source == "inferred")
    extra: dict[str, DiagramNode] = {}
    for rel in ir.relationships:
        if rel.type not in ("DEPENDS_ON", "DEPLOYS_TO", "CONFIGURES", "USES", "READS_FROM", "WRITES_TO"):
            continue
        s, t = rel.source_id, rel.target_id
        if s in node_ids and t in node_ids:
            agg.add(s, t, rel.type, _inferred(rel))
        elif s in node_ids and idx.kind(t) == "datastore":
            ds = idx.entities[t]
            extra.setdefault(
                t,
                DiagramNode(
                    id=t,
                    label=ds.name,
                    kind="datastore",
                    group="data",
                    ref_uid=t,
                    detail=getattr(ds, "technology", None),
                ),
            )
            agg.add(s, t, rel.type, _inferred(rel))
    nodes += [extra[k] for k in sorted(extra)]
    edges = agg.edges()
    if ported:
        nodes.insert(
            0, DiagramNode(id="actor:host", label="Users / host network", kind="actor", group="edge")
        )
        for svc in ported:
            edges.append(
                DiagramEdge(source="actor:host", target=svc.id, label=", ".join(svc.ports[:4]), kind="port")
            )
    if len(nodes) < 2:
        return None
    labels = {"edge": "Entry", "app": "Application services", "data": "Data services"}
    labels |= {
        n.group: f"{n.group.split(':', 1)[1]} resources"
        for n in nodes
        if n.group and n.group.startswith("infra:")
    }
    return _spec(
        ir,
        project_id,
        "deployment",
        "runtime",
        title="Deployment topology",
        description="Runnable services and infrastructure resources from compose/Kubernetes/Terraform, with "
        "published ports and declared dependencies.",
        nodes=nodes,
        edges=edges,
        groups=_groups(nodes, labels),
    )


# ---- AI pipeline -------------------------------------------------------------------------------------------


def _stage_of(ai: AIComponentIR) -> str | None:
    text = _norm_words(f"{ai.name} {ai.path or ''} {ai.technology or ''}")
    if ai.component_kind == "vector_search" and any(k in text for k in ("index", "upsert", "ingest")):
        return "index"
    if ai.component_kind in _STAGE_BY_KIND:
        return _STAGE_BY_KIND[ai.component_kind]
    for stage, keywords in _STAGE_KEYWORDS:
        if any(k in text for k in keywords):
            return stage
    return None


def _norm_words(text: str) -> str:
    return re.sub(r"[^0-9a-z_]+", " ", text.lower())


def ai_pipeline(ir: ProjectIR, *, project_id: str | None = None) -> DiagramSpec | None:
    if not ir.ai_components:
        return None
    idx = _Index(ir)
    stage_rank = {s: i for i, (s, _) in enumerate(_PIPELINE_STAGES)}
    comps = sorted(
        ir.ai_components, key=lambda a: (stage_rank.get(_stage_of(a) or "", 99), a.path or "", a.name, a.id)
    )
    nodes: list[DiagramNode] = []
    ai_ids = {a.id for a in comps}
    by_stage: dict[str, list[str]] = defaultdict(list)
    for a in comps:
        stage = _stage_of(a)
        if stage is not None:
            by_stage[stage].append(a.id)
        nodes.append(
            DiagramNode(
                id=a.id,
                label=a.name,
                kind="ai",
                group=f"stage:{stage}" if stage else "stage:other",
                ref_uid=a.id,
                detail=" · ".join(x for x in (a.component_kind, a.technology, idx.location(a.id)) if x),
                inferred=a.provenance.source == "inferred",
            )
        )

    agg = _EdgeAgg()
    users: dict[str, DiagramNode] = {}
    for rel in ir.relationships:
        if rel.type in ("CONTAINS", "PART_OF", "EVIDENCED_BY"):
            continue
        s, t = rel.source_id, rel.target_id
        if s in ai_ids and t in ai_ids:
            agg.add(s, t, rel.type, _inferred(rel))
            continue
        if t not in ai_ids:
            continue
        user = idx.module_of(s) or idx.file_of(s)
        if user is None:
            continue
        if user not in users:
            ent = idx.entities[user]
            label = ent.path or ent.name
            users[user] = DiagramNode(
                id=user,
                label=label,
                kind="module" if user in idx.modules else "file",
                group="code",
                ref_uid=user,
            )
        agg.add(user, t, rel.type, _inferred(rel))

    edges = agg.edges()
    present = [s for s, _ in _PIPELINE_STAGES if by_stage.get(s)]
    ordered = len(present) >= 2
    if ordered:
        for prev_stage, next_stage in itertools.pairwise(present):
            edges.append(
                DiagramEdge(
                    source=by_stage[prev_stage][-1],
                    target=by_stage[next_stage][0],
                    label="then",
                    kind="pipeline",
                    inferred=True,
                )
            )
    user_nodes = [users[k] for k in sorted(users, key=lambda k: (users[k].label, k))]
    all_nodes = user_nodes + nodes
    if len(all_nodes) < 2 and not edges:
        return None
    labels = {"code": "Code using AI components"}
    labels |= {f"stage:{s}": f"{i + 1} · {label}" for i, (s, label) in enumerate(_PIPELINE_STAGES)}
    labels["stage:other"] = "Other AI components"
    how = (
        "ordered by pipeline stage (ingest → chunk → embed → index → retrieve → rerank → generate)"
        if ordered
        else "linked by the relationships found in the code"
    )
    return _spec(
        ir,
        project_id,
        "ai_pipeline",
        "pipeline",
        title="AI pipeline",
        description=f"AI components and the code that uses them, {how}.",
        nodes=all_nodes,
        edges=edges,
        groups=_groups(all_nodes, labels),
        annotations=["Stage order is inferred from component kinds and names."] if ordered else [],
    )


# ---- workflow sequence -------------------------------------------------------------------------------------


def _name_index(ir: ProjectIR) -> dict[str, tuple[str, str]]:
    """normalized name -> (entity id, diagram node kind). Earlier categories win."""
    out: dict[str, tuple[str, str]] = {}

    def put(name: str | None, eid: str, kind: str) -> None:
        key = _norm(name or "")
        if key:
            out.setdefault(key, (eid, kind))

    for svc in sorted(ir.services, key=lambda x: x.id):
        put(svc.name, svc.id, "datastore" if svc.service_kind in _DATA_SERVICE_KINDS else "service")
    for ds in sorted(ir.data_stores, key=lambda x: x.id):
        put(ds.name, ds.id, "datastore")
        put(ds.technology, ds.id, "datastore")
    for q in sorted(ir.queues, key=lambda x: x.id):
        put(q.name, q.id, "queue")
    for ai in sorted(ir.ai_components, key=lambda x: x.id):
        put(ai.name, ai.id, "ai")
    for m in sorted(ir.modules, key=lambda x: x.id):
        put(m.name, m.id, "module")
        put(m.path, m.id, "module")
        put(PurePosixPath(m.path).name, m.id, "module")
    for sym in sorted(ir.symbols, key=lambda x: (x.kind != "class", x.id)):
        put(sym.qualname, sym.id, "component")
        put(sym.name, sym.id, "component")
    for ep in sorted(ir.endpoints, key=lambda x: x.id):
        put(f"{ep.method} {ep.route}", ep.id, "endpoint")
    for f in sorted(ir.files, key=lambda x: x.id):
        put(f.path, f.id, "file")
        put(PurePosixPath(f.path).stem, f.id, "file")
    return out


def workflow_sequence(
    finding: WorkflowFinding, ir: ProjectIR, *, project_id: str | None = None, key: str | None = None
) -> DiagramSpec | None:
    if not finding.steps:
        return None
    names = _name_index(ir)
    participants: dict[str, DiagramNode] = {}

    def participant(raw: str) -> str:
        label = " ".join(raw.split()) or "?"
        norm = _norm(label)
        if norm in participants:
            return participants[norm].id
        pid = f"p{len(participants) + 1}"
        match = names.get(norm)
        if match is not None:
            node = DiagramNode(id=pid, label=label, kind=match[1], ref_uid=match[0])
        else:
            node = DiagramNode(
                id=pid, label=label, kind="actor" if norm in _HUMAN_ACTORS else "component", inferred=True
            )
        participants[norm or pid] = node
        return pid

    edges: list[DiagramEdge] = []
    for step in finding.steps:
        src = participant(step.actor)
        tgt = participant(step.target) if step.target else src
        edges.append(
            DiagramEdge(
                source=src,
                target=tgt,
                label=" ".join(step.action.split()),
                kind="message",
                inferred=not step.evidence_ids,
            )
        )
    annotations = [f"Trigger: {finding.trigger}"] + [f"Failure: {b}" for b in finding.failure_branches]
    return _spec(
        ir,
        project_id,
        "workflow_sequence",
        key or _norm(finding.name) or "workflow",
        title=finding.name,
        description=finding.trigger,
        nodes=list(participants.values()),
        edges=edges,
        annotations=annotations,
    )


# ---- all ---------------------------------------------------------------------------------------------------

MAX_SEQUENCES = 8


def build_all(
    project_id: str, ir: ProjectIR, subsystems: list[SubsystemView], workflows: list[WorkflowFinding]
) -> list[DiagramSpec]:
    """Every diagram the IR supports, validated, in a stable order."""
    specs: list[DiagramSpec | None] = [
        architecture(ir, subsystems, project_id=project_id),
        module_dependencies(ir, project_id=project_id),
        data_model(ir, project_id=project_id),
        request_flow(ir, project_id=project_id),
        deployment(ir, project_id=project_id),
        ai_pipeline(ir, project_id=project_id),
    ]
    used_keys: Counter[str] = Counter()
    for wf in workflows[:MAX_SEQUENCES]:
        base = _norm(wf.name) or "workflow"
        used_keys[base] += 1
        key = base if used_keys[base] == 1 else f"{base}-{used_keys[base]}"
        specs.append(workflow_sequence(wf, ir, project_id=project_id, key=key))
    return [validate_spec(s) for s in specs if s is not None]
