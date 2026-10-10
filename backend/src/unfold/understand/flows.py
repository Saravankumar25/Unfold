"""Deterministic flow tracing over the RepoMap (investigation engine §5). Never executes repository code.

Depth-first, preorder, stable edge order, one visit per entity (cycles terminate). `INFERRED` edges are never
followed. Calls the analyzers could not resolve are reported in `unresolved`, not drawn as steps.
"""

from __future__ import annotations

from unfold.core.errors import NotFound
from unfold.domain.enums import Proof
from unfold.understand.models import Edge, FlowStep, FlowTrace
from unfold.understand.repomap import RepoMap

FLOW_RELS = frozenset({"CALLS", "READS_FROM", "WRITES_TO", "PUBLISHES_TO"})
MAX_UNRESOLVED = 20
_PROOF_RANK = {Proof.STATIC: 0, Proof.HEURISTIC: 1, Proof.INFERRED: 2}


def trace_flow(rm: RepoMap, entry: str, max_depth: int = 6, fan_out: int = 4) -> FlowTrace:
    """Trace from `entry` (entity id or unique name). Raises `NotFound` when it is absent or ambiguous."""
    root = rm.symbol(entry)
    if root is None:
        raise NotFound(f"No unique entry point matches {entry!r}.", detail={"entry": entry})
    visited = {root.id}
    steps = [FlowStep(symbol=root, note=rm.entry_label(root.id))]
    unresolved: list[str] = []
    truncated = False

    def candidates(node: str) -> list[Edge]:
        out = [
            e
            for e in rm.out_edges(node)
            if e.rel in FLOW_RELS and e.proof != Proof.INFERRED and e.dst not in visited and rm.ref(e.dst)
        ]
        return sorted(out, key=lambda e: (_PROOF_RANK[e.proof], rm.line_of(e), e.dst))

    def visit(node: str, depth: int) -> None:
        nonlocal truncated
        for u in rm.unresolved(node):
            if u not in unresolved and len(unresolved) < MAX_UNRESOLVED:
                unresolved.append(u)
        # A file-level store usage marks the store once, on the first symbol of that file the flow reaches.
        for e in rm.out_edges(rm.file_of(node) or node):
            store = rm.ref(e.dst)
            used = e.rel == "USES" and e.proof != Proof.INFERRED and store and store.kind == "datastore"
            if used and store and e.dst not in visited:
                visited.add(e.dst)
                steps.append(FlowStep(symbol=store, via=e, note="used by the file containing the step"))
        cands = candidates(node)
        if depth >= max_depth:
            truncated = truncated or bool(cands)
            return
        truncated = truncated or len(cands) > fan_out
        for e in cands[:fan_out]:
            ref = rm.ref(e.dst)
            if ref is None or e.dst in visited:  # visited: reached through an earlier sibling's subtree
                continue
            visited.add(e.dst)
            steps.append(FlowStep(symbol=ref, via=e))
            visit(e.dst, depth + 1)

    visit(root.id, 0)
    return FlowTrace(entry=root, steps=steps, truncated=truncated, unresolved=unresolved)
