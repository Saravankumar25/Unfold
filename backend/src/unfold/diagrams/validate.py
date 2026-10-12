"""DiagramSpec validation and deterministic repair (LLD §17).

`problems(spec)` reports what is wrong without changing anything; `validate_spec(spec)` returns a repaired copy:

* duplicate node ids → the first occurrence wins;
* edges whose endpoints do not exist are dropped;
* self-loops are dropped except in sequence diagrams (a message to oneself is meaningful there);
* duplicate edges (same source, target and label) are merged;
* node groups that are not declared get a declaration; declared groups nobody uses are dropped;
* more than `MAX_NODES` nodes → the lowest-degree nodes are collapsed into one "N more …" node per group
  (or a single "other" node for ungrouped nodes) and their edges are rewired to it.

Every step is order-preserving and tie-broken by id, so the same input always yields the same output.
"""

from __future__ import annotations

from collections import Counter

from unfold.core.errors import ErrorCode, UnfoldError
from unfold.diagrams.models import MAX_NODES, DiagramEdge, DiagramGroup, DiagramNode, DiagramSpec

SELF_LOOP_KINDS = frozenset({"workflow_sequence"})
COLLAPSED_KIND = "collapsed"
_OTHER = "__other__"


class DiagramValidationError(UnfoldError):
    code = ErrorCode.VALIDATION_ERROR
    http_status = 422


def problems(spec: DiagramSpec, *, max_nodes: int = MAX_NODES) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for n in spec.nodes:
        if n.id in seen:
            out.append(f"duplicate node id {n.id!r}")
        seen.add(n.id)
    for e in spec.edges:
        if e.source not in seen or e.target not in seen:
            out.append(f"edge {e.source!r}->{e.target!r} references a missing node")
        elif e.source == e.target and spec.kind not in SELF_LOOP_KINDS:
            out.append(f"self-loop on {e.source!r}")
    if len(seen) > max_nodes:
        out.append(f"{len(seen)} nodes exceed the limit of {max_nodes}")
    return out


def validate_spec(spec: DiagramSpec, *, max_nodes: int = MAX_NODES) -> DiagramSpec:
    """Return a repaired copy of `spec` that satisfies `problems(...) == []`."""
    if max_nodes < 2:
        raise DiagramValidationError("max_nodes must be at least 2")
    nodes = _unique_nodes(spec.nodes)
    edges = _clean_edges(spec, nodes, spec.edges)
    if len(nodes) > max_nodes:
        nodes, edges = _collapse(spec, nodes, edges, max_nodes)
        edges = _clean_edges(spec, nodes, edges)
    groups = _fix_groups(spec.groups, nodes)
    return spec.model_copy(update={"nodes": nodes, "edges": edges, "groups": groups})


def _unique_nodes(nodes: list[DiagramNode]) -> list[DiagramNode]:
    seen: set[str] = set()
    out: list[DiagramNode] = []
    for n in nodes:
        if n.id not in seen:
            seen.add(n.id)
            out.append(n)
    return out


def _clean_edges(spec: DiagramSpec, nodes: list[DiagramNode], edges: list[DiagramEdge]) -> list[DiagramEdge]:
    ids = {n.id for n in nodes}
    allow_loops = spec.kind in SELF_LOOP_KINDS
    sequence = spec.kind in SELF_LOOP_KINDS
    out: list[DiagramEdge] = []
    index: dict[tuple[str, str, str | None], int] = {}
    for e in edges:
        if e.source not in ids or e.target not in ids:
            continue
        if e.source == e.target and not allow_loops:
            continue
        if sequence:  # sequence messages are ordered events; repeats are meaningful
            out.append(e)
            continue
        key = (e.source, e.target, e.label)
        if key in index:
            prev = out[index[key]]
            out[index[key]] = prev.model_copy(update={"inferred": prev.inferred and e.inferred})
            continue
        index[key] = len(out)
        out.append(e)
    return out


def _degrees(nodes: list[DiagramNode], edges: list[DiagramEdge]) -> Counter[str]:
    deg: Counter[str] = Counter({n.id: 0 for n in nodes})
    for e in edges:
        deg[e.source] += 1
        deg[e.target] += 1
    return deg


def _collapse(
    spec: DiagramSpec, nodes: list[DiagramNode], edges: list[DiagramEdge], max_nodes: int
) -> tuple[list[DiagramNode], list[DiagramEdge]]:
    deg = _degrees(nodes, edges)
    candidates = sorted(nodes, key=lambda n: (deg[n.id], n.id))
    removed: list[DiagramNode] = []
    for r in range(len(nodes) - max_nodes, len(nodes) + 1):
        removed = candidates[:r]
        buckets = {n.group or _OTHER for n in removed}
        if len(nodes) - r + len(buckets) <= max_nodes:
            break
    removed_ids = {n.id for n in removed}
    group_labels = {g.id: g.label for g in spec.groups}
    per_bucket: dict[str, list[DiagramNode]] = {}
    for n in removed:
        per_bucket.setdefault(n.group or _OTHER, []).append(n)

    bucket_node: dict[str, str] = {}
    collapsed_nodes: list[DiagramNode] = []
    for bucket in sorted(per_bucket):
        members = per_bucket[bucket]
        node_id = f"collapsed:{bucket}"
        bucket_node[bucket] = node_id
        if bucket == _OTHER:
            label = f"{len(members)} other components"
        else:
            label = f"{len(members)} more in {group_labels.get(bucket, bucket)}"
        names = ", ".join(m.label for m in members[:12])
        more = "" if len(members) <= 12 else f" (+{len(members) - 12})"
        collapsed_nodes.append(
            DiagramNode(
                id=node_id,
                label=label,
                kind=COLLAPSED_KIND,
                group=None if bucket == _OTHER else bucket,
                detail=f"{names}{more}",
                inferred=all(m.inferred for m in members),
            )
        )
    owner = {n.id: bucket_node[n.group or _OTHER] for n in removed}
    kept = [n for n in nodes if n.id not in removed_ids]
    new_edges: list[DiagramEdge] = []
    for e in edges:
        src = owner.get(e.source, e.source)
        tgt = owner.get(e.target, e.target)
        if src == tgt and spec.kind not in SELF_LOOP_KINDS:
            continue
        collapsed = src != e.source or tgt != e.target
        new_edges.append(
            e.model_copy(update={"source": src, "target": tgt, "label": None if collapsed else e.label})
        )
    return kept + collapsed_nodes, new_edges


def _fix_groups(groups: list[DiagramGroup], nodes: list[DiagramNode]) -> list[DiagramGroup]:
    used = [n.group for n in nodes if n.group]
    used_set = set(used)
    out: list[DiagramGroup] = []
    seen: set[str] = set()
    for g in groups:
        if g.id in used_set and g.id not in seen:
            out.append(g)
            seen.add(g.id)
    for gid in used:
        if gid not in seen:
            out.append(DiagramGroup(id=gid, label=gid))
            seen.add(gid)
    return out
