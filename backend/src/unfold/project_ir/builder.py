"""Project IR builder (LLD §8, ESD §9): `Inventory` + `RepositoryAnalysis` → `ProjectIR`.

Pure and deterministic: no I/O, no clock, no randomness. Every entity id comes from `core.ids.entity_id`, every
collection is emitted in a sorted order and relationships are de-duplicated on `(type, source, target)`, so the
same inputs always serialize to byte-identical JSON (idempotent graph upserts, cacheable snapshots).

Id keys (kind, path, qualname) — consumers (chunking, graph, diagrams, tests) rely on these:

| entity     | path                  | qualname                         |
|------------|-----------------------|----------------------------------|
| file       | file path             | ""                               |
| module     | directory ("" = root) | ""                               |
| symbol     | file path             | symbol qualname (kind = symbol kind) |
| endpoint   | file path             | "METHOD /route"                  |
| schema     | file path             | schema name                      |
| datastore  | ""                    | canonical technology             |
| queue      | ""                    | canonical technology             |
| framework  | ""                    | technology                       |
| ai         | ""                    | "component_kind:technology"      |
| service    | defining file path    | service / app-object name        |
| infra      | file path             | "resource_kind:name"             |
| dependency | manifest path         | "ecosystem:name"                 |

Relationship directions follow `graph.builder._derived_relationships` (e.g. `HANDLES` is handler → endpoint,
`EXPOSES` is file/service → endpoint) so IR edges and graph-derived edges coincide instead of duplicating.
Facts linked by heuristics (name matching, service roots, unresolved-call fallbacks) stay `deterministic` but carry
a confidence below 1.0 and a `project_ir.link` / `project_ir.resolve` analyzer name.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

from unfold.core.errors import ErrorCode, UnfoldError
from unfold.core.ids import entity_id
from unfold.core.logging import get_logger
from unfold.domain.enums import Proof
from unfold.domain.models import Provenance
from unfold.project_ir.schema import (
    AIComponentIR,
    DataStoreIR,
    DependencyIR,
    EndpointIR,
    Entity,
    FileIR,
    FrameworkInfo,
    InfraResourceIR,
    LanguageInfo,
    ModuleIR,
    ProjectIdentity,
    ProjectIR,
    QueueIR,
    RelationshipIR,
    RelType,
    SchemaIR,
    ServiceIR,
    SymbolIR,
    UnresolvedCallIR,
)
from unfold.repo_intel.models import (
    Binding,
    ComposeServiceDef,
    Detected,
    FileAnalysis,
    Inventory,
    InventoryFile,
    RepositoryAnalysis,
    RouteDef,
)

__all__ = ["build_ir", "validate_ir"]

log = get_logger(__name__)

BUILDER = "project_ir"
LINK = "project_ir.link"  # heuristic cross-entity linkage
RESOLVE = "project_ir.resolve"  # fallback name resolution the analyzers did not do
DETECT = "repo_intel.detect"

_UNRESOLVED_CALL_CONFIDENCE = 0.7  # resolved by the analyzer but no confidence recorded
_FALLBACK_CONFIDENCE = 0.6
_NAME_MATCH_CONFIDENCE = 0.7
_ROOT_MATCH_CONFIDENCE = 0.8
MAX_UNRESOLVED_CALLS = 5000
# Callees that dispatch dynamically: recorded as unresolved even when no project symbol shares the name.
DYNAMIC_CALLEES = frozenset({"getattr", "import_module", "__import__", "eval", "exec"})

CODE_LANGUAGES = frozenset(
    {
        "python",
        "javascript",
        "typescript",
        "tsx",
        "jsx",
        "go",
        "java",
        "kotlin",
        "scala",
        "rust",
        "c",
        "cpp",
        "c++",
        "csharp",
        "c#",
        "ruby",
        "php",
        "swift",
        "objective-c",
        "dart",
        "elixir",
        "erlang",
        "haskell",
        "clojure",
        "lua",
        "perl",
        "r",
        "julia",
        "vue",
        "svelte",
        "shell",
        "bash",
        "powershell",
        "groovy",
        "fsharp",
        "ocaml",
        "zig",
        "nim",
        "solidity",
    }
)

# Compose/k8s image → (kind, canonical technology, category). Matched against the image's last path segment.
_IMAGE_RULES: tuple[tuple[str, str, str, str], ...] = (
    ("postgis", "datastore", "postgresql", "relational"),
    ("timescaledb", "datastore", "postgresql", "relational"),
    ("postgres", "datastore", "postgresql", "relational"),
    ("mysql", "datastore", "mysql", "relational"),
    ("mariadb", "datastore", "mariadb", "relational"),
    ("mssql", "datastore", "sqlserver", "relational"),
    ("cockroach", "datastore", "cockroachdb", "relational"),
    ("clickhouse", "datastore", "clickhouse", "relational"),
    ("mongo", "datastore", "mongodb", "document"),
    ("couchdb", "datastore", "couchdb", "document"),
    ("cassandra", "datastore", "cassandra", "document"),
    ("scylla", "datastore", "scylladb", "document"),
    ("dynamodb", "datastore", "dynamodb", "document"),
    ("redis", "datastore", "redis", "key_value"),
    ("valkey", "datastore", "redis", "key_value"),
    ("keydb", "datastore", "redis", "key_value"),
    ("memcached", "datastore", "memcached", "cache"),
    ("neo4j", "datastore", "neo4j", "graph"),
    ("qdrant", "datastore", "qdrant", "vector"),
    ("weaviate", "datastore", "weaviate", "vector"),
    ("milvus", "datastore", "milvus", "vector"),
    ("chroma", "datastore", "chroma", "vector"),
    ("elasticsearch", "datastore", "elasticsearch", "search"),
    ("opensearch", "datastore", "opensearch", "search"),
    ("meilisearch", "datastore", "meilisearch", "search"),
    ("typesense", "datastore", "typesense", "search"),
    ("minio", "datastore", "minio", "object"),
    ("localstack", "datastore", "s3", "object"),
    ("rabbitmq", "queue", "rabbitmq", "queue"),
    ("kafka", "queue", "kafka", "queue"),
    ("redpanda", "queue", "kafka", "queue"),
    ("nats", "queue", "nats", "queue"),
    ("activemq", "queue", "activemq", "queue"),
)
_TECH_ALIASES = {
    "postgres": "postgresql",
    "pg": "postgresql",
    "psql": "postgresql",
    "mongo": "mongodb",
    "elastic": "elasticsearch",
    "valkey": "redis",
    "redpanda": "kafka",
    "amazon_s3": "s3",
    "aws_s3": "s3",
}
_PROXY_IMAGES = ("nginx", "traefik", "caddy", "haproxy", "envoy", "httpd", "apache")
_WORKER_WORDS = ("worker", "celery", "arq", "rq ", "dramatiq", "sidekiq", "bullmq", "huey", "consumer")
_CRON_WORDS = ("cron", "beat", "scheduler")
_WEB_OBJECTS = frozenset(
    {"nextjs", "next", "react", "vue", "nuxt", "svelte", "sveltekit", "angular", "remix"}
)
_CLI_OBJECTS = frozenset({"click", "typer", "argparse", "cobra", "commander", "yargs", "cli", "fire"})
_FRONTEND_DIRS = frozenset({"frontend", "client", "web", "ui", "webapp", "www", "app-web"})
_EVIDENCE_LINK_SOURCES = frozenset({"import", "code", "config"})

_PY_MODULE_TARGET = re.compile(r"(?<![\w./-])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):[A-Za-z_]\w*")
_PY_DASH_M = re.compile(r"-m\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")
_DOTTED = re.compile(r"(?<![\w./:-])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)(?![\w/:])")
_PATH_TARGET = re.compile(r"[\w./-]+\.(?:py|js|mjs|cjs|ts|mts|go|rb|php|java|kt|jar|exs?)\b")


# ---- small helpers -----------------------------------------------------------------------------------------


def _prov(analyzer: str, confidence: float = 1.0) -> Provenance:
    return Provenance(source="deterministic", analyzer=analyzer, confidence=round(float(confidence), 4))


def _norm_name(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", name.lower()).strip("_")


def _canon_tech(tech: str) -> str:
    key = _norm_name(tech)
    return _TECH_ALIASES.get(key, key)


def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _join(base: str, rel: str) -> str:
    rel = rel.strip()
    if rel.startswith("/"):
        rel = rel.lstrip("/")
    joined = posixpath.normpath(posixpath.join(base, rel)) if base else posixpath.normpath(rel)
    return "" if joined in (".", "") else joined


def _under(path: str, root: str) -> bool:
    return not root or path == root or path.startswith(root + "/")


def _is_dockerfile(path: str) -> bool:
    base = _basename(path).lower()
    return base == "dockerfile" or base.startswith("dockerfile.") or base.endswith(".dockerfile")


def _image_rule(image: str | None) -> tuple[str, str, str] | None:
    if not image:
        return None
    name = image.split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0].lower()
    for key, kind, tech, category in _IMAGE_RULES:
        if name.startswith(key):
            return kind, tech, category
    return None


def normalize_route(route: str) -> str:
    """`/api//users/` → `/api/users`; empty → `/`. Path parameters are kept as written."""
    route = route.strip() or "/"
    if not route.startswith("/"):
        route = "/" + route
    route = re.sub(r"/{2,}", "/", route)
    if len(route) > 1:
        route = route.rstrip("/") or "/"
    return route


def _join_route(prefix: str, path: str) -> str:
    if not prefix:
        return normalize_route(path)
    return normalize_route(f"{prefix.rstrip('/')}/{path.lstrip('/')}")


def _is_code(f: InventoryFile, fa: FileAnalysis | None) -> bool:
    if fa is not None and fa.symbols:
        return True
    return (f.language or "").lower() in CODE_LANGUAGES


# ---- builder -----------------------------------------------------------------------------------------------


class _Builder:
    def __init__(
        self,
        identity: ProjectIdentity,
        inventory: Inventory,
        analysis: RepositoryAnalysis,
        analysis_version: str,
    ) -> None:
        self.identity = identity
        self.pid = identity.project_id
        self.inventory = inventory
        self.analysis = analysis
        self.analysis_version = analysis_version
        self.inv_files = sorted((f for f in inventory.files if f.tier < 4), key=lambda f: f.path)
        self.inv_by_path = {f.path: f for f in self.inv_files}
        self.fa: dict[str, FileAnalysis] = {
            p: a for p, a in sorted(analysis.files.items()) if p in self.inv_by_path
        }
        self.ignored_analyses = sorted(set(analysis.files) - set(self.fa))

        self.files: dict[str, FileIR] = {}  # path -> file
        self.modules: dict[str, ModuleIR] = {}  # dir -> module
        self.symbols: dict[str, SymbolIR] = {}  # id -> symbol
        self.sym_by_path: dict[str, dict[str, SymbolIR]] = defaultdict(dict)  # path -> qualname -> symbol
        self.endpoints: dict[str, EndpointIR] = {}
        self.schemas: dict[str, SchemaIR] = {}
        self.data_stores: dict[str, DataStoreIR] = {}  # canonical tech -> store
        self.services: dict[str, ServiceIR] = {}
        self.queues: dict[str, QueueIR] = {}  # canonical tech -> queue
        self.infra: dict[str, InfraResourceIR] = {}
        self.ai: dict[str, AIComponentIR] = {}  # "kind:tech" -> component
        self.frameworks: dict[str, FrameworkInfo] = {}
        self.dependencies: dict[str, DependencyIR] = {}
        self.rels: dict[tuple[str, str, str], RelationshipIR] = {}
        self.unresolved: list[UnresolvedCallIR] = []
        self.dropped_self_loops = 0
        self._schema_lookup: dict[str, list[SchemaIR]] = defaultdict(list)  # normalized name/table
        self._deps_by_name: dict[str, list[DependencyIR]] = defaultdict(list)
        self._files_by_id: dict[str, FileIR] = {}

    # --- relationships -------------------------------------------------------------------------------------

    def rel(
        self,
        rel_type: RelType,
        source: str | None,
        target: str | None,
        analyzer: str = BUILDER,
        confidence: float = 1.0,
        **properties: Any,
    ) -> None:
        if not source or not target:
            return
        if source == target:
            self.dropped_self_loops += 1
            return
        key = (rel_type, source, target)
        existing = self.rels.get(key)
        if existing is not None and existing.provenance.confidence >= confidence:
            return
        props = {k: v for k, v in properties.items() if v is not None and v != [] and v != ""}
        if existing is not None:
            props = {**props, **existing.properties}
        self.rels[key] = RelationshipIR(
            source_id=source,
            target_id=target,
            type=rel_type,
            provenance=_prov(analyzer, confidence),
            properties=props,
            proof=Proof.HEURISTIC
            if rel_type in ("CALLS", "INHERITS") or analyzer in (RESOLVE, LINK, DETECT)
            else Proof.STATIC,
        )

    def eid(self, kind: str, path: str, qualname: str = "") -> str:
        return entity_id(self.pid, kind, path, qualname)

    # --- files & modules -----------------------------------------------------------------------------------

    def build_files_and_modules(self) -> None:
        code_dirs: dict[str, list[InventoryFile]] = defaultdict(list)
        for f in self.inv_files:
            if _is_code(f, self.fa.get(f.path)):
                code_dirs[f.directory].append(f)
        all_paths = set(self.inv_by_path)
        for d in sorted(code_dirs):
            langs = Counter((f.language or "").lower() for f in code_dirs[d] if f.language)
            language = sorted(langs.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if langs else None
            self.modules[d] = ModuleIR(
                id=self.eid("module", d),
                name=self._module_name(d, language, all_paths),
                path=d,
                language=language,
                provenance=_prov(BUILDER),
            )
        for f in self.inv_files:
            fa = self.fa.get(f.path)
            module = self.modules.get(f.directory)
            doc = fa.docstring.strip() if fa is not None and fa.docstring else None
            self.files[f.path] = FileIR(
                id=self.eid("file", f.path),
                name=f.name,
                path=f.path,
                language=f.language,
                size_bytes=f.size,
                lines=f.lines,
                tier=f.tier,
                role=f.role,
                is_text=f.is_text,
                module_id=module.id if module else None,
                docstring=doc or None,
                provenance=_prov(fa.analyzer if fa is not None else "inventory"),
            )
            if module is not None:
                module.file_ids.append(self.files[f.path].id)
                self.rel("CONTAINS", module.id, self.files[f.path].id)
        # Module hierarchy: nearest ancestor module contains the child module.
        for d, module in self.modules.items():
            parts = d.split("/") if d else []
            for cut in range(len(parts) - 1, -1, -1):
                parent = self.modules.get("/".join(parts[:cut]))
                if parent is not None and parent.id != module.id:
                    self.rel("CONTAINS", parent.id, module.id)
                    break

    def _module_name(self, d: str, language: str | None, all_paths: set[str]) -> str:
        if not d:
            return "(root)"
        if language == "python" and f"{d}/__init__.py" in all_paths:
            parts = d.split("/")
            top = len(parts) - 1
            while top > 0 and "/".join([*parts[:top], "__init__.py"]) in all_paths:
                top -= 1
            return ".".join(parts[top:])
        return d

    # --- symbols -------------------------------------------------------------------------------------------

    def build_symbols(self) -> None:
        for path, fa in self.fa.items():
            file = self.files[path]
            by_qual = self.sym_by_path[path]
            for sd in sorted(fa.symbols, key=lambda s: (s.start_line, s.qualname, s.kind)):
                if sd.qualname in by_qual:
                    continue  # redefinition (conditional def, overload): keep the first
                sym = SymbolIR(
                    id=self.eid(sd.kind, path, sd.qualname),
                    name=sd.name,
                    kind=sd.kind,
                    qualname=sd.qualname,
                    file_id=file.id,
                    path=path,
                    start_line=sd.start_line,
                    end_line=max(sd.start_line, sd.end_line),
                    signature=sd.signature,
                    docstring=sd.docstring.strip() if sd.docstring else None,
                    decorators=list(sd.decorators),
                    bases=list(sd.bases),
                    exported=sd.exported,
                    provenance=_prov(fa.analyzer),
                )
                by_qual[sd.qualname] = sym
                self.symbols[sym.id] = sym
            parents = {sd.qualname: sd.parent for sd in fa.symbols}
            for qual, sym in by_qual.items():
                parent = self._enclosing(by_qual, qual, parents.get(qual))
                if parent is not None:
                    sym.parent_id = parent.id
                    self.rel("CONTAINS", parent.id, sym.id)
                else:
                    self.rel("CONTAINS", file.id, sym.id)

    @staticmethod
    def _enclosing(by_qual: dict[str, SymbolIR], qual: str, parent: str | None) -> SymbolIR | None:
        if parent and parent in by_qual and parent != qual:
            return by_qual[parent]
        parts = qual.split(".")
        for cut in range(len(parts) - 1, 0, -1):
            cand = by_qual.get(".".join(parts[:cut]))
            if cand is not None:
                return cand
        return None

    def symbol(self, path: str | None, qualname: str | None) -> SymbolIR | None:
        if not path or not qualname:
            return None
        return self.sym_by_path.get(path, {}).get(qualname)

    def actor(self, path: str, qualname: str | None) -> str:
        """The symbol a site belongs to (falls back to the file for module-level code)."""
        sym = self.symbol(path, qualname)
        if sym is None and qualname:  # e.g. a nested helper the analyzer did not emit as a symbol
            sym = self._enclosing(self.sym_by_path.get(path, {}), qualname, None)
        return sym.id if sym is not None else self.files[path].id

    def resolve_ref(self, path: str, parts: list[str], caller: str | None = None) -> SymbolIR | None:
        """Best-effort static resolution of a dotted reference written in `path` (never executes code)."""
        if not parts:
            return None
        fa = self.fa.get(path)
        local = self.sym_by_path.get(path, {})
        head, rest = parts[0], parts[1:]
        if head in ("self", "cls", "this") and caller and rest:
            cls = self._enclosing(local, caller, None)
            while cls is not None and cls.kind not in ("class", "interface"):
                cls = self.symbols.get(cls.parent_id) if cls.parent_id else None
            if cls is not None:
                return local.get(".".join([cls.qualname, *rest]))
            return None
        if ".".join(parts) in local:
            return local[".".join(parts)]
        binding: Binding | None = fa.bindings.get(head) if fa is not None else None
        if binding is not None and binding.path and binding.path in self.sym_by_path:
            target = self.sym_by_path[binding.path]
            if binding.kind == "symbol" and binding.attr:
                return target.get(".".join([binding.attr, *rest])) or (
                    target.get(binding.attr) if not rest else None
                )
            if binding.kind == "module" and rest:
                return target.get(".".join(rest))
        return None

    # --- imports / calls / inheritance ---------------------------------------------------------------------

    def build_code_relationships(self) -> None:
        # Unresolved calls that matter: the callee name is a project symbol (receiver type unknown) or dynamic.
        known = {s.qualname.rsplit(".", 1)[-1] for s in self.symbols.values()} | DYNAMIC_CALLEES
        for path, fa in self.fa.items():
            file_id = self.files[path].id
            for imp in sorted(fa.imports, key=lambda i: (i.line, i.module)):
                if imp.resolved_path and imp.resolved_path in self.files:
                    self.rel("IMPORTS", file_id, self.files[imp.resolved_path].id, fa.analyzer, line=imp.line)
                elif imp.resolved_dir is not None and imp.resolved_dir in self.modules and not imp.external:
                    self.rel(
                        "IMPORTS", file_id, self.modules[imp.resolved_dir].id, fa.analyzer, line=imp.line
                    )
                elif imp.external and imp.package:
                    dep = self._dependency_for(imp.package, path)
                    if dep is not None:
                        self.rel("USES", file_id, dep.id, LINK, _NAME_MATCH_CONFIDENCE, line=imp.line)
            for call in sorted(fa.calls, key=lambda c: (c.line, c.caller or "", c.parts)):
                source = self.actor(path, call.caller)
                target: SymbolIR | None = None
                analyzer, confidence = fa.analyzer, call.confidence or _UNRESOLVED_CALL_CONFIDENCE
                if call.resolved_qualname:
                    target = self.symbol(call.resolved_path or path, call.resolved_qualname)
                if target is None and not call.resolved_qualname:
                    target = self.resolve_ref(path, call.parts, call.caller)
                    analyzer, confidence = RESOLVE, _FALLBACK_CONFIDENCE
                if target is not None:
                    self.rel("CALLS", source, target.id, analyzer, confidence, line=call.line)
                elif call.parts and call.parts[-1] in known and len(self.unresolved) < MAX_UNRESOLVED_CALLS:
                    name = ".".join(call.parts)
                    self.unresolved.append(
                        UnresolvedCallIR(source_id=source, name=name, path=path, line=call.line)
                    )
            for sym in self.sym_by_path.get(path, {}).values():
                for base in sym.bases:
                    parent = self.resolve_ref(path, base.split("."))
                    if parent is not None and parent.kind in ("class", "interface"):
                        self.rel("INHERITS", sym.id, parent.id, RESOLVE, 0.9)

    # --- endpoints & schemas -------------------------------------------------------------------------------

    def build_endpoints(self) -> None:
        for path, fa in self.fa.items():
            file_id = self.files[path].id
            prefixes = {r.var: r.prefix for r in fa.routers}
            for route in sorted(fa.routes, key=lambda r: (r.line, r.method, r.path)):
                method = (route.method or "ANY").upper()
                full = route.full_path or _join_route(prefixes.get(route.router or "", ""), route.path)
                full = normalize_route(full)
                name = f"{method} {full}"
                eid = self.eid("endpoint", path, name)
                if eid in self.endpoints:
                    continue
                handler = self._route_handler(path, route)
                same_file = handler is not None and handler.path == path
                self.endpoints[eid] = EndpointIR(
                    id=eid,
                    name=name,
                    method=method,
                    route=full,
                    handler_id=handler.id if handler else None,
                    framework=route.framework,
                    path=path,
                    start_line=route.line or None,
                    end_line=(handler.end_line if same_file and handler else route.line) or None,
                    provenance=_prov(fa.analyzer, 1.0 if route.full_path or not route.router else 0.9),
                )
                self.rel("EXPOSES", file_id, eid, fa.analyzer)
                if handler is not None:
                    self.rel("HANDLES", handler.id, eid, fa.analyzer)

    def _route_handler(self, path: str, route: RouteDef) -> SymbolIR | None:
        handler_path = route.handler_path or path
        if route.handler:
            found = self.symbol(handler_path, route.handler)
            if found is not None:
                return found
        if route.handler_ref:
            return self.resolve_ref(path, route.handler_ref)
        return None

    def build_schemas(self) -> None:
        for path, fa in self.fa.items():
            for sd in sorted(fa.schemas, key=lambda s: (s.start_line, s.name)):
                sid = self.eid("schema", path, sd.name)
                if sid in self.schemas:
                    continue
                sym = self.symbol(path, sd.symbol) or self.symbol(path, sd.name)
                fields: list[dict[str, Any]] = []
                for fd in sd.fields:
                    field: dict[str, Any] = {"name": fd.name}
                    if fd.type:
                        field["type"] = fd.type
                    if fd.relation:
                        field["relation"] = fd.relation
                    if fd.attributes:
                        field["attributes"] = sorted(fd.attributes)
                    fields.append(field)
                self.schemas[sid] = SchemaIR(
                    id=sid,
                    name=sd.name,
                    schema_kind=sd.schema_kind,
                    fields=fields,
                    symbol_id=sym.id if sym else None,
                    path=path,
                    start_line=sd.start_line,
                    end_line=max(sd.start_line, sd.end_line),
                    provenance=_prov(fa.analyzer),
                )
        tables = {
            self.eid("schema", p, sd.name): sd.table
            for p, fa in self.fa.items()
            for sd in fa.schemas
            if sd.table
        }
        for sc in self.schemas.values():
            self._schema_lookup[_norm_name(sc.name)].append(sc)
            table = tables.get(sc.id)
            if table and _norm_name(table) != _norm_name(sc.name):
                self._schema_lookup[_norm_name(table)].append(sc)

    def build_data_access(self) -> None:
        for path, fa in self.fa.items():
            for acc in sorted(fa.data_access, key=lambda a: (a.line, a.model, a.mode)):
                target: SchemaIR | None = None
                confidence = 1.0
                analyzer = fa.analyzer
                if acc.resolved_path and acc.resolved_name:
                    target = self.schemas.get(self.eid("schema", acc.resolved_path, acc.resolved_name))
                if target is None:
                    candidates = self._schema_lookup.get(_norm_name(acc.model), [])
                    if len(candidates) == 1:
                        target, confidence, analyzer = candidates[0], _NAME_MATCH_CONFIDENCE, LINK
                if target is None:
                    continue
                rel_type: RelType = "READS_FROM" if acc.mode == "read" else "WRITES_TO"
                source = self.actor(path, acc.caller)
                self.rel(rel_type, source, target.id, analyzer, confidence, line=acc.line, orm=acc.orm)

    # --- detection-backed technologies ---------------------------------------------------------------------

    def _evidence_anchor(self, d: Detected) -> tuple[str | None, int | None]:
        for ev in sorted(d.evidence, key=lambda e: (e.path, e.line)):
            if ev.path in self.files:
                return ev.path, ev.line or None
        return None, None

    def _link_evidence(self, d: Detected, target_id: str) -> None:
        for ev in sorted(d.evidence, key=lambda e: (e.path, e.line)):
            if ev.source in _EVIDENCE_LINK_SOURCES and ev.path in self.files:
                self.rel(
                    "USES", self.files[ev.path].id, target_id, DETECT, d.confidence, line=ev.line or None
                )

    def build_detected(self) -> None:
        det = self.analysis.detection
        for d in sorted(det.frameworks, key=lambda x: (x.technology or x.name, x.name)):
            key = _canon_tech(d.technology or d.name)
            if key in self.frameworks:
                continue
            path, line = self._evidence_anchor(d)
            self.frameworks[key] = FrameworkInfo(
                id=self.eid("framework", "", key),
                name=d.name,
                category=d.category or "other",
                version=d.version,
                path=path,
                start_line=line,
                provenance=_prov(DETECT, d.confidence),
            )
            self._link_evidence(d, self.frameworks[key].id)
        for d in sorted(det.datastores, key=lambda x: (x.technology or x.name, x.name)):
            key = _canon_tech(d.technology or d.name)
            store = self._datastore(key, d.name, d.category or "other", DETECT, d.confidence, d)
            self._link_evidence(d, store.id)
        for d in sorted(det.queues, key=lambda x: (x.technology or x.name, x.name)):
            key = _canon_tech(d.technology or d.name)
            queue = self._queue(key, d.name, DETECT, d.confidence, d)
            self._link_evidence(d, queue.id)
        for d in sorted(det.ai_components, key=lambda x: (x.category, x.technology or x.name, x.name)):
            path, line = self._evidence_anchor(d)
            comp = self._ai(d.category or "other", d.technology or d.name, path, line, DETECT, d.confidence)
            self._link_evidence(d, comp.id)

    def _datastore(
        self, tech: str, name: str, category: str, analyzer: str, confidence: float, d: Detected | None
    ) -> DataStoreIR:
        if tech not in self.data_stores:
            path, line = self._evidence_anchor(d) if d is not None else (None, None)
            self.data_stores[tech] = DataStoreIR(
                id=self.eid("datastore", "", tech),
                name=name,
                technology=tech,
                category=category,
                path=path,
                start_line=line,
                provenance=_prov(analyzer, confidence),
            )
        return self.data_stores[tech]

    def _queue(
        self, tech: str, name: str, analyzer: str, confidence: float = 1.0, d: Detected | None = None
    ) -> QueueIR:
        if tech not in self.queues:
            path, line = self._evidence_anchor(d) if d is not None else (None, None)
            self.queues[tech] = QueueIR(
                id=self.eid("queue", "", tech),
                name=name,
                technology=tech,
                path=path,
                start_line=line,
                provenance=_prov(analyzer, confidence),
            )
        return self.queues[tech]

    def _ai(
        self,
        kind: str,
        technology: str,
        path: str | None,
        line: int | None,
        analyzer: str,
        confidence: float = 1.0,
    ) -> AIComponentIR:
        tech = _canon_tech(technology) if technology else ""
        key = f"{kind}:{tech}"
        if key not in self.ai:
            label = kind.replace("_", " ")
            self.ai[key] = AIComponentIR(
                id=self.eid("ai", "", key),
                name=f"{technology} {label}".strip() if technology else label,
                component_kind=kind,
                technology=technology or None,
                path=path,
                start_line=line,
                provenance=_prov(analyzer, confidence),
            )
        return self.ai[key]

    def build_ai_hints(self) -> None:
        for path, fa in self.fa.items():
            for hint in sorted(fa.ai_hints, key=lambda h: (h.line, h.component_kind, h.technology)):
                comp = self._ai(hint.component_kind, hint.technology, path, hint.line or None, fa.analyzer)
                self.rel("USES", self.actor(path, hint.symbol), comp.id, fa.analyzer, line=hint.line or None)

    def build_tasks(self) -> None:
        for path, fa in self.fa.items():
            for task in sorted(fa.tasks, key=lambda t: (t.line, t.name)):
                queue = self._queue(_canon_tech(task.technology), task.technology, fa.analyzer, 1.0)
                actor = self.actor(path, task.symbol or task.name)
                self.rel("CONSUMES_FROM", actor, queue.id, fa.analyzer, task=task.name, line=task.line)
            for pub in sorted(fa.publishes, key=lambda p: (p.line, p.task)):
                queue = self._queue(_canon_tech(pub.technology), pub.technology, fa.analyzer, 1.0)
                actor = self.actor(path, pub.caller)
                self.rel("PUBLISHES_TO", actor, queue.id, fa.analyzer, task=pub.task, line=pub.line)

    # --- dependencies --------------------------------------------------------------------------------------

    def build_dependencies(self) -> None:
        for path, fa in self.fa.items():
            for dd in sorted(fa.dependencies, key=lambda d: (d.ecosystem, d.name, d.dev)):
                did = self.eid("dependency", path, f"{dd.ecosystem}:{dd.name}")
                if did in self.dependencies:
                    continue
                dep = DependencyIR(
                    id=did,
                    name=dd.name,
                    ecosystem=dd.ecosystem,
                    version=dd.version,
                    dev=dd.dev,
                    manifest_path=path,
                    path=path,
                    start_line=dd.line or None,
                    provenance=_prov(fa.analyzer),
                )
                self.dependencies[did] = dep
                self._deps_by_name[_norm_name(dd.name)].append(dep)
                self.rel("DEPENDS_ON", self.files[path].id, did, fa.analyzer)

    def _dependency_for(self, package: str, importer: str) -> DependencyIR | None:
        cands = self._deps_by_name.get(_norm_name(package), [])
        if not cands:
            return None
        # Closest manifest above the importing file wins; otherwise the first by manifest path.
        scoped = [d for d in cands if _under(importer, _dirname(d.manifest_path))]
        pool = scoped or cands
        return sorted(pool, key=lambda d: (-len(_dirname(d.manifest_path)), d.manifest_path, d.id))[0]

    # --- infrastructure & services -------------------------------------------------------------------------

    def build_infra(self) -> None:
        for path, fa in self.fa.items():
            kinds: set[str] = set()
            for idef in sorted(fa.infra, key=lambda i: (i.start_line, i.resource_kind, i.name)):
                iid = self.eid("infra", path, f"{idef.resource_kind}:{idef.name}")
                kinds.add(idef.resource_kind)
                if iid in self.infra:
                    continue
                self.infra[iid] = InfraResourceIR(
                    id=iid,
                    name=idef.name,
                    resource_kind=idef.resource_kind,
                    provider=idef.provider,
                    details=dict(idef.details),
                    path=path,
                    start_line=idef.start_line or None,
                    end_line=idef.end_line or None,
                    provenance=_prov(fa.analyzer),
                )
            if _is_dockerfile(path) and "dockerfile" not in kinds:
                details: dict[str, Any] = {"entry_command": fa.entry_command} if fa.entry_command else {}
                self._synthetic_infra(path, "dockerfile", "docker", details, fa.analyzer)
            if fa.compose_services and "compose" not in kinds:
                names = sorted(s.name for s in fa.compose_services)
                self._synthetic_infra(path, "compose", "docker", {"services": names}, fa.analyzer)
        # Files the inventory classified as containers but no analyzer handled (still worth a resource).
        for path, f in self.files.items():
            if _is_dockerfile(path) and path not in self.fa and f.is_text:
                self._synthetic_infra(path, "dockerfile", "docker", {}, "inventory")

    def _synthetic_infra(
        self, path: str, kind: str, provider: str, details: dict[str, Any], analyzer: str
    ) -> InfraResourceIR:
        iid = self.eid("infra", path, f"{kind}:{path}")
        if iid not in self.infra:
            self.infra[iid] = InfraResourceIR(
                id=iid,
                name=path,
                resource_kind=kind,
                provider=provider,
                details=details,
                path=path,
                start_line=1,
                provenance=_prov(analyzer),
            )
        return self.infra[iid]

    def _infra_of_file(self, path: str | None, kind: str) -> InfraResourceIR | None:
        if not path:
            return None
        for res in sorted(self.infra.values(), key=lambda r: r.id):
            if res.path == path and res.resource_kind == kind:
                return res
        return None

    def _resolve_command(self, command: str | None, root: str) -> FileIR | None:
        """Map `uvicorn app.main:app`, `python -m pkg.cli`, `node dist/server.js` to a repository file."""
        if not command:
            return None
        candidates: list[str] = []
        for m in _PY_MODULE_TARGET.finditer(command):
            candidates.append(m.group(1).replace(".", "/") + ".py")
        for m in _PY_DASH_M.finditer(command):
            mod = m.group(1).replace(".", "/")
            candidates += [f"{mod}.py", f"{mod}/__main__.py", f"{mod}/__init__.py"]
        for m in _PATH_TARGET.finditer(command):
            rel = m.group(0).removeprefix("./")
            candidates.append(rel)
            for ext in (".js", ".mjs", ".cjs"):  # compiled output → TypeScript source
                if rel.endswith(ext):
                    stem = rel.removesuffix(ext)
                    for src in (stem, re.sub(r"^(dist|build|out|lib)/", "src/", stem)):
                        candidates += [src + ".ts", src + ".js"]
        for m in _DOTTED.finditer(command):  # `arq app.worker.WorkerSettings`, `celery -A proj.celery`
            parts = m.group(1).split(".")
            candidates += ["/".join(parts[:n]) + ".py" for n in range(len(parts), 0, -1)]
        for cand in candidates:
            direct = _join(root, cand)
            if direct in self.files:
                return self.files[direct]
            matches = sorted(
                p for p in self.files if _under(p, root) and (p == cand or p.endswith("/" + cand))
            )
            if len(matches) == 1:
                return self.files[matches[0]]
        return None

    def _app_files(self, root: str) -> list[str]:
        return [p for p, fa in self.fa.items() if fa.app_objects and _under(p, root)]

    def _entry_for(self, command: str | None, root: str) -> FileIR | None:
        """The file a container command runs: explicit reference, else the only matching app object."""
        entry = self._resolve_command(command, root)
        if entry is not None:
            return entry
        apps = self._app_files(root)
        if command:
            cmd = f"{command.lower()} "
            wants_worker = any(w in cmd for w in _WORKER_WORDS)
            apps = [
                p
                for p in apps
                if (self._object_kind(sorted(self.fa[p].app_objects)[0]) == "worker") == wants_worker
            ]
        return self.files[apps[0]] if len(apps) == 1 else None

    def build_services(self) -> None:
        claimed_entry: set[str] = set()
        referenced_dockerfiles: set[str] = set()
        compose_by_file: dict[str, dict[str, ServiceIR]] = defaultdict(dict)

        # 1. compose services
        for path, fa in self.fa.items():
            compose_infra = self._infra_of_file(path, "compose")
            for cs in sorted(fa.compose_services, key=lambda s: (s.line, s.name)):
                svc = self._compose_service(path, cs, compose_infra, referenced_dockerfiles)
                compose_by_file[path][cs.name] = svc
                if svc.entrypoint_file_id:
                    claimed_entry.add(svc.entrypoint_file_id)
        for services in compose_by_file.values():
            for svc in services.values():
                for dep in svc.depends_on:
                    target = services.get(dep)
                    if target is not None:
                        self.rel("DEPENDS_ON", svc.id, target.id, BUILDER)

        # 2. Dockerfiles no compose service builds
        for path, fa in self.fa.items():
            if not _is_dockerfile(path) or path in referenced_dockerfiles:
                continue
            root = _dirname(path)
            entry = self._entry_for(fa.entry_command, root)
            name = _basename(root) if root else self.identity.name
            kind = self._kind_for(fa.entry_command, entry, None, bool(fa.entry_command))
            sid = self.eid("service", path, "")
            self.services[sid] = ServiceIR(
                id=sid,
                name=self._unique_service_name(name, path),
                service_kind=kind,
                entrypoint_file_id=entry.id if entry else None,
                path=path,
                start_line=1,
                provenance=_prov(fa.analyzer),
            )
            if entry is not None:
                claimed_entry.add(entry.id)
            docker_infra = self._infra_of_file(path, "dockerfile")
            if docker_infra is not None:
                self.rel("CONFIGURES", docker_infra.id, sid, BUILDER)

        # 3. application entrypoints (app objects) not already run by a compose/Dockerfile service
        for path, fa in self.fa.items():
            if not fa.app_objects or self.files[path].id in claimed_entry:
                continue
            obj = sorted(fa.app_objects)[0]
            top = path.split("/", 1)[0] if "/" in path else ""
            base = top or self.identity.name
            sid = self.eid("service", path, obj)
            self.services[sid] = ServiceIR(
                id=sid,
                name=self._unique_service_name(base, _dirname(path) or path),
                service_kind=self._object_kind(obj),
                entrypoint_file_id=self.files[path].id,
                path=path,
                provenance=_prov(fa.analyzer, 0.9),
            )

        self._link_services()

    def _compose_service(
        self,
        path: str,
        cs: ComposeServiceDef,
        compose_infra: InfraResourceIR | None,
        referenced_dockerfiles: set[str],
    ) -> ServiceIR:
        fa = self.fa[path]
        compose_dir = _dirname(path)
        context = _join(compose_dir, cs.build_context) if cs.build_context is not None else None
        dockerfile: str | None = None
        if cs.dockerfile:
            for cand in (cs.dockerfile, _join(context or compose_dir, cs.dockerfile)):
                if cand in self.files:
                    dockerfile = cand
                    break
        elif context is not None and _join(context, "Dockerfile") in self.files:
            dockerfile = _join(context, "Dockerfile")
        if dockerfile:
            referenced_dockerfiles.add(dockerfile)
        root = context if context is not None else (_dirname(dockerfile) if dockerfile else None)
        entry: FileIR | None = None
        if root is not None:
            docker_fa = self.fa.get(dockerfile) if dockerfile else None
            # A compose `command` overrides the image's CMD/ENTRYPOINT.
            entry = self._entry_for(cs.command or (docker_fa.entry_command if docker_fa else None), root)
        rule = _image_rule(cs.image)
        kind = self._kind_for(cs.command, entry, rule, bool(cs.ports), cs.image, root)
        sid = self.eid("service", path, cs.name)
        svc = ServiceIR(
            id=sid,
            name=cs.name,
            service_kind=kind,
            image=cs.image,
            entrypoint_file_id=entry.id if entry else None,
            ports=list(cs.ports),
            depends_on=sorted(set(cs.depends_on)),
            path=path,
            start_line=cs.line or None,
            provenance=_prov(fa.analyzer),
        )
        self.services[sid] = svc
        if compose_infra is not None:
            self.rel("CONFIGURES", compose_infra.id, sid, BUILDER)
        docker_infra = self._infra_of_file(dockerfile, "dockerfile")
        if docker_infra is not None:
            self.rel("CONFIGURES", docker_infra.id, sid, BUILDER)
        if rule is not None:
            rkind, tech, category = rule
            if rkind == "datastore":
                self._datastore(tech, tech, category, LINK, 0.9, None)
            else:
                self._queue(tech, tech, LINK, 0.9)
        return svc

    def _kind_for(
        self,
        command: str | None,
        entry: FileIR | None,
        rule: tuple[str, str, str] | None,
        has_ports: bool,
        image: str | None = None,
        root: str | None = None,
    ) -> str:
        if rule is not None:
            kind, _, category = rule
            if kind == "queue":
                return "other"
            return "cache" if category in ("key_value", "cache") else "database"
        img = (image or "").lower().rsplit("/", 1)[-1]
        if any(img.startswith(p) for p in _PROXY_IMAGES):
            return "proxy"
        cmd = f"{(command or '').lower()} "
        if any(w in cmd for w in _CRON_WORDS):
            return "cron"
        if any(w in cmd for w in _WORKER_WORDS):
            return "worker"
        if entry is not None:
            fa = self.fa.get(entry.path)
            if fa is not None and fa.app_objects:
                return self._object_kind(sorted(fa.app_objects)[0])
        if root is not None and any(part.lower() in _FRONTEND_DIRS for part in root.split("/") if part):
            return "web"
        return "api" if has_ports else "other"

    @staticmethod
    def _object_kind(obj: str) -> str:
        low = obj.lower()
        if "worker" in low or low in {"celery", "arq", "rq", "dramatiq", "huey", "sidekiq", "bullmq"}:
            return "worker"
        if "cron" in low or "scheduler" in low or "beat" in low:
            return "cron"
        if low in _WEB_OBJECTS:
            return "web"
        if low in _CLI_OBJECTS:
            return "other"
        return "api"

    def _unique_service_name(self, base: str, fallback: str) -> str:
        taken = {s.name for s in self.services.values()}
        if base not in taken:
            return base
        if fallback not in taken:
            return fallback
        n = 2
        while f"{base}-{n}" in taken:
            n += 1
        return f"{base}-{n}"

    def _file_by_id(self, file_id: str | None) -> FileIR | None:
        if not self._files_by_id:
            self._files_by_id = {f.id: f for f in self.files.values()}
        return self._files_by_id.get(file_id) if file_id else None

    def _service_root(self, svc: ServiceIR) -> str | None:
        if svc.path and _is_dockerfile(svc.path):
            return _dirname(svc.path)
        entry = self._file_by_id(svc.entrypoint_file_id)
        if svc.path and svc.path in self.fa and self.fa[svc.path].compose_services:
            cs = next((c for c in self.fa[svc.path].compose_services if c.name == svc.name), None)
            if cs is not None and cs.build_context is not None:
                return _join(_dirname(svc.path), cs.build_context)
        if entry is None:
            return None
        return entry.path.split("/", 1)[0] if "/" in entry.path else ""

    def _link_services(self) -> None:
        roots = {
            sid: root
            for sid, svc in sorted(self.services.items())
            if svc.service_kind in ("api", "web") and (root := self._service_root(svc)) is not None
        }
        # EXPOSES: each endpoint is exposed by the service with the most specific root containing it.
        for eid, ep in sorted(self.endpoints.items()):
            owners = [(len(r), sid) for sid, r in roots.items() if ep.path and _under(ep.path, r)]
            if owners:
                best = max(owners)[0]
                for length, sid in sorted(owners):
                    if length == best:
                        self.rel("EXPOSES", sid, eid, LINK, _ROOT_MATCH_CONFIDENCE)
        # Workers consume from the queue technology their app object names (or the only queue there is).
        for sid, svc in sorted(self.services.items()):
            if svc.service_kind != "worker":
                continue
            fa = self.fa.get(entry.path if (entry := self._file_by_id(svc.entrypoint_file_id)) else "")
            objs = [o.lower() for o in (fa.app_objects if fa else [])]
            matched = [q for tech, q in self.queues.items() if any(o.startswith(tech) for o in objs)]
            if not matched and len(self.queues) == 1:
                matched = list(self.queues.values())
            for q in matched:
                self.rel("CONSUMES_FROM", sid, q.id, LINK, _NAME_MATCH_CONFIDENCE)
        # Kubernetes / Terraform / other deploy resources → services by name or image.
        by_name = {_norm_name(s.name): s for s in self.services.values()}
        by_image = {s.image.split(":", 1)[0]: s for s in self.services.values() if s.image}
        for res in sorted(self.infra.values(), key=lambda r: r.id):
            if res.resource_kind in ("dockerfile", "compose"):
                continue
            target = by_name.get(_norm_name(res.name))
            images = res.details.get("images") or ([res.details["image"]] if "image" in res.details else [])
            if target is None and isinstance(images, list):
                for image in images:
                    if isinstance(image, str) and image.split(":", 1)[0] in by_image:
                        target = by_image[image.split(":", 1)[0]]
                        break
            if target is not None:
                self.rel("DEPLOYS_TO", res.id, target.id, LINK, _NAME_MATCH_CONFIDENCE)

    # --- assembly ------------------------------------------------------------------------------------------

    def languages(self) -> list[LanguageInfo]:
        agg: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        for f in self.inv_files:
            if f.language and f.is_text:
                row = agg[f.language]
                row[0] += 1
                row[1] += f.lines
                row[2] += f.size
        return sorted(
            (LanguageInfo(name=k, files=v[0], lines=v[1], bytes=v[2]) for k, v in agg.items()),
            key=lambda li: (-li.lines, -li.files, li.name),
        )

    def build(self) -> ProjectIR:
        self.build_files_and_modules()
        self.build_symbols()
        self.build_dependencies()
        self.build_code_relationships()
        self.build_endpoints()
        self.build_schemas()
        self.build_data_access()
        self.build_detected()
        self.build_ai_hints()
        self.build_tasks()
        self.build_infra()
        self.build_services()

        for module in self.modules.values():
            module.file_ids.sort()
        entities: dict[str, Entity] = {}
        groups: list[Iterable[Entity]] = [
            self.files.values(),
            self.modules.values(),
            self.symbols.values(),
            self.endpoints.values(),
            self.schemas.values(),
            self.data_stores.values(),
            self.services.values(),
            self.queues.values(),
            self.infra.values(),
            self.ai.values(),
            self.frameworks.values(),
            self.dependencies.values(),
        ]
        for group in groups:
            for e in group:
                entities[e.id] = e
        relationships = sorted(
            (r for r in self.rels.values() if r.source_id in entities and r.target_id in entities),
            key=lambda r: (r.type, r.source_id, r.target_id),
        )
        dangling = len(self.rels) - len(relationships)

        identity = self.identity.model_copy(deep=True)
        if not identity.categories:
            identity.categories = [c.category for c in self.analysis.detection.categories]
        if not identity.description and self.analysis.description:
            identity.description = self.analysis.description

        ir = ProjectIR(
            analysis_version=self.analysis_version,
            project=identity,
            languages=self.languages(),
            frameworks=sorted(self.frameworks.values(), key=lambda x: (x.name, x.id)),
            files=sorted(self.files.values(), key=lambda x: x.path),
            symbols=sorted(
                self.symbols.values(), key=lambda s: (s.path or "", s.start_line or 0, s.qualname, s.id)
            ),
            modules=sorted(self.modules.values(), key=lambda x: (x.path, x.id)),
            endpoints=sorted(self.endpoints.values(), key=lambda e: (e.route, e.method, e.path or "", e.id)),
            schemas=sorted(self.schemas.values(), key=lambda s: (s.path or "", s.start_line or 0, s.id)),
            data_stores=sorted(self.data_stores.values(), key=lambda x: (x.technology, x.id)),
            services=sorted(self.services.values(), key=lambda x: (x.name, x.id)),
            queues=sorted(self.queues.values(), key=lambda x: (x.technology, x.id)),
            infrastructure=sorted(self.infra.values(), key=lambda x: (x.path or "", x.start_line or 0, x.id)),
            ai_components=sorted(self.ai.values(), key=lambda x: (x.component_kind, x.name, x.id)),
            dependencies=sorted(
                self.dependencies.values(), key=lambda x: (x.manifest_path, x.ecosystem, x.name, x.id)
            ),
            relationships=relationships,
            unresolved_calls=sorted(
                (u for u in self.unresolved if u.source_id in entities),
                key=lambda u: (u.path, u.line, u.name, u.source_id),
            ),
        )
        ir.stats = self.stats(ir, dangling)
        log.info(
            "project_ir.built",
            project_id=self.pid,
            files=len(ir.files),
            symbols=len(ir.symbols),
            endpoints=len(ir.endpoints),
            relationships=len(ir.relationships),
            dangling_dropped=dangling,
            ignored_analyses=len(self.ignored_analyses),
        )
        return ir

    def stats(self, ir: ProjectIR, dangling: int) -> dict[str, Any]:
        tiers = self.inventory.tier_counts()
        code_files = [f for f in ir.files if _is_code(self.inv_by_path[f.path], self.fa.get(f.path))]
        rel_counts = Counter(r.type for r in ir.relationships)
        return {
            "files_total": len(self.inventory.files),
            "files_indexed": len(ir.files),
            "files_by_tier": {str(k): v for k, v in sorted(tiers.items())},
            "files_by_role": dict(sorted(Counter(f.role for f in ir.files).items())),
            "code_files": len(code_files),
            "lines_total": sum(f.lines for f in ir.files),
            "code_lines": sum(f.lines for f in code_files),
            "bytes_total": self.inventory.total_bytes or sum(f.size for f in self.inventory.files),
            "languages": len(ir.languages),
            "code_languages": len({(f.language or "").lower() for f in code_files if f.language}),
            "entities": {
                "files": len(ir.files),
                "modules": len(ir.modules),
                "symbols": len(ir.symbols),
                "endpoints": len(ir.endpoints),
                "schemas": len(ir.schemas),
                "data_stores": len(ir.data_stores),
                "services": len(ir.services),
                "queues": len(ir.queues),
                "infrastructure": len(ir.infrastructure),
                "ai_components": len(ir.ai_components),
                "frameworks": len(ir.frameworks),
                "dependencies": len(ir.dependencies),
            },
            "symbols_by_kind": dict(sorted(Counter(s.kind for s in ir.symbols).items())),
            "relationships": len(ir.relationships),
            "relationships_by_type": dict(sorted(rel_counts.items())),
            "endpoints_with_handler": sum(1 for e in ir.endpoints if e.handler_id),
            "dropped_dangling_relationships": dangling,
            "dropped_self_loops": self.dropped_self_loops,
            "parse_error_files": len(self.analysis.errors),
            "flagged_files": len(self.analysis.flagged),
            "skipped_files": len(self.inventory.skipped),
            "ignored_analyses": len(self.ignored_analyses),
            "rules_version": self.analysis.detection.rules_version,
        }


# ---- public API --------------------------------------------------------------------------------------------


def build_ir(
    identity: ProjectIdentity,
    inventory: Inventory,
    analysis: RepositoryAnalysis,
    *,
    analysis_version: str,
) -> ProjectIR:
    """Normalize one repository's inventory + analysis into a `ProjectIR` (pure, deterministic).

    Tier-4 inventory files (vendor / generated / binary noise) are excluded; analyses for paths that are not
    indexable inventory files are ignored. Every relationship endpoint exists in the returned IR.
    """
    if analysis.project_id and analysis.project_id != identity.project_id:
        raise UnfoldError(
            "Repository analysis belongs to a different project.",
            code=ErrorCode.ANALYSIS_STAGE_FAILED,
            detail={"identity": identity.project_id, "analysis": analysis.project_id},
        )
    return _Builder(identity, inventory, analysis, analysis_version).build()


def validate_ir(ir: ProjectIR) -> list[str]:
    """Referential-integrity problems of an IR (empty list = valid). Used by tests and before persisting."""
    problems: list[str] = []
    seen: dict[str, str] = {}
    groups: dict[str, list[Any]] = {
        "frameworks": ir.frameworks,
        "files": ir.files,
        "symbols": ir.symbols,
        "modules": ir.modules,
        "endpoints": ir.endpoints,
        "schemas": ir.schemas,
        "data_stores": ir.data_stores,
        "services": ir.services,
        "queues": ir.queues,
        "infrastructure": ir.infrastructure,
        "ai_components": ir.ai_components,
        "dependencies": ir.dependencies,
    }
    for group, items in groups.items():
        for e in items:
            if e.id in seen:
                problems.append(f"duplicate id {e.id} in {group} (also in {seen[e.id]})")
            seen[e.id] = group
    files = {f.id for f in ir.files}
    symbols = {s.id for s in ir.symbols}
    modules = {m.id for m in ir.modules}

    def check(ref: str | None, pool: set[str], what: str) -> None:
        if ref is not None and ref not in pool:
            problems.append(f"{what} -> missing {ref}")

    for f in ir.files:
        check(f.module_id, modules, f"file {f.path} module_id")
    for m in ir.modules:
        for fid in m.file_ids:
            check(fid, files, f"module {m.path or '(root)'} file_ids")
    for s in ir.symbols:
        check(s.file_id, files, f"symbol {s.qualname} file_id")
        check(s.parent_id, symbols, f"symbol {s.qualname} parent_id")
    for e in ir.endpoints:
        check(e.handler_id, symbols, f"endpoint {e.name} handler_id")
    for sc in ir.schemas:
        check(sc.symbol_id, symbols, f"schema {sc.name} symbol_id")
    for sv in ir.services:
        check(sv.entrypoint_file_id, files, f"service {sv.name} entrypoint_file_id")
    keys: set[tuple[str, str, str]] = set()
    for r in ir.relationships:
        key = (r.type, r.source_id, r.target_id)
        if key in keys:
            problems.append(f"duplicate relationship {r.type} {r.source_id} -> {r.target_id}")
        keys.add(key)
        if r.source_id not in seen:
            problems.append(f"{r.type} source missing {r.source_id}")
        if r.target_id not in seen:
            problems.append(f"{r.type} target missing {r.target_id}")
    return problems
