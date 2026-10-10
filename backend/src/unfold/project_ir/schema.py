"""Project IR schema (ESD §9, LLD §8). The normalized, language-independent model of a repository.

Every entity id comes from `unfold.core.ids.entity_id(project_id, kind, path, qualname)` so rebuilding the IR for
the same project yields the same ids (idempotent graph upserts). Paths are repository-relative POSIX paths.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from unfold.domain.enums import Proof
from unfold.domain.models import Provenance

IR_SCHEMA_VERSION = "1"

Tier = Literal[0, 1, 2, 3, 4]


class Entity(BaseModel):
    id: str
    name: str
    provenance: Provenance
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None


class ProjectIdentity(BaseModel):
    project_id: str
    name: str
    input_type: Literal["github", "zip"]
    source_locator: str | None = None
    commit_sha: str | None = None
    default_branch: str | None = None
    description: str | None = None
    categories: list[str] = Field(default_factory=list)  # e.g. web_app, rag_system, cli_tool (detect rules)


class LanguageInfo(BaseModel):
    name: str
    files: int
    lines: int
    bytes: int


class FrameworkInfo(Entity):
    kind: Literal["framework"] = "framework"
    category: (
        str  # web_backend | web_frontend | orm | testing | ml | llm | queue | infra | mobile | cli | other
    )
    version: str | None = None


class FileIR(Entity):
    kind: Literal["file"] = "file"
    path: str
    language: str | None = None
    size_bytes: int = 0
    lines: int = 0
    tier: Tier = 2
    role: str = "source"
    is_text: bool = True
    module_id: str | None = None
    docstring: str | None = None


class SymbolIR(Entity):
    """Class / function / method / component. `qualname` is dotted within the file (e.g. `Service.run`)."""

    kind: Literal["class", "function", "method", "component", "interface", "variable"]
    qualname: str
    file_id: str
    parent_id: str | None = None
    signature: str | None = None
    docstring: str | None = None
    decorators: list[str] = Field(default_factory=list)
    bases: list[str] = Field(default_factory=list)
    exported: bool = False


class ModuleIR(Entity):
    """A directory-level package/module (Python package, JS folder, Go package...)."""

    kind: Literal["module"] = "module"
    path: str
    file_ids: list[str] = Field(default_factory=list)
    language: str | None = None


class EndpointIR(Entity):
    kind: Literal["endpoint"] = "endpoint"
    method: str  # GET/POST/... or "ANY"; for GraphQL/RPC use "RPC"
    route: str
    handler_id: str | None = None
    framework: str | None = None


class SchemaIR(Entity):
    """Data model definition: ORM model, Pydantic model, SQL table, Prisma model, protobuf message..."""

    kind: Literal["schema"] = "schema"
    schema_kind: str  # orm_model | pydantic | sql_table | prisma | dataclass | protobuf | graphql | other
    fields: list[dict[str, Any]] = Field(default_factory=list)  # [{name, type, relation?}]
    symbol_id: str | None = None


class DataStoreIR(Entity):
    kind: Literal["datastore"] = "datastore"
    technology: str  # postgresql | mysql | sqlite | mongodb | redis | neo4j | qdrant | s3 | ...
    category: str  # relational | document | key_value | graph | vector | object | search | cache


class ServiceIR(Entity):
    """A runnable unit: compose service, app entrypoint, worker, serverless function."""

    kind: Literal["service"] = "service"
    service_kind: str  # web | api | worker | cron | database | cache | proxy | other
    image: str | None = None
    entrypoint_file_id: str | None = None
    ports: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class QueueIR(Entity):
    kind: Literal["queue"] = "queue"
    technology: str  # celery | arq | rq | kafka | rabbitmq | sqs | pubsub | bullmq | ...


class InfraResourceIR(Entity):
    kind: Literal["infra"] = "infra"
    resource_kind: str  # dockerfile | compose | k8s_deployment | terraform_resource | ci_workflow | ...
    provider: str | None = None  # docker | kubernetes | aws | gcp | azure | github_actions | ...
    details: dict[str, Any] = Field(default_factory=dict)


class AIComponentIR(Entity):
    kind: Literal["ai"] = "ai"
    component_kind: (
        str  # llm_client | embedding | vector_search | reranker | agent | training | inference | dataset
    )
    technology: str | None = None


class DependencyIR(Entity):
    kind: Literal["dependency"] = "dependency"
    ecosystem: str  # pypi | npm | maven | go | cargo | composer | rubygems | nuget
    version: str | None = None
    dev: bool = False
    manifest_path: str


RelType = Literal[
    "CONTAINS",
    "PART_OF",
    "IMPORTS",
    "CALLS",
    "INHERITS",
    "IMPLEMENTS",
    "EXPOSES",
    "HANDLES",
    "USES",
    "DEPENDS_ON",
    "READS_FROM",
    "WRITES_TO",
    "PUBLISHES_TO",
    "CONSUMES_FROM",
    "CONFIGURES",
    "DEPLOYS_TO",
    "AUTHENTICATES_WITH",
    "EVIDENCED_BY",
]


class RelationshipIR(BaseModel):
    source_id: str
    target_id: str
    type: RelType
    provenance: Provenance
    properties: dict[str, Any] = Field(default_factory=dict)
    proof: Proof = Proof.STATIC  # additive (3.2); old snapshots load as static


class UnresolvedCallIR(BaseModel):
    """A call the analyzers could not resolve to a project symbol (never an edge). Capped by the builder."""

    source_id: str
    name: str  # dotted callee expression as written
    path: str
    line: int


class ProjectIR(BaseModel):
    schema_version: str = IR_SCHEMA_VERSION
    analysis_version: str
    project: ProjectIdentity
    languages: list[LanguageInfo] = Field(default_factory=list)
    frameworks: list[FrameworkInfo] = Field(default_factory=list)
    files: list[FileIR] = Field(default_factory=list)
    symbols: list[SymbolIR] = Field(default_factory=list)
    modules: list[ModuleIR] = Field(default_factory=list)
    endpoints: list[EndpointIR] = Field(default_factory=list)
    schemas: list[SchemaIR] = Field(default_factory=list)
    data_stores: list[DataStoreIR] = Field(default_factory=list)
    services: list[ServiceIR] = Field(default_factory=list)
    queues: list[QueueIR] = Field(default_factory=list)
    infrastructure: list[InfraResourceIR] = Field(default_factory=list)
    ai_components: list[AIComponentIR] = Field(default_factory=list)
    dependencies: list[DependencyIR] = Field(default_factory=list)
    relationships: list[RelationshipIR] = Field(default_factory=list)
    unresolved_calls: list[UnresolvedCallIR] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)

    def entity_index(self) -> dict[str, Entity]:
        """All entities by id (files, symbols, modules, endpoints, ...). Built on demand; not serialized."""
        out: dict[str, Entity] = {}
        for group in (
            self.frameworks,
            self.files,
            self.symbols,
            self.modules,
            self.endpoints,
            self.schemas,
            self.data_stores,
            self.services,
            self.queues,
            self.infrastructure,
            self.ai_components,
            self.dependencies,
        ):
            for e in group:
                out[e.id] = e
        return out
