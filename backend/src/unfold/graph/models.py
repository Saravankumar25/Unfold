"""Graph-layer value objects (LLD §9). Returned by `GraphBuilder`, `GraphQueries` and `communities.detect`."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# --- allow-lists (labels / relationship types cannot be Cypher parameters, so they are validated before use) ---

NODE_LABELS: frozenset[str] = frozenset(
    {
        "Project",
        "Subsystem",
        "Module",
        "File",
        "Class",
        "Function",
        "Method",
        "APIEndpoint",
        "DataStore",
        "Schema",
        "Service",
        "Queue",
        "ExternalService",
        "Library",
        "Framework",
        "InfrastructureResource",
        "AIComponent",
        "Concept",
    }
)
REL_TYPES: frozenset[str] = frozenset(
    {
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
    }
)
INFERRED_NODE_LABELS: frozenset[str] = frozenset({"Subsystem", "Concept"})
# Relationship types traversed by default in path / neighbourhood queries (structural containment excluded).
TRAVERSAL_REL_TYPES: tuple[str, ...] = (
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
    "PART_OF",
    "CONTAINS",
)


class GraphValidationError(ValueError):
    """A label / relationship type outside the allow-list (never interpolated into Cypher)."""


def check_label(label: str, allowed: frozenset[str] = NODE_LABELS) -> str:
    if label not in allowed:
        raise GraphValidationError(f"label not allowed: {label!r}")
    return label


def check_rel_type(rel_type: str, allowed: frozenset[str] = REL_TYPES) -> str:
    if rel_type not in allowed:
        raise GraphValidationError(f"relationship type not allowed: {rel_type!r}")
    return rel_type


# --- write side -------------------------------------------------------------------------------------------------


class GraphWriteStats(BaseModel):
    nodes: int = 0
    relationships: int = 0
    nodes_by_label: dict[str, int] = Field(default_factory=dict)
    relationships_by_type: dict[str, int] = Field(default_factory=dict)
    skipped_relationships: int = 0  # endpoints missing from the IR or type not allow-listed


class InferredNode(BaseModel):
    """Subsystem / Concept node proposed by community detection or an LLM (never overwrites deterministic)."""

    uid: str
    label: Literal["Subsystem", "Concept"]
    name: str
    properties: dict[str, Any] = Field(default_factory=dict)  # small scalar props only


class InferredRelationship(BaseModel):
    source_uid: str
    target_uid: str
    type: str = "PART_OF"
    confidence: float = 0.5
    analyzer: str = "inferred"
    evidence_ids: list[str] = Field(default_factory=list)


# --- read side --------------------------------------------------------------------------------------------------


class GraphNode(BaseModel):
    uid: str
    label: str
    name: str
    kind: str | None = None
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    subsystem_id: str | None = None
    source: str | None = None  # deterministic | inferred
    score: float | None = None  # match score (find_entities) or hop distance (neighbors/callers/callees)


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str
    provenance: str | None = None  # deterministic | inferred
    confidence: float | None = None
    proof: str | None = None  # static | heuristic | inferred (None on edges written before 3.2)


class Neighborhood(BaseModel):
    center: GraphNode | None = None
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class PathResult(BaseModel):
    nodes: list[GraphNode]
    relationships: list[str]  # rel types along the path, len == len(nodes) - 1


class EndpointFlow(BaseModel):
    endpoint: GraphNode
    method: str | None = None
    route: str | None = None
    handler: GraphNode | None = None
    callees: list[GraphNode] = Field(default_factory=list)
    touches: list[GraphNode] = Field(
        default_factory=list
    )  # Schema / DataStore reached from handler or callees


class DegreeNode(BaseModel):
    node: GraphNode
    degree: int


class GraphOverview(BaseModel):
    node_counts: dict[str, int] = Field(default_factory=dict)
    relationship_counts: dict[str, int] = Field(default_factory=dict)
    top_nodes: list[DegreeNode] = Field(default_factory=list)


class ModuleDependency(BaseModel):
    source_uid: str
    source_name: str
    source_path: str | None = None
    target_uid: str
    target_name: str
    target_path: str | None = None
    weight: int


# --- communities ------------------------------------------------------------------------------------------------


class SubsystemCandidate(BaseModel):
    id: str  # 'subsystem:' + hash(project_id + sorted member file ids) — stable across reruns
    member_module_ids: list[str] = Field(default_factory=list)
    member_file_ids: list[str] = Field(default_factory=list)
    key_files: list[str] = Field(default_factory=list)  # file ids, by tier then fan-in
    label_hint: str
    cohesion: float = 0.0  # internal / (internal + external) edge weight
