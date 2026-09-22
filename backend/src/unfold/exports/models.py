"""Inputs for the export templates (LLD §19). The worker maps persisted rows onto these before rendering."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from unfold.domain.models import EvidenceRef


class ExportProject(BaseModel):
    name: str
    source: str | None = None  # GitHub URL or uploaded archive name
    input_type: str | None = None  # github | zip
    commit_sha: str | None = None
    technical_level: str | None = None
    generated_at: datetime
    estimated_read_minutes: int | None = None


class DiagramExport(BaseModel):
    id: str  # DiagramSpec.id, referenced by DiagramBlock.diagram_id
    title: str
    mermaid: str  # from unfold.diagrams.mermaid.to_mermaid(spec)
    caption: str | None = None


class ExportChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    mode: str = "default"
    evidence: list[EvidenceRef] = Field(default_factory=list)
    created_at: datetime
