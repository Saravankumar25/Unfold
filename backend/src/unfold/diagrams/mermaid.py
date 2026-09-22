"""DiagramSpec → Mermaid source (LLD §17, §19). Used by the PDF export; the web renders the same spec natively.

Repository names are untrusted, so nothing from the spec is ever emitted raw:

* node/group ids are replaced by generated identifiers (`n0`, `g0`, `p0`) — spec ids never reach the output;
* every label is reduced to a whitelist of plain characters; everything else (quotes, brackets, pipes, `<`/`>`,
  `#`, `;`, `%`, backticks, markdown characters, non-ASCII) becomes a Mermaid entity code `#<decimal>;`, which
  Mermaid decodes back to the character *after* parsing, so it can neither break nor inject syntax;
* newlines and other whitespace collapse to single spaces; labels are length-limited.
"""

from __future__ import annotations

from unfold.diagrams.models import DiagramEdge, DiagramNode, DiagramSpec

LABEL_LIMIT = 80
EDGE_LABEL_LIMIT = 60
MESSAGE_LIMIT = 120

_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,:/-()?!+=@'")

# node kind -> (open, close) around the quoted label
_SHAPES: dict[str, tuple[str, str]] = {
    "datastore": ("[(", ")]"),
    "queue": (">", "]"),
    "external": ("{{", "}}"),
    "actor": ("((", "))"),
    "endpoint": ("([", "])"),
    "collapsed": ("[/", "/]"),
    "ai": ("[[", "]]"),
    "infra": ("[\\", "\\]"),
}
_DIRECTION = {
    "architecture": "LR",
    "module_dependencies": "LR",
    "data_model": "TD",
    "request_flow": "LR",
    "deployment": "LR",
    "ai_pipeline": "LR",
}


def escape_label(text: str | None, limit: int = LABEL_LIMIT) -> str:
    """Whitelist-encode `text` for use inside a Mermaid string. Output contains only `_SAFE` chars, `#`, digits, `;`."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) > limit:
        collapsed = collapsed[: limit - 1].rstrip() + "…"
    out: list[str] = []
    for ch in collapsed:
        out.append(ch if ch in _SAFE else f"#{ord(ch)};")
    return "".join(out)


def to_mermaid(spec: DiagramSpec) -> str:
    if spec.kind == "workflow_sequence":
        return _sequence(spec)
    return _flowchart(spec)


def _node_line(node_id: str, node: DiagramNode) -> str:
    open_, close = _SHAPES.get(node.kind, ("[", "]"))
    return f'{node_id}{open_}"{escape_label(node.label)}"{close}'


def _edge_line(ids: dict[str, str], edge: DiagramEdge) -> str:
    arrow = "-.->" if edge.inferred else "-->"
    src, tgt = ids[edge.source], ids[edge.target]
    if edge.label:
        return f'{src} {arrow}|"{escape_label(edge.label, EDGE_LABEL_LIMIT)}"| {tgt}'
    return f"{src} {arrow} {tgt}"


def _flowchart(spec: DiagramSpec) -> str:
    ids = {n.id: f"n{i}" for i, n in enumerate(spec.nodes)}
    lines = [f"flowchart {_DIRECTION.get(spec.kind, 'LR')}"]
    grouped: dict[str, list[DiagramNode]] = {}
    for n in spec.nodes:
        if n.group:
            grouped.setdefault(n.group, []).append(n)
    group_labels = {g.id: g.label for g in spec.groups}
    order = [g.id for g in spec.groups if g.id in grouped] + [g for g in grouped if g not in group_labels]
    for gi, gid in enumerate(order):
        lines.append(f'  subgraph g{gi}["{escape_label(group_labels.get(gid, gid))}"]')
        lines.extend(f"    {_node_line(ids[n.id], n)}" for n in grouped[gid])
        lines.append("  end")
    lines.extend(f"  {_node_line(ids[n.id], n)}" for n in spec.nodes if not n.group)
    lines.extend(f"  {_edge_line(ids, e)}" for e in spec.edges if e.source in ids and e.target in ids)
    inferred = [ids[n.id] for n in spec.nodes if n.inferred]
    if inferred:
        lines.append("  classDef inferred stroke-dasharray:4 3")
        lines.append(f"  class {','.join(inferred)} inferred")
    return "\n".join(lines) + "\n"


def _sequence(spec: DiagramSpec) -> str:
    ids = {n.id: f"p{i}" for i, n in enumerate(spec.nodes)}
    lines = ["sequenceDiagram"]
    for n in spec.nodes:
        keyword = "actor" if n.kind == "actor" else "participant"
        lines.append(f"  {keyword} {ids[n.id]} as {escape_label(n.label)}")
    first = ids[spec.nodes[0].id] if spec.nodes else None
    notes = [a for a in spec.annotations if a.startswith("Trigger:")]
    if first is not None:
        lines.extend(f"  Note over {first}: {escape_label(a, MESSAGE_LIMIT)}" for a in notes)
    for e in spec.edges:
        if e.source not in ids or e.target not in ids:
            continue
        arrow = "-->>" if e.inferred else "->>"
        lines.append(f"  {ids[e.source]}{arrow}{ids[e.target]}: {escape_label(e.label or '', MESSAGE_LIMIT)}")
    last = ids[spec.nodes[-1].id] if spec.nodes else None
    for a in spec.annotations:
        if a.startswith("Failure:") and first is not None:
            span = first if first == last else f"{first},{last}"
            lines.append(f"  Note over {span}: {escape_label(a, MESSAGE_LIMIT)}")
    return "\n".join(lines) + "\n"
