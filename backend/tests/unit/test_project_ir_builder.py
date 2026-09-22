"""Project IR builder: hand-built analysis inputs (independent of the analyzers) → IR consumed by chunking,
graph, communities and diagrams."""

from __future__ import annotations

from typing import Any

import orjson
import pytest

from unfold.agents.models import WorkflowFinding, WorkflowStep
from unfold.core.errors import UnfoldError
from unfold.core.ids import content_hash, entity_id
from unfold.diagrams.builders import build_all
from unfold.diagrams.inputs import SubsystemView
from unfold.graph import communities
from unfold.graph.builder import _derived_relationships, _ir_nodes, project_uid
from unfold.indexing.chunking import chunk_project
from unfold.project_ir import build_ir, validate_ir
from unfold.project_ir.builder import normalize_route
from unfold.project_ir.schema import ProjectIdentity, ProjectIR
from unfold.project_ir.snapshot import decode_ir, encode_ir
from unfold.repo_intel.models import (
    AIHint,
    Binding,
    CallRef,
    CategoryScore,
    ComposeServiceDef,
    DataAccess,
    DependencyDef,
    Detected,
    DetectionResult,
    DocSection,
    Evidence,
    FieldDef,
    FileAnalysis,
    ImportRef,
    InfraDef,
    Inventory,
    InventoryFile,
    PublishRef,
    RepositoryAnalysis,
    RouteDef,
    RouterDef,
    SchemaDef,
    SymbolDef,
    TaskDef,
)

PID = "22222222-2222-2222-2222-222222222222"

# path -> (language, lines, role, tier)
FILES: dict[str, tuple[str | None, int, str, int]] = {
    "README.md": ("markdown", 12, "docs", 3),
    "docker-compose.yml": ("yaml", 30, "container", 0),
    ".github/workflows/ci.yml": ("yaml", 20, "ci", 2),
    "k8s/deploy.yaml": ("yaml", 25, "iac", 2),
    "backend/Dockerfile": (None, 10, "container", 0),
    "backend/pyproject.toml": ("toml", 20, "manifest", 0),
    "backend/app/__init__.py": ("python", 1, "source", 2),
    "backend/app/main.py": ("python", 20, "entrypoint", 0),
    "backend/app/models.py": ("python", 30, "model_def", 1),
    "backend/app/worker.py": ("python", 20, "service", 1),
    "backend/app/api/__init__.py": ("python", 1, "source", 2),
    "backend/app/api/routes.py": ("python", 40, "route", 1),
    "backend/app/services/__init__.py": ("python", 1, "source", 2),
    "backend/app/services/base.py": ("python", 10, "service", 2),
    "backend/app/services/orders.py": ("python", 50, "service", 1),
    "backend/tests/test_orders.py": ("python", 15, "test", 3),
    "frontend/src/App.tsx": ("typescript", 30, "component", 2),
    "frontend/src/api.ts": ("typescript", 20, "source", 2),
    "vendor/lib.js": ("javascript", 500, "source", 4),
}


def _inventory() -> Inventory:
    files = []
    for path, (lang, lines, role, tier) in sorted(FILES.items()):
        base = path.rsplit("/", 1)[-1]
        files.append(
            InventoryFile(
                path=path,
                size=lines * 30,
                ext=base.rsplit(".", 1)[-1].lower() if "." in base else "",
                language=lang,
                sha256=content_hash(path),
                lines=lines,
                role=role,
                tier=tier,  # type: ignore[arg-type]
                vendor=tier == 4,
            )
        )
    return Inventory(files=files, total_bytes=sum(f.size for f in files), skipped={"bin/x": "symlink"})


def _sym(qual: str, kind: str, start: int, end: int, **kw: Any) -> SymbolDef:
    return SymbolDef(
        name=qual.rsplit(".", 1)[-1], qualname=qual, kind=kind, start_line=start, end_line=end, **kw
    )  # type: ignore[arg-type]


def _analysis() -> RepositoryAnalysis:
    py = "python"
    files: dict[str, FileAnalysis] = {
        "README.md": FileAnalysis(
            path="README.md",
            language="markdown",
            analyzer="docs",
            sections=[DocSection(title="Shop", level=1, start_line=1, end_line=12)],
        ),
        "backend/app/main.py": FileAnalysis(
            path="backend/app/main.py",
            language=py,
            analyzer="python",
            docstring="FastAPI application factory.",
            symbols=[_sym("create_app", "function", 5, 18)],
            imports=[
                ImportRef(
                    module="app.api.routes",
                    names=["router"],
                    line=2,
                    resolved_path="backend/app/api/routes.py",
                    external=False,
                ),
                ImportRef(module="fastapi", names=["FastAPI"], line=1, package="fastapi"),
            ],
            app_objects=["fastapi"],
        ),
        "backend/app/api/routes.py": FileAnalysis(
            path="backend/app/api/routes.py",
            language=py,
            analyzer="python",
            symbols=[
                _sym("list_orders", "function", 8, 14, decorators=["router.get"]),
                _sym("create_order", "function", 16, 25, decorators=["router.post"]),
                _sym("health", "function", 27, 30),
            ],
            imports=[
                ImportRef(
                    module="app.services.orders",
                    names=["OrderService"],
                    line=3,
                    resolved_path="backend/app/services/orders.py",
                    external=False,
                ),
            ],
            bindings={
                "OrderService": Binding(
                    kind="symbol", path="backend/app/services/orders.py", attr="OrderService"
                )
            },
            routers=[RouterDef(var="router", kind="apirouter", prefix="/orders", line=5)],
            routes=[
                RouteDef(
                    method="get",
                    path="/",
                    line=7,
                    framework="fastapi",
                    router="router",
                    handler="list_orders",
                    full_path="/api/orders",
                ),
                RouteDef(
                    method="POST",
                    path="/{order_id}",
                    line=15,
                    framework="fastapi",
                    router="router",
                    handler="create_order",
                ),
                RouteDef(method="GET", path="/health", line=26, framework="fastapi", handler="health"),
                RouteDef(
                    method="GET", path="/health", line=26, framework="fastapi", handler="health"
                ),  # duplicate
            ],
            calls=[
                # Resolved by the analyzer.
                CallRef(
                    caller="list_orders",
                    parts=["OrderService", "fetch"],
                    line=10,
                    resolved_path="backend/app/services/orders.py",
                    resolved_qualname="OrderService.fetch",
                    confidence=0.95,
                ),
                # Unresolved: builder falls back to bindings.
                CallRef(caller="create_order", parts=["OrderService", "create"], line=18),
                # Recursion (self-loop) is dropped.
                CallRef(caller="health", parts=["health"], line=28),
                # Nowhere to resolve to.
                CallRef(caller="health", parts=["json", "dumps"], line=29),
            ],
            data_access=[
                DataAccess(caller="list_orders", model="orders", mode="read", line=11, orm="sqlalchemy")
            ],
        ),
        "backend/app/services/base.py": FileAnalysis(
            path="backend/app/services/base.py",
            language=py,
            analyzer="python",
            symbols=[_sym("BaseService", "class", 1, 8)],
        ),
        "backend/app/services/orders.py": FileAnalysis(
            path="backend/app/services/orders.py",
            language=py,
            analyzer="python",
            symbols=[
                _sym("OrderService", "class", 5, 45, bases=["BaseService"]),
                _sym("OrderService.fetch", "method", 8, 15, parent="OrderService"),
                _sym("OrderService.create", "method", 17, 30, parent="OrderService"),
                _sym("OrderService.create._validate", "function", 18, 20),  # nested, parent inferred
                _sym("OrderService.summarize", "method", 32, 44, parent="OrderService"),
                _sym("OrderService", "class", 47, 50),  # redefinition is ignored
            ],
            imports=[
                ImportRef(
                    module="app.services.base",
                    names=["BaseService"],
                    line=1,
                    resolved_path="backend/app/services/base.py",
                    external=False,
                ),
                ImportRef(
                    module="app.models",
                    names=["Order"],
                    line=2,
                    resolved_path="backend/app/models.py",
                    external=False,
                ),
                ImportRef(module="openai", line=3, package="openai"),
            ],
            bindings={
                "BaseService": Binding(kind="symbol", path="backend/app/services/base.py", attr="BaseService")
            },
            calls=[CallRef(caller="OrderService.create", parts=["self", "fetch"], line=21)],
            data_access=[
                DataAccess(
                    caller="OrderService.create",
                    model="Order",
                    mode="write",
                    line=22,
                    orm="sqlalchemy",
                    resolved_path="backend/app/models.py",
                    resolved_name="Order",
                ),
                DataAccess(
                    caller="OrderService.fetch", model="Unknown", mode="read", line=9, orm="sqlalchemy"
                ),
            ],
            ai_hints=[
                AIHint(
                    component_kind="llm_client", technology="openai", line=33, symbol="OrderService.summarize"
                )
            ],
            publishes=[
                PublishRef(caller="OrderService.create", task="send_email", technology="arq", line=25)
            ],
        ),
        "backend/app/models.py": FileAnalysis(
            path="backend/app/models.py",
            language=py,
            analyzer="python",
            symbols=[_sym("Order", "class", 3, 15), _sym("Customer", "class", 17, 28)],
            imports=[ImportRef(module="sqlalchemy", line=1, package="sqlalchemy")],
            schemas=[
                SchemaDef(
                    name="Order",
                    schema_kind="orm_model",
                    start_line=3,
                    end_line=15,
                    symbol="Order",
                    table="orders",
                    fields=[
                        FieldDef(name="id", type="int", attributes=["primary_key"]),
                        FieldDef(name="customer_id", type="int", relation="Customer", attributes=["fk"]),
                    ],
                ),
                SchemaDef(
                    name="Customer",
                    schema_kind="orm_model",
                    start_line=17,
                    end_line=28,
                    symbol="Customer",
                    fields=[FieldDef(name="id", type="int")],
                ),
            ],
        ),
        "backend/app/worker.py": FileAnalysis(
            path="backend/app/worker.py",
            language=py,
            analyzer="python",
            symbols=[_sym("send_email", "function", 3, 10), _sym("WorkerSettings", "class", 12, 18)],
            tasks=[TaskDef(name="send_email", technology="arq", line=3, symbol="send_email")],
            app_objects=["arq_worker"],
        ),
        "backend/tests/test_orders.py": FileAnalysis(
            path="backend/tests/test_orders.py",
            language=py,
            analyzer="python",
            symbols=[_sym("test_list", "function", 1, 10)],
            imports=[
                ImportRef(
                    module="app.api.routes", line=1, resolved_path="backend/app/api/routes.py", external=False
                )
            ],
        ),
        "backend/pyproject.toml": FileAnalysis(
            path="backend/pyproject.toml",
            language="toml",
            analyzer="manifest",
            dependencies=[
                DependencyDef(name="fastapi", version=">=0.110", ecosystem="pypi", line=5),
                DependencyDef(name="openai", ecosystem="pypi", line=6),
                DependencyDef(name="arq", ecosystem="pypi", line=7),
                DependencyDef(name="SQLAlchemy", ecosystem="pypi", line=8),
                DependencyDef(name="pytest", ecosystem="pypi", dev=True, line=12),
            ],
        ),
        "backend/Dockerfile": FileAnalysis(
            path="backend/Dockerfile",
            analyzer="infra",
            entry_command="uvicorn app.main:app --host 0.0.0.0 --port 8000",
        ),
        "docker-compose.yml": FileAnalysis(
            path="docker-compose.yml",
            language="yaml",
            analyzer="infra",
            compose_services=[
                ComposeServiceDef(
                    name="api",
                    build_context="./backend",
                    ports=["8000:8000"],
                    depends_on=["db", "redis", "ghost"],
                    line=2,
                ),
                ComposeServiceDef(
                    name="worker",
                    build_context="backend",
                    command="arq app.worker.WorkerSettings",
                    depends_on=["redis"],
                    line=10,
                ),
                ComposeServiceDef(name="db", image="postgres:16", line=16),
                ComposeServiceDef(name="redis", image="redis:7-alpine", line=22),
            ],
        ),
        ".github/workflows/ci.yml": FileAnalysis(
            path=".github/workflows/ci.yml",
            language="yaml",
            analyzer="infra",
            infra=[
                InfraDef(
                    resource_kind="ci_workflow",
                    name="ci",
                    provider="github_actions",
                    start_line=1,
                    end_line=20,
                )
            ],
        ),
        "k8s/deploy.yaml": FileAnalysis(
            path="k8s/deploy.yaml",
            language="yaml",
            analyzer="infra",
            infra=[
                InfraDef(
                    resource_kind="k8s_deployment",
                    name="api",
                    provider="kubernetes",
                    start_line=1,
                    end_line=25,
                    details={"images": ["shop/api:1"]},
                )
            ],
        ),
        "frontend/src/App.tsx": FileAnalysis(
            path="frontend/src/App.tsx",
            language="typescript",
            analyzer="tree_sitter",
            symbols=[_sym("App", "component", 3, 28, exported=True)],
            imports=[ImportRef(module="./api", line=1, resolved_path="frontend/src/api.ts", external=False)],
            calls=[
                CallRef(
                    caller="App",
                    parts=["fetchOrders"],
                    line=10,
                    resolved_path="frontend/src/api.ts",
                    resolved_qualname="fetchOrders",
                    confidence=0.9,
                )
            ],
        ),
        "frontend/src/api.ts": FileAnalysis(
            path="frontend/src/api.ts",
            language="typescript",
            analyzer="tree_sitter",
            symbols=[_sym("fetchOrders", "function", 1, 10, exported=True)],
        ),
        # Tier-4 / unknown files: ignored by the builder.
        "vendor/lib.js": FileAnalysis(
            path="vendor/lib.js", analyzer="tree_sitter", symbols=[_sym("x", "function", 1, 2)]
        ),
        "does/not/exist.py": FileAnalysis(path="does/not/exist.py", analyzer="python"),
    }
    detection = DetectionResult(
        rules_version="r1",
        frameworks=[
            Detected(kind="framework", name="FastAPI", category="web_backend", technology="fastapi", version="0.110",
                     evidence=[Evidence(path="backend/pyproject.toml", line=5, source="dependency"),
                               Evidence(path="backend/app/main.py", line=1, source="import")]),
            Detected(kind="framework", name="SQLAlchemy", category="orm", technology="sqlalchemy",
                     evidence=[Evidence(path="backend/app/models.py", line=1, source="import")]),
        ],
        datastores=[
            Detected(kind="datastore", name="PostgreSQL", category="relational", technology="postgres",
                     evidence=[Evidence(path="docker-compose.yml", line=16, source="image"),
                               Evidence(path="backend/app/models.py", line=1, source="code")]),
        ],
        queues=[Detected(kind="queue", name="arq", category="queue", technology="arq",
                         evidence=[Evidence(path="backend/app/worker.py", line=1, source="import")])],
        ai_components=[
            Detected(kind="ai", name="OpenAI", category="llm_client", technology="openai",
                     evidence=[Evidence(path="backend/app/services/orders.py", line=3, source="import")]),
        ],
        categories=[CategoryScore(category="web_app", score=0.9), CategoryScore(category="llm_app", score=0.6)],
    )  # fmt: skip
    return RepositoryAnalysis(project_id=PID, files=files, detection=detection, description="A shop.")


def _identity() -> ProjectIdentity:
    return ProjectIdentity(project_id=PID, name="shop", input_type="zip")


def _build() -> ProjectIR:
    return build_ir(_identity(), _inventory(), _analysis(), analysis_version="v1")


@pytest.fixture(scope="module")
def ir() -> ProjectIR:
    return _build()


def _texts(ir: ProjectIR) -> dict[str, str]:
    return {f.path: "\n".join(f"line {i} of {f.path}" for i in range(1, f.lines + 1)) for f in ir.files}


def _rels(ir: ProjectIR, rel_type: str) -> set[tuple[str, str]]:
    return {(r.source_id, r.target_id) for r in ir.relationships if r.type == rel_type}


def _fid(path: str) -> str:
    return entity_id(PID, "file", path)


def _sid(kind: str, path: str, qual: str) -> str:
    return entity_id(PID, kind, path, qual)


ROUTES = "backend/app/api/routes.py"
ORDERS = "backend/app/services/orders.py"


# ---- determinism & integrity -------------------------------------------------------------------------------


def test_deterministic_byte_identical_output(ir: ProjectIR) -> None:
    again = _build()
    assert orjson.dumps(ir.model_dump(mode="json")) == orjson.dumps(again.model_dump(mode="json"))
    assert encode_ir(ir) == encode_ir(again)
    assert decode_ir(encode_ir(ir)) == ir


def test_no_dangling_references(ir: ProjectIR) -> None:
    assert validate_ir(ir) == []
    assert ir.stats["dropped_dangling_relationships"] == 0
    assert all(
        r.provenance.source == "deterministic" and r.provenance.evidence_ids == [] for r in ir.relationships
    )


def test_validate_ir_reports_problems(ir: ProjectIR) -> None:
    broken = ir.model_copy(deep=True)
    broken.endpoints[0].handler_id = "function:missing"
    broken.relationships.append(broken.relationships[0].model_copy(update={"target_id": "file:nope"}))
    problems = validate_ir(broken)
    assert any("handler_id" in p for p in problems)
    assert any("file:nope" in p for p in problems)


def test_project_mismatch_rejected() -> None:
    other = _analysis().model_copy(update={"project_id": "other"})
    with pytest.raises(UnfoldError):
        build_ir(_identity(), _inventory(), other, analysis_version="v1")


def test_ids_follow_core_ids_convention(ir: ProjectIR) -> None:
    by_path = {f.path: f for f in ir.files}
    assert by_path[ROUTES].id == _fid(ROUTES)
    ep = next(e for e in ir.endpoints if e.route == "/api/orders")
    assert ep.id == entity_id(PID, "endpoint", ROUTES, "GET /api/orders")
    assert ep.name == "GET /api/orders"
    sch = next(s for s in ir.schemas if s.name == "Order")
    assert sch.id == entity_id(PID, "schema", "backend/app/models.py", "Order")
    assert sch.symbol_id == _sid("class", "backend/app/models.py", "Order")


# ---- files / modules / symbols -----------------------------------------------------------------------------


def test_files_exclude_tier4_and_carry_metadata(ir: ProjectIR) -> None:
    paths = [f.path for f in ir.files]
    assert paths == sorted(paths)
    assert "vendor/lib.js" not in paths
    main = next(f for f in ir.files if f.path == "backend/app/main.py")
    assert (main.tier, main.role, main.docstring, main.lines) == (
        0,
        "entrypoint",
        "FastAPI application factory.",
        20,
    )
    assert ir.stats["files_total"] == len(FILES)
    assert ir.stats["files_indexed"] == len(FILES) - 1
    assert ir.stats["files_by_tier"]["4"] == 1
    assert ir.stats["ignored_analyses"] == 2
    assert ir.project.categories == ["web_app", "llm_app"]
    assert ir.project.description == "A shop."


def test_modules_are_code_directories(ir: ProjectIR) -> None:
    mods = {m.path: m for m in ir.modules}
    assert set(mods) == {
        "backend/app",
        "backend/app/api",
        "backend/app/services",
        "backend/tests",
        "frontend/src",
    }
    assert mods["backend/app/api"].name == "app.api"  # python package naming stops at the package root
    assert mods["frontend/src"].name == "frontend/src"
    assert mods["backend/app"].language == "python"
    files = {f.path: f for f in ir.files}
    assert files[ROUTES].module_id == mods["backend/app/api"].id
    assert files["README.md"].module_id is None
    assert files[ROUTES].id in mods["backend/app/api"].file_ids
    contains = _rels(ir, "CONTAINS")
    assert (mods["backend/app/api"].id, files[ROUTES].id) in contains
    assert (mods["backend/app"].id, mods["backend/app/api"].id) in contains


def test_symbols_parents_and_containment(ir: ProjectIR) -> None:
    syms = {(s.path, s.qualname): s for s in ir.symbols}
    cls = syms[(ORDERS, "OrderService")]
    assert cls.start_line == 5  # first definition wins over the redefinition
    fetch = syms[(ORDERS, "OrderService.fetch")]
    nested = syms[(ORDERS, "OrderService.create._validate")]
    assert fetch.parent_id == cls.id
    assert nested.parent_id == syms[(ORDERS, "OrderService.create")].id
    contains = _rels(ir, "CONTAINS")
    assert (cls.id, fetch.id) in contains
    assert (_fid(ORDERS), cls.id) in contains
    assert (_fid(ORDERS), fetch.id) not in contains
    assert len([s for s in ir.symbols if s.qualname == "OrderService"]) == 1


# ---- code relationships ------------------------------------------------------------------------------------


def test_imports_calls_inherits(ir: ProjectIR) -> None:
    imports = _rels(ir, "IMPORTS")
    assert (_fid("backend/app/main.py"), _fid(ROUTES)) in imports
    assert (_fid("frontend/src/App.tsx"), _fid("frontend/src/api.ts")) in imports
    calls = {(r.source_id, r.target_id): r for r in ir.relationships if r.type == "CALLS"}
    resolved = calls[(_sid("function", ROUTES, "list_orders"), _sid("method", ORDERS, "OrderService.fetch"))]
    assert resolved.provenance.confidence == 0.95
    fallback = calls[
        (_sid("function", ROUTES, "create_order"), _sid("method", ORDERS, "OrderService.create"))
    ]
    assert fallback.provenance.analyzer == "project_ir.resolve"
    assert fallback.provenance.confidence < 1.0
    assert (
        _sid("method", ORDERS, "OrderService.create"),
        _sid("method", ORDERS, "OrderService.fetch"),
    ) in calls
    assert not any(s == t for s, t in calls)
    assert ir.stats["dropped_self_loops"] >= 1
    assert (
        _sid("class", ORDERS, "OrderService"),
        _sid("class", "backend/app/services/base.py", "BaseService"),
    ) in _rels(ir, "INHERITS")


def test_external_imports_use_dependencies(ir: ProjectIR) -> None:
    deps = {d.name: d for d in ir.dependencies}
    assert deps["SQLAlchemy"].id == entity_id(PID, "dependency", "backend/pyproject.toml", "pypi:SQLAlchemy")
    assert deps["pytest"].dev
    uses = _rels(ir, "USES")
    assert (_fid("backend/app/models.py"), deps["SQLAlchemy"].id) in uses  # case/sep-insensitive match
    assert (_fid("backend/pyproject.toml"), deps["fastapi"].id) in _rels(ir, "DEPENDS_ON")


# ---- endpoints / schemas / data ----------------------------------------------------------------------------


def test_endpoints_routes_and_handlers(ir: ProjectIR) -> None:
    eps = {(e.method, e.route): e for e in ir.endpoints}
    assert set(eps) == {("GET", "/api/orders"), ("POST", "/orders/{order_id}"), ("GET", "/health")}
    create = eps[("POST", "/orders/{order_id}")]
    assert create.handler_id == _sid("function", ROUTES, "create_order")
    assert create.framework == "fastapi"
    assert (create.start_line, create.end_line) == (15, 25)
    handles = _rels(ir, "HANDLES")
    exposes = _rels(ir, "EXPOSES")
    for e in ir.endpoints:
        assert e.handler_id is not None
        assert (e.handler_id, e.id) in handles
        assert (_fid(ROUTES), e.id) in exposes
    assert ir.stats["endpoints_with_handler"] == 3


def test_services_expose_endpoints_by_root(ir: ProjectIR) -> None:
    svcs = {s.name: s for s in ir.services}
    api = svcs["api"]
    assert api.service_kind == "api"
    assert api.entrypoint_file_id == _fid("backend/app/main.py")  # from the Dockerfile CMD
    assert api.depends_on == ["db", "ghost", "redis"]
    exposes = _rels(ir, "EXPOSES")
    assert all((api.id, e.id) in exposes for e in ir.endpoints)
    assert svcs["worker"].service_kind == "worker"
    assert svcs["worker"].entrypoint_file_id == _fid("backend/app/worker.py")
    assert svcs["db"].service_kind == "database"
    assert svcs["redis"].service_kind == "cache"
    depends = _rels(ir, "DEPENDS_ON")
    assert (api.id, svcs["db"].id) in depends
    assert all(t != "ghost" for _, t in depends)
    # The Dockerfile is built by compose services, so it does not become a service of its own; the worker
    # app object is claimed by the compose worker.
    assert set(svcs) == {"api", "worker", "db", "redis"}


def test_schemas_and_data_access(ir: ProjectIR) -> None:
    order = next(s for s in ir.schemas if s.name == "Order")
    assert order.fields[1] == {
        "name": "customer_id",
        "type": "int",
        "relation": "Customer",
        "attributes": ["fk"],
    }
    reads = {(r.source_id, r.target_id): r for r in ir.relationships if r.type == "READS_FROM"}
    via_table = reads[(_sid("function", ROUTES, "list_orders"), order.id)]  # "orders" → table of Order
    assert via_table.provenance.analyzer == "project_ir.link"
    assert (_sid("method", ORDERS, "OrderService.create"), order.id) in _rels(ir, "WRITES_TO")


def test_datastores_queues_ai_frameworks(ir: ProjectIR) -> None:
    stores = {d.technology: d for d in ir.data_stores}
    assert set(stores) == {"postgresql", "redis"}  # detection alias + compose image
    assert stores["postgresql"].name == "PostgreSQL"
    uses = _rels(ir, "USES")
    assert (_fid("backend/app/models.py"), stores["postgresql"].id) in uses
    queues = {q.technology: q for q in ir.queues}
    assert set(queues) == {"arq"}
    arq = queues["arq"]
    assert (_sid("function", "backend/app/worker.py", "send_email"), arq.id) in _rels(ir, "CONSUMES_FROM")
    assert (_sid("method", ORDERS, "OrderService.create"), arq.id) in _rels(ir, "PUBLISHES_TO")
    worker = next(s for s in ir.services if s.name == "worker")
    assert (worker.id, arq.id) in _rels(ir, "CONSUMES_FROM")
    assert len(ir.ai_components) == 1  # detection + hint merge into one component
    ai = ir.ai_components[0]
    assert (ai.component_kind, ai.technology) == ("llm_client", "openai")
    assert (_sid("method", ORDERS, "OrderService.summarize"), ai.id) in uses
    fws = {f.name: f for f in ir.frameworks}
    assert fws["FastAPI"].category == "web_backend"
    assert (_fid("backend/app/main.py"), fws["FastAPI"].id) in uses
    assert (_fid("backend/pyproject.toml"), fws["FastAPI"].id) not in uses  # manifest evidence is not USES


def test_infrastructure_links(ir: ProjectIR) -> None:
    infra = {(i.resource_kind, i.path): i for i in ir.infrastructure}
    assert set(infra) == {
        ("ci_workflow", ".github/workflows/ci.yml"),
        ("k8s_deployment", "k8s/deploy.yaml"),
        ("dockerfile", "backend/Dockerfile"),
        ("compose", "docker-compose.yml"),
    }
    svcs = {s.name: s.id for s in ir.services}
    configures = _rels(ir, "CONFIGURES")
    assert (infra[("dockerfile", "backend/Dockerfile")].id, svcs["api"]) in configures
    assert all((infra[("compose", "docker-compose.yml")].id, sid) in configures for sid in svcs.values())
    assert (infra[("k8s_deployment", "k8s/deploy.yaml")].id, svcs["api"]) in _rels(ir, "DEPLOYS_TO")


def test_languages_and_stats(ir: ProjectIR) -> None:
    langs = {li.name: li for li in ir.languages}
    assert "javascript" not in langs  # only in the tier-4 vendor file
    assert langs["python"].files == 10
    assert ir.languages[0].name == "python"
    assert ir.stats["relationships"] == len(ir.relationships)
    assert sum(ir.stats["relationships_by_type"].values()) == len(ir.relationships)


def test_normalize_route() -> None:
    assert normalize_route("") == "/"
    assert normalize_route("api//users/") == "/api/users"
    assert normalize_route("/") == "/"


def test_standalone_dockerfile_and_app_object_services() -> None:
    analysis = _analysis()
    del analysis.files["docker-compose.yml"]
    ir = build_ir(_identity(), _inventory(), analysis, analysis_version="v1")
    assert validate_ir(ir) == []
    svcs = {s.name: s for s in ir.services}
    backend = svcs["backend"]  # from backend/Dockerfile
    assert backend.path == "backend/Dockerfile"
    assert backend.entrypoint_file_id == _fid("backend/app/main.py")
    worker = next(s for s in ir.services if s.entrypoint_file_id == _fid("backend/app/worker.py"))
    assert worker.service_kind == "worker"
    assert worker.name != backend.name
    assert all((backend.id, e.id) in _rels(ir, "EXPOSES") for e in ir.endpoints)


def test_empty_inputs() -> None:
    ir = build_ir(
        _identity(),
        Inventory(),
        RepositoryAnalysis(project_id=PID, detection=DetectionResult(rules_version="r1")),
        analysis_version="v0",
    )
    assert validate_ir(ir) == []
    assert ir.files == []
    assert ir.relationships == []


# ---- consumers accept the IR -------------------------------------------------------------------------------


def test_chunking_accepts_ir(ir: ProjectIR) -> None:
    chunks = chunk_project(ir, _texts(ir))
    assert chunks
    assert {c.path for c in chunks} <= {f.path for f in ir.files}
    handler = next(c for c in chunks if c.symbol_name == "create_order")
    assert handler.kind == "endpoint"
    assert any(i.startswith("endpoint:") for i in handler.graph_node_ids)
    model = next(c for c in chunks if c.symbol_name == "Order")
    assert model.kind == "schema"
    entities = ir.entity_index()
    assert all(nid in entities for c in chunks for nid in c.graph_node_ids)


def test_graph_builder_rows_have_no_dangling_edges(ir: ProjectIR) -> None:
    nodes = _ir_nodes(ir)
    node_ids = {uid for _, uid, _ in nodes} | {project_uid(PID)}
    assert len({uid for _, uid, _ in nodes}) == len(nodes)
    for _, src, tgt in _derived_relationships(ir, node_ids):
        assert src in node_ids
        assert tgt in node_ids
    # Variables are not graph nodes; every other IR relationship endpoint is one.
    for r in ir.relationships:
        assert r.source_id in node_ids
        assert r.target_id in node_ids


def test_communities_and_diagrams_accept_ir(ir: ProjectIR) -> None:
    cands = communities.detect(ir)
    assert cands
    assert {fid for c in cands for fid in c.member_file_ids} == {f.id for f in ir.files}
    files = {f.id: f.path for f in ir.files}
    views = [
        SubsystemView(key=c.id, title=c.label_hint, member_file_paths=[files[f] for f in c.member_file_ids])
        for c in cands
    ]
    workflow = WorkflowFinding(
        name="Create order",
        trigger="POST /orders/{order_id}",
        steps=[
            WorkflowStep(actor="Client", action="POST", target="api"),
            WorkflowStep(actor="api", action="publish", target="arq"),
        ],
    )  # type: ignore[call-arg]
    specs = build_all(PID, ir, views, [workflow])
    kinds = {s.kind for s in specs}
    assert {
        "architecture",
        "data_model",
        "request_flow",
        "deployment",
        "ai_pipeline",
        "module_dependencies",
    } <= kinds
    assert "workflow_sequence" in kinds
    assert [s.id for s in specs] == [s.id for s in build_all(PID, _build(), views, [workflow])]
