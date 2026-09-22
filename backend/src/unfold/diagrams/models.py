"""DiagramSpec (ESD §22, LLD §17): the single structured source for React Flow (web) and Mermaid (PDF)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DiagramKind = Literal[
    "architecture",
    "module_dependencies",
    "data_model",
    "request_flow",
    "deployment",
    "ai_pipeline",
    "workflow_sequence",
]
MAX_NODES = 40


class DiagramNode(BaseModel):
    id: str
    label: str
    kind: str = "component"  # service | module | datastore | endpoint | external | queue | actor | file | ...
    group: str | None = None
    ref_uid: str | None = None  # graph/IR entity id for "click a node → evidence / ask"
    detail: str | None = None
    inferred: bool = False


class DiagramEdge(BaseModel):
    source: str
    target: str
    label: str | None = None
    kind: str | None = None  # relationship type or "message" for sequence diagrams
    inferred: bool = False


class DiagramGroup(BaseModel):
    id: str
    label: str


class DiagramSpec(BaseModel):
    id: str  # unfold.core.ids.diagram_id(project_id, kind, key)
    kind: DiagramKind
    title: str
    description: str = ""
    nodes: list[DiagramNode] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)
    groups: list[DiagramGroup] = Field(default_factory=list)
    annotations: list[str] = Field(default_factory=list)
    section_key: str | None = None
