"""Contracts of the understanding layer (docs/design/investigation-engine.md §4). Pure value objects."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from unfold.domain.enums import Proof

__all__ = [
    "ArchitectureModel",
    "Boundary",
    "Component",
    "Dependency",
    "Edge",
    "FlowStep",
    "FlowTrace",
    "ProjectKind",
    "Proof",
    "SymbolRef",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SymbolRef(_Frozen):
    """Any located entity (symbol, file, endpoint, schema). `id` is the IR entity id."""

    id: str
    name: str
    kind: str
    path: str
    start_line: int
    end_line: int


class Edge(_Frozen):
    src: str
    dst: str
    rel: str  # a `graph.models.REL_TYPES` member
    proof: Proof


class FlowStep(_Frozen):
    symbol: SymbolRef
    via: Edge | None = None  # None for the entry
    note: str | None = None


class FlowTrace(_Frozen):
    entry: SymbolRef
    steps: list[FlowStep]
    truncated: bool = False  # a depth or fan-out cap hid reachable steps
    unresolved: list[str] = Field(default_factory=list)  # calls seen but not resolvable: "name (path:line)"


class Component(_Frozen):
    id: str
    title: str
    member_paths: list[str]
    responsibility: str
    community: str | None = None  # `SubsystemCandidate.id` when derived from community detection


class Boundary(_Frozen):
    store: str
    technology: str
    category: str
    access: list[SymbolRef]


class Dependency(_Frozen):
    name: str
    ecosystem: str
    version: str | None = None
    manifest_path: str


ProjectKind = Literal["cli", "library", "web_service", "pipeline", "app", "monorepo", "unknown"]


class ArchitectureModel(_Frozen):
    project_kind: ProjectKind
    components: list[Component]
    entry_points: list[SymbolRef]
    flows: list[FlowTrace]
    persistence: list[Boundary]
    external: list[Dependency]
    uncertainty: list[str]
