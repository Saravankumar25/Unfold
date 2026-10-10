"""RepoMap: symbol lookup and neighbour expansion over a `ProjectIR` (no Neo4j needed; same edges as the graph).

Never executes repository code. Edges carry the IR `proof`; callers decide what to trust (`flows.trace_flow`
never follows `INFERRED`).
"""

from __future__ import annotations

from collections import defaultdict, deque

from unfold.graph.models import TRAVERSAL_REL_TYPES
from unfold.project_ir.schema import Entity, ProjectIR
from unfold.understand.models import Edge, SymbolRef

MAX_DEPTH = 4
MAX_FIND = 50
DEFAULT_RELS: tuple[str, ...] = tuple(r for r in TRAVERSAL_REL_TYPES if r not in ("CONTAINS", "PART_OF"))
_COMMAND_DECORATORS = frozenset({"command", "group"})
_MAIN_FILES = frozenset({"__main__.py", "main.py", "cli.py"})


class RepoMap:
    def __init__(self, ir: ProjectIR) -> None:
        self.ir = ir
        self._refs: dict[str, SymbolRef] = {}
        self._qual: dict[str, str] = {}  # id -> qualified name used for lookup
        self._file_of: dict[str, str] = {}  # symbol id -> file id
        for f in ir.files:
            self._add(f, f.path, 1, f.lines, f.path)
        for s in ir.symbols:
            self._file_of[s.id] = s.file_id
            self._add(s, s.path, s.start_line, s.end_line, s.qualname)
        for e in (*ir.endpoints, *ir.schemas, *ir.data_stores):
            self._add(e, e.path, e.start_line, e.end_line, e.name)
        self._out: dict[str, list[Edge]] = defaultdict(list)
        self._in: dict[str, list[Edge]] = defaultdict(list)
        self._line: dict[tuple[str, str, str], int] = {}
        for r in ir.relationships:  # IR order is already deterministic (type, source, target)
            edge = Edge(src=r.source_id, dst=r.target_id, rel=r.type, proof=r.proof)
            self._out[edge.src].append(edge)
            self._in[edge.dst].append(edge)
            self._line[(edge.src, edge.dst, edge.rel)] = int(r.properties.get("line") or 0)
        self._unresolved: dict[str, list[str]] = defaultdict(list)
        for u in ir.unresolved_calls:
            self._unresolved[u.source_id].append(f"{u.name} ({u.path}:{u.line})")
        self._entries = self._find_entries()

    def _add(self, e: Entity, path: str | None, start: int | None, end: int | None, name: str) -> None:
        if not path:
            return
        s = start or 1
        self._refs[e.id] = SymbolRef(
            id=e.id,
            name=name,
            kind=str(getattr(e, "kind", "entity")),
            path=path,
            start_line=s,
            end_line=max(end or s, s),
        )
        self._qual[e.id] = name

    # --- lookup ---------------------------------------------------------------------------------------------

    def ref(self, entity_id: str) -> SymbolRef | None:
        return self._refs.get(entity_id)

    def file_of(self, symbol_id: str) -> str | None:
        """Id of the file that contains a symbol."""
        return self._file_of.get(symbol_id)

    def symbol(self, name_or_id: str) -> SymbolRef | None:
        """By id, else the single entity whose name / qualified name / path matches; None when absent or
        ambiguous (use `find` to see the candidates)."""
        hit = self._refs.get(name_or_id)
        if hit is not None:
            return hit
        found = self._exact(name_or_id)
        return found[0] if len(found) == 1 else None

    def _exact(self, name: str, kind: str | None = None) -> list[SymbolRef]:
        out = []
        for eid, r in self._refs.items():
            if kind is not None and r.kind != kind:
                continue
            q = self._qual[eid]
            if r.kind == "file":
                hit = name == r.path or r.path.endswith("/" + name)
            else:
                hit = name in (q, q.rsplit(".", 1)[-1])
            if hit:
                out.append(r)
        return sorted(out, key=lambda r: (r.path, r.start_line, r.name, r.id))

    def find(self, name: str, kind: str | None = None) -> list[SymbolRef]:
        """Exact name / qualified-name / file-path-suffix matches, else case-insensitive substring of the qualified
        name. Ordered by path and line; at most `MAX_FIND`."""
        hits = self._exact(name, kind)
        if not hits and name:
            low = name.lower()
            hits = sorted(
                (r for i, r in self._refs.items() if low in self._qual[i].lower() and kind in (None, r.kind)),
                key=lambda r: (r.path, r.start_line, r.name, r.id),
            )
        return hits[:MAX_FIND]

    def files(self) -> list[SymbolRef]:
        return sorted((r for r in self._refs.values() if r.kind == "file"), key=lambda r: r.path)

    # --- edges ----------------------------------------------------------------------------------------------

    def out_edges(self, entity_id: str) -> list[Edge]:
        return self._out.get(entity_id, [])

    def unresolved(self, entity_id: str) -> list[str]:
        """Calls made by `entity_id` that the analyzers could not resolve (never edges)."""
        return self._unresolved.get(entity_id, [])

    def line_of(self, edge: Edge) -> int:
        return self._line.get((edge.src, edge.dst, edge.rel), 0)

    def neighbours(self, symbol_id: str, rels: tuple[str, ...] | None = None, depth: int = 1) -> list[Edge]:
        """Edges within `depth` hops of `symbol_id` (either direction), in discovery order."""
        wanted = set(rels or DEFAULT_RELS)
        seen = {symbol_id}
        edges: dict[tuple[str, str, str], Edge] = {}
        queue: deque[tuple[str, int]] = deque([(symbol_id, 0)])
        while queue:
            node, d = queue.popleft()
            if d >= max(1, min(depth, MAX_DEPTH)):
                continue
            for e in [*self._out.get(node, []), *self._in.get(node, [])]:
                if e.rel not in wanted:
                    continue
                edges.setdefault((e.src, e.dst, e.rel), e)
                other = e.dst if e.src == node else e.src
                if other not in seen:
                    seen.add(other)
                    queue.append((other, d + 1))
        return list(edges.values())

    # --- entry points ---------------------------------------------------------------------------------------

    def _find_entries(self) -> dict[str, str]:
        ir = self.ir
        found: dict[str, str] = {}
        for ep in ir.endpoints:
            target = ep.handler_id if ep.handler_id in self._refs else ep.id
            found.setdefault(target, f"{ep.method} {ep.route}")
        for r in ir.relationships:
            if r.type == "CONSUMES_FROM" and r.source_id in self._file_of:
                found.setdefault(r.source_id, "queue consumer")
        for sv in ir.services:
            if sv.entrypoint_file_id:
                found.setdefault(sv.entrypoint_file_id, f"{sv.service_kind} service {sv.name}")
        paths = {f.id: f.path for f in ir.files}
        for s in ir.symbols:
            if s.kind not in ("function", "method"):
                continue
            if any(d.split("(")[0].rsplit(".", 1)[-1] in _COMMAND_DECORATORS for d in s.decorators):
                found.setdefault(s.id, "command")
            elif s.qualname == "main" and paths.get(s.file_id, "").rsplit("/", 1)[-1] in _MAIN_FILES:
                found.setdefault(s.id, "main")
        return {i: label for i, label in found.items() if i in self._refs}

    def entry_points(self) -> list[SymbolRef]:
        """Route handlers, queue consumers, service entry files, CLI commands and `main`; ordered by location."""
        refs = (self._refs[i] for i in self._entries)
        return sorted(refs, key=lambda r: (r.path, r.start_line, r.id))

    def entry_label(self, entity_id: str) -> str | None:
        """`POST /query`, `queue consumer`, ... for an entry point."""
        return self._entries.get(entity_id)
