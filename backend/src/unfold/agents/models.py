"""Deep Agent output contracts (LLD §15). Findings feed the guide planner, section writer and diagrams."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

InvestigatorKind = Literal["architecture", "workflow", "data", "ai_ml", "infrastructure", "security", "chat"]


class WorkflowStep(BaseModel):
    actor: str  # component/service/module name as it appears in the repository
    action: str
    target: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)  # chunk ids


class WorkflowFinding(BaseModel):
    name: str
    trigger: str
    steps: list[WorkflowStep]
    failure_branches: list[str] = Field(default_factory=list)


class FindingFact(BaseModel):
    text: str
    kind: Literal["project_fact", "inference"] = "project_fact"
    evidence_ids: list[str] = Field(default_factory=list)


class ComponentFinding(BaseModel):
    name: str
    responsibility: str
    paths: list[str] = Field(default_factory=list)


class InvestigationFinding(BaseModel):
    summary: str
    facts: list[FindingFact] = Field(default_factory=list)
    workflows: list[WorkflowFinding] = Field(default_factory=list)
    components: list[ComponentFinding] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
