"""Subsystem discovery by community detection (LLD §9, AI infra §19).

Louvain (networkx, fixed seed) over the module-level graph: file/symbol IMPORTS/CALLS/INHERITS/USES edges are
aggregated to their owning module ("unit") and blended with directory structure so sibling folders cohere. Tiny
repositories get one subsystem per top-level directory. Output is deterministic for the same IR + seed.
Community boundaries are suggestions; the LLM stage labels (and may refine) them.
"""

from __future__ import annotations

import hashlib
import math
import posixpath
from collections import Counter, defaultdict

import networkx as nx

from unfold.graph.models import SubsystemCandidate
from unfold.project_ir.schema import FileIR, ProjectIR

MAX_SUBSYSTEMS = 12
TINY_FILE_COUNT = 12
TINY_UNIT_COUNT = 3
KEY_FILES = 5

_EDGE_WEIGHTS = {
    "IMPORTS": 1.0,
    "CALLS": 0.6,
    "INHERITS": 0.6,
    "IMPLEMENTS": 0.6,
    "USES": 0.4,
    "DEPENDS_ON": 0.3,
    "READS_FROM": 0.3,
    "WRITES_TO": 0.3,
}
_SIBLING_WEIGHT = 0.35  # modules in the same parent directory
_PARENT_WEIGHT = 0.6  # module directly nested in another module's directory


def _subsystem_id(project_id: str, member_file_ids: list[str]) -> str:
    h = hashlib.sha1("\x1f".join([project_id, *sorted(member_file_ids)]).encode(), usedforsecurity=False)
    return f"subsystem:{h.hexdigest()[:16]}"


def _dir(path: str) -> str:
    return posixpath.dirname(path.strip("/"))


def _top(path: str) -> str:
    parts = path.strip("/").split("/")
    return parts[0] if len(parts) > 1 else ""


def fan_in(ir: ProjectIR) -> Counter[str]:
    """Incoming IMPORTS/CALLS per entity id; symbol-level counts also roll up to the symbol's file."""
    sym_file = {s.id: s.file_id for s in ir.symbols}
    counts: Counter[str] = Counter()
    for r in ir.relationships:
        if r.type in ("IMPORTS", "CALLS") and r.source_id != r.target_id:
            counts[r.target_id] += 1
            if r.target_id in sym_file:
                counts[sym_file[r.target_id]] += 1
    return counts


class _Units:
    """Maps IR entities to module-level units (real module ids or `dir:<path>` pseudo modules)."""

    def __init__(self, ir: ProjectIR) -> None:
        self.files = [f for f in ir.files if f.tier < 4]
        self.module_ids = {m.id for m in ir.modules}
        self.module_dir = {m.id: m.path.strip("/") for m in ir.modules}
        file_by_path = {f.path: f for f in self.files}
        self.file_unit: dict[str, str] = {}
        for f in self.files:
            self.file_unit[f.id] = f.module_id if f.module_id in self.module_ids else f"dir:{_dir(f.path)}"
        for m in ir.modules:  # modules listing files that do not point back
            for fid in m.file_ids:
                self.file_unit.setdefault(fid, m.id)
        self.entity_unit: dict[str, str] = dict(self.file_unit)
        for mid in self.module_ids:
            self.entity_unit[mid] = mid
        for s in ir.symbols:
            if s.file_id in self.file_unit:
                self.entity_unit[s.id] = self.file_unit[s.file_id]
        for group in (ir.endpoints, ir.schemas):
            for e in group:
                if e.path and e.path in file_by_path:
                    self.entity_unit[e.id] = self.file_unit[file_by_path[e.path].id]
        self.unit_files: dict[str, list[FileIR]] = defaultdict(list)
        for f in self.files:
            self.unit_files[self.file_unit[f.id]].append(f)

    def unit_dir(self, unit: str) -> str:
        if unit.startswith("dir:"):
            return unit[4:]
        files = self.unit_files.get(unit)
        return self.module_dir.get(unit) or (_dir(files[0].path) if files else "")


def _tiny_partition(units: _Units) -> list[set[str]]:
    by_top: dict[str, set[str]] = defaultdict(set)
    for unit, files in units.unit_files.items():
        tops = Counter(_top(f.path) for f in files)
        by_top[sorted(tops.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]].add(unit)
    return [by_top[k] for k in sorted(by_top)]


def _build_graph(ir: ProjectIR, units: _Units) -> nx.Graph:
    weights: dict[tuple[str, str], float] = defaultdict(float)
    for r in ir.relationships:
        w = _EDGE_WEIGHTS.get(r.type)
        if w is None:
            continue
        a, b = units.entity_unit.get(r.source_id), units.entity_unit.get(r.target_id)
        if a and b and a != b and a in units.unit_files and b in units.unit_files:
            weights[(min(a, b), max(a, b))] += w * r.provenance.confidence
    graph = nx.Graph()
    unit_list = sorted(units.unit_files)
    graph.add_nodes_from(unit_list)
    for (a, b), w in sorted(weights.items()):
        graph.add_edge(a, b, weight=1.0 + math.log1p(w))  # dampen hub modules
    # Directory structure blend.
    dir_of = {u: units.unit_dir(u) for u in unit_list}
    by_parent: dict[str, list[str]] = defaultdict(list)
    for u in unit_list:
        by_parent[_dir(dir_of[u]) if dir_of[u] else "\x00root"].append(u)
    dir_to_unit = {d: u for u, d in dir_of.items()}
    for u in unit_list:
        d = dir_of[u]
        parent = dir_to_unit.get(_dir(d)) if d else None
        if parent and parent != u:
            _add_weight(graph, u, parent, _PARENT_WEIGHT)
    for siblings in by_parent.values():
        if 1 < len(siblings) <= 25:
            for i, a in enumerate(siblings):
                for b in siblings[i + 1 :]:
                    _add_weight(graph, a, b, _SIBLING_WEIGHT)
    return graph


def _add_weight(graph: nx.Graph, a: str, b: str, w: float) -> None:
    if graph.has_edge(a, b):
        graph[a][b]["weight"] += w
    else:
        graph.add_edge(a, b, weight=w)


def _merge_small(
    graph: nx.Graph, communities: list[set[str]], units: _Units, max_count: int
) -> list[set[str]]:
    comms = [set(c) for c in communities if c]

    def size(c: set[str]) -> int:
        return sum(len(units.unit_files[u]) for u in c)

    def connection(a: set[str], b: set[str]) -> float:
        return float(sum(graph[u][v]["weight"] for u in a for v in graph.neighbors(u) if v in b))

    def dir_affinity(a: set[str], b: set[str]) -> int:
        pa = min(units.unit_dir(u) for u in a).split("/")
        pb = min(units.unit_dir(u) for u in b).split("/")
        common = 0
        for x, y in zip(pa, pb, strict=False):
            if not x or x != y:
                break
            common += 1
        return common

    def merge_once(require_small: bool) -> bool:
        order = sorted(range(len(comms)), key=lambda i: (size(comms[i]), min(comms[i])))
        for i in order:
            if require_small and size(comms[i]) >= 2:
                return False
            others = sorted((j for j in range(len(comms)) if j != i), key=lambda j: min(comms[j]))
            if not others:
                return False
            # max() keeps the first maximal element, so ties resolve to the smallest community key.
            best = max(
                others,
                key=lambda j: (
                    connection(comms[i], comms[j]),
                    dir_affinity(comms[i], comms[j]),
                    -size(comms[j]),
                ),
            )
            if (
                require_small
                and connection(comms[i], comms[best]) == 0
                and dir_affinity(comms[i], comms[best]) == 0
            ):
                continue
            comms[best] |= comms[i]
            del comms[i]
            return True
        return False

    while len(comms) > 1 and merge_once(require_small=True):
        pass
    while len(comms) > max_count and merge_once(require_small=False):
        pass
    return comms


def _label_hint(files: list[FileIR]) -> str:
    dirs = [_dir(f.path) for f in files]
    non_root = [d for d in dirs if d]
    if not non_root:
        return "root"
    common = posixpath.commonpath(non_root) if len(non_root) == len(dirs) else ""
    if common:
        return common.split("/")[-1]
    counts = Counter(d.split("/")[-1] for d in non_root)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def detect(ir: ProjectIR, seed: int = 42, max_subsystems: int = MAX_SUBSYSTEMS) -> list[SubsystemCandidate]:
    units = _Units(ir)
    if not units.unit_files:
        return []
    graph = _build_graph(ir, units)
    if len(units.files) <= TINY_FILE_COUNT or len(units.unit_files) <= TINY_UNIT_COUNT:
        communities = _tiny_partition(units)
    else:
        raw = nx.community.louvain_communities(graph, weight="weight", resolution=1.0, seed=seed)
        communities = sorted((set(c) for c in raw), key=min)
        communities = _merge_small(graph, communities, units, max_subsystems)
    if len(communities) > max_subsystems:
        communities = _merge_small(graph, communities, units, max_subsystems)

    fi = fan_in(ir)
    total_weight: dict[str, float] = {
        u: sum(d["weight"] for _, _, d in graph.edges(u, data=True)) for u in graph.nodes
    }
    out: list[SubsystemCandidate] = []
    for comm in communities:
        files = sorted((f for u in comm for f in units.unit_files[u]), key=lambda f: f.path)
        if not files:
            continue
        file_ids = [f.id for f in files]
        internal = sum(d["weight"] for a, b, d in graph.edges(comm, data=True) if a in comm and b in comm)
        boundary = sum(total_weight[u] for u in comm) - 2 * internal
        cohesion = internal / (internal + boundary) if internal + boundary > 0 else 0.0
        key_files = sorted(files, key=lambda f: (f.tier, -fi.get(f.id, 0), f.path))[:KEY_FILES]
        out.append(
            SubsystemCandidate(
                id=_subsystem_id(ir.project.project_id, file_ids),
                member_module_ids=sorted(u for u in comm if u in units.module_ids),
                member_file_ids=file_ids,
                key_files=[f.id for f in key_files],
                label_hint=_label_hint(files),
                cohesion=round(cohesion, 4),
            )
        )
    out.sort(key=lambda c: (-len(c.member_file_ids), c.id))
    return out


def entity_subsystems(ir: ProjectIR, candidates: list[SubsystemCandidate]) -> dict[str, str]:
    """entity uid → subsystem id for files, their symbols, member modules and path-located entities."""
    file_sub = {fid: c.id for c in candidates for fid in c.member_file_ids}
    out: dict[str, str] = dict(file_sub)
    for c in candidates:
        for mid in c.member_module_ids:
            out[mid] = c.id
    for s in ir.symbols:
        if s.file_id in file_sub:
            out[s.id] = file_sub[s.file_id]
    path_sub = {f.path: file_sub[f.id] for f in ir.files if f.id in file_sub}
    for group in (ir.endpoints, ir.schemas, ir.infrastructure, ir.services):
        for e in group:
            if e.path and e.path in path_sub:
                out[e.id] = path_sub[e.path]
    return out
