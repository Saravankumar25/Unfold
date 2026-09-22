"""Repository-intelligence value objects (LLD §7, ESD §7–§8).

`Inventory` describes every file of an extracted repository; `FileAnalysis` is what one analyzer extracted from one
file; `RepositoryAnalysis` is the cross-file linked result consumed by `project_ir.builder.build_ir`. All models are
plain pydantic, JSON-serializable, and contain repository-relative POSIX paths only.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Tier = Literal[0, 1, 2, 3, 4]


# --------------------------------------------------------------------------------------------------- inventory


class InventoryFile(BaseModel):
    path: str  # repository-relative POSIX path
    size: int
    ext: str  # lowercase, without the dot ("" when none)
    language: str | None = None
    is_text: bool = True
    generated: bool = False
    vendor: bool = False
    oversized: bool = False
    sha256: str
    lines: int = 0
    role: str = "source"
    tier: Tier = 2

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def directory(self) -> str:
        """Parent directory ("" for the repository root)."""
        return self.path.rsplit("/", 1)[0] if "/" in self.path else ""


class Inventory(BaseModel):
    files: list[InventoryFile] = Field(default_factory=list)  # sorted by path
    skipped: dict[str, str] = Field(default_factory=dict)  # path -> reason (symlink, unreadable, ...)
    total_bytes: int = 0

    def by_path(self) -> dict[str, InventoryFile]:
        return {f.path: f for f in self.files}

    def tier_counts(self) -> dict[int, int]:
        counts = dict.fromkeys(range(5), 0)
        for f in self.files:
            counts[f.tier] += 1
        return counts


# --------------------------------------------------------------------------------------------------- analysis


SymbolKind = Literal["class", "function", "method", "component", "interface", "variable"]


class SymbolDef(BaseModel):
    name: str
    qualname: str  # dotted within the file, e.g. `Service.run`
    kind: SymbolKind
    start_line: int
    end_line: int
    parent: str | None = None  # qualname of the enclosing class
    signature: str | None = None
    docstring: str | None = None
    decorators: list[str] = Field(default_factory=list)
    bases: list[str] = Field(default_factory=list)
    exported: bool = False
    is_async: bool = False


class ImportRef(BaseModel):
    module: str  # absolute module / specifier as written (relative Python imports made absolute)
    names: list[str] = Field(default_factory=list)
    line: int = 0
    alias: str | None = None
    resolved_path: str | None = None  # repository file the import resolves to
    resolved_dir: str | None = None  # repository directory (package-style imports, e.g. Go)
    external: bool = True
    package: str | None = None  # top-level external package name (for dependency/framework detection)


class Binding(BaseModel):
    """What a local name refers to after imports (used for cross-file call / mount resolution)."""

    kind: Literal["module", "symbol"]
    path: str | None = None  # repository file, when internal
    attr: str | None = None  # symbol name inside `path` (kind == symbol)
    external: str | None = None  # external module name, when not internal


class CallRef(BaseModel):
    caller: str | None  # qualname of the enclosing symbol (None = module level)
    parts: list[str]  # callee expression split on dots, e.g. ["self", "store", "search"]
    line: int
    resolved_path: str | None = None
    resolved_qualname: str | None = None
    confidence: float = 0.0


class RouteDef(BaseModel):
    method: str  # GET/POST/... or ANY
    path: str  # as written in the file
    line: int
    framework: str
    router: str | None = None  # local router variable (FastAPI/Flask/Express)
    handler: str | None = None  # qualname of the handler in this file
    handler_ref: list[str] | None = None  # unresolved handler expression (e.g. ["h", "Health"])
    full_path: str | None = None  # after mount-prefix resolution
    handler_path: str | None = None  # file of the resolved handler (defaults to this file)


class RouterDef(BaseModel):
    var: str
    kind: str  # fastapi | apirouter | flask | blueprint | express | chi | ...
    prefix: str = ""
    line: int = 0


class MountDef(BaseModel):
    """`parent.include_router(target, prefix=...)`, `app.use("/x", router)`, Django `include(...)`."""

    parent: str | None  # router variable doing the mounting
    target: list[str]  # expression parts of the mounted router
    prefix: str = ""
    line: int = 0
    target_module: str | None = None  # Django include("app.urls")


class FieldDef(BaseModel):
    name: str
    type: str | None = None
    relation: str | None = None  # related model / table name
    attributes: list[str] = Field(default_factory=list)  # primary_key, unique, nullable, fk, ...


class SchemaDef(BaseModel):
    name: str
    schema_kind: str  # orm_model | pydantic | sql_table | prisma | dataclass | protobuf | graphql | other
    start_line: int
    end_line: int
    fields: list[FieldDef] = Field(default_factory=list)
    symbol: str | None = None  # qualname of the defining class, when there is one
    table: str | None = None


class TaskDef(BaseModel):
    name: str
    technology: str  # celery | arq | rq | dramatiq | bullmq | ...
    line: int
    symbol: str | None = None


class PublishRef(BaseModel):
    caller: str | None
    task: str
    technology: str
    line: int


class DataAccess(BaseModel):
    caller: str | None
    model: str  # model / table name as referenced
    mode: Literal["read", "write"]
    line: int
    orm: str  # sqlalchemy | django | prisma | ...
    resolved_path: str | None = None
    resolved_name: str | None = None


class AIHint(BaseModel):
    component_kind: (
        str  # llm_client | embedding | vector_search | lexical_search | reranker | agent | training | ...
    )
    technology: str
    line: int
    symbol: str | None = None


class DependencyDef(BaseModel):
    name: str
    version: str | None = None
    ecosystem: str
    dev: bool = False
    line: int = 0


class InfraDef(BaseModel):
    resource_kind: str
    name: str
    provider: str | None = None
    start_line: int = 0
    end_line: int = 0
    details: dict[str, Any] = Field(default_factory=dict)


class ComposeServiceDef(BaseModel):
    name: str
    image: str | None = None
    build_context: str | None = None  # repository-relative directory
    dockerfile: str | None = None  # repository-relative path
    command: str | None = None
    ports: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    env: list[str] = Field(default_factory=list)  # variable NAMES only, never values
    line: int = 0


class DocSection(BaseModel):
    title: str
    level: int
    start_line: int
    end_line: int


class FileAnalysis(BaseModel):
    path: str
    language: str | None = None
    analyzer: str
    docstring: str | None = None
    symbols: list[SymbolDef] = Field(default_factory=list)
    imports: list[ImportRef] = Field(default_factory=list)
    bindings: dict[str, Binding] = Field(default_factory=dict)
    calls: list[CallRef] = Field(default_factory=list)
    exports: list[str] = Field(default_factory=list)
    routers: list[RouterDef] = Field(default_factory=list)
    mounts: list[MountDef] = Field(default_factory=list)
    routes: list[RouteDef] = Field(default_factory=list)
    schemas: list[SchemaDef] = Field(default_factory=list)
    tasks: list[TaskDef] = Field(default_factory=list)
    publishes: list[PublishRef] = Field(default_factory=list)
    data_access: list[DataAccess] = Field(default_factory=list)
    ai_hints: list[AIHint] = Field(default_factory=list)
    dependencies: list[DependencyDef] = Field(default_factory=list)
    scripts: dict[str, str] = Field(default_factory=dict)
    infra: list[InfraDef] = Field(default_factory=list)
    compose_services: list[ComposeServiceDef] = Field(default_factory=list)
    sections: list[DocSection] = Field(default_factory=list)
    app_objects: list[str] = Field(default_factory=list)  # e.g. ["fastapi"], ["express"], ["arq_worker"]
    entry_command: str | None = None  # Dockerfile CMD/ENTRYPOINT (joined)
    errors: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    path: str
    line: int = 0
    source: str  # dependency | import | image | infra | config | code


class Detected(BaseModel):
    """A technology found by `repo_intel.detect` with the evidence that triggered it."""

    kind: Literal["framework", "datastore", "queue", "external_service", "ai"]
    name: str
    category: str  # framework category | datastore category | ai component kind | service category
    technology: str
    version: str | None = None
    confidence: float = 1.0
    evidence: list[Evidence] = Field(default_factory=list)


class CategoryScore(BaseModel):
    category: str
    score: float
    signals: list[str] = Field(default_factory=list)


class DetectionResult(BaseModel):
    rules_version: str
    frameworks: list[Detected] = Field(default_factory=list)
    datastores: list[Detected] = Field(default_factory=list)
    queues: list[Detected] = Field(default_factory=list)
    external_services: list[Detected] = Field(default_factory=list)
    ai_components: list[Detected] = Field(default_factory=list)
    categories: list[CategoryScore] = Field(default_factory=list)  # score >= threshold, best first
    category_scores: list[CategoryScore] = Field(default_factory=list)  # every category, for explainability


class RepositoryAnalysis(BaseModel):
    project_id: str
    files: dict[str, FileAnalysis] = Field(default_factory=dict)  # path -> analysis (tier 0–3 text files)
    detection: DetectionResult
    errors: dict[str, list[str]] = Field(default_factory=dict)  # path -> parse errors (analysis continued)
    description: str | None = None  # first README paragraph
    flagged: dict[str, list[str]] = Field(default_factory=dict)  # path -> ["secret", "injection"] signals
