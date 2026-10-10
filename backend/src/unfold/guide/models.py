"""Guide content contracts (LLD §16). The same structured blocks feed the web reader and the PDF export."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from unfold.domain.enums import Proof
from unfold.domain.models import ClaimRecord
from unfold.understand.models import FlowTrace

PLANNER_VERSION = "2"  # v1 rows are `GuideSectionPlan`s; they load as `ChapterPlan`s without research fields
EvidenceKind = Literal["definition", "flow", "config", "test", "route", "model"]


class GuideSectionPlan(BaseModel):
    key: str  # slug, unique within the plan, e.g. "project-in-60-seconds", "retrieval-pipeline"
    title: str
    kind: Literal["backbone", "core"]
    purpose: str
    target_minutes: float
    required_concepts: list[str] = Field(default_factory=list)
    evidence_objectives: list[str] = Field(default_factory=list)
    visual_needs: list[str] = Field(default_factory=list)  # DiagramKind values
    depth: Literal["overview", "standard", "deep"] = "standard"
    related_subsystems: list[str] = Field(default_factory=list)


class ChapterPlan(GuideSectionPlan):
    """v2 plan entry (investigation engine §4). `purpose` mirrors `objective` for v1 readers (API, exports)."""

    objective: str = ""
    questions: list[str] = Field(default_factory=list)  # 2-6, answerable from code
    targets: list[str] = Field(default_factory=list)  # RepoMap entity id | component id
    depends_on: list[str] = Field(
        default_factory=list
    )  # chapter keys, acyclic; ordering and reader links only
    expected_evidence: list[EvidenceKind] = Field(default_factory=list)

    @model_validator(mode="after")
    def _objective_is_purpose(self) -> ChapterPlan:
        if not self.objective:
            self.objective = self.purpose
        elif not self.purpose:
            self.purpose = self.objective
        return self


class GuidePlan(BaseModel):
    estimated_read_minutes: int
    sections: list[ChapterPlan]


# ---- research ---------------------------------------------------------------------------------------------


class EdgeRef(BaseModel):
    """A call edge a finding or flow states, by entity id (what an edge claim's `graph_node_ids` carry)."""

    src: str
    dst: str
    src_name: str
    dst_name: str


class Finding(BaseModel):
    question_id: int  # index into `ResearchBrief.questions`
    statement: str  # built deterministically from retrieved chunks / IR / flows, never model prose
    evidence: list[str]  # chunk ids
    proof: Proof
    edges: list[EdgeRef] = Field(default_factory=list)  # the call edges named in `statement`


class ResearchSpent(BaseModel):
    rounds: int = 0
    retrievals: int = 0
    llm_calls: int = 0
    tokens: int = 0  # estimated size of the brief


class ResearchBrief(BaseModel):
    chapter_key: str
    questions: list[str]
    findings: list[Finding]
    flows: list[FlowTrace]
    tests: list[str]
    open_questions: list[str]
    coverage: float  # answered / asked
    spent: ResearchSpent
    stopped_by: str | None = None  # coverage | rounds | retrievals | llm_calls | tokens | exhausted | budget


# ---- content blocks --------------------------------------------------------------------------------------
# Inline markup allowed in text fields: **bold**, *em*, `code`, and citations [E3] (resolved to chunk ids).


class HeadingBlock(BaseModel):
    type: Literal["heading"] = "heading"
    text: str
    level: Literal[2, 3] = 2


class ParagraphBlock(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    text: str


class BulletsBlock(BaseModel):
    type: Literal["bullets"] = "bullets"
    items: list[str]
    ordered: bool = False


class CalloutBlock(BaseModel):
    type: Literal["callout"] = "callout"
    tone: Literal["note", "tip", "warning", "insight"] = "note"
    text: str


class CodeBlock(BaseModel):
    type: Literal["code"] = "code"
    language: str = ""
    code: str
    path: str | None = None
    start_line: int | None = None
    caption: str | None = None


class DiagramBlock(BaseModel):
    type: Literal["diagram"] = "diagram"
    diagram_id: str
    caption: str | None = None


class TableBlock(BaseModel):
    type: Literal["table"] = "table"
    headers: list[str]
    rows: list[list[str]]


class QABlock(BaseModel):
    type: Literal["qa"] = "qa"
    question: str
    answer: str
    kind: Literal["repository", "design", "failure", "security", "extension"] = "repository"


class GlossaryItem(BaseModel):
    term: str
    definition: str


class GlossaryBlock(BaseModel):
    type: Literal["glossary"] = "glossary"
    items: list[GlossaryItem]


Block = Annotated[
    HeadingBlock
    | ParagraphBlock
    | BulletsBlock
    | CalloutBlock
    | CodeBlock
    | DiagramBlock
    | TableBlock
    | QABlock
    | GlossaryBlock,
    Field(discriminator="type"),
]


class SectionContent(BaseModel):
    summary: str
    blocks: list[Block]


class DraftClaim(BaseModel):
    text: str
    kind: Literal["project_fact", "inference", "improvement"] = "project_fact"
    evidence: list[str] = Field(default_factory=list)  # "E3" labels from the compiled context


class SectionDraft(BaseModel):
    content: SectionContent
    claims: list[DraftClaim] = Field(default_factory=list)


class GuideSection(BaseModel):
    """API/PDF view of a persisted section."""

    key: str
    title: str
    position: int
    status: str
    kind: str = "core"
    target_minutes: float = 0.0
    content: SectionContent | None = None
    claims: list[ClaimRecord] = Field(default_factory=list)
    evidence: dict[str, dict[str, object]] = Field(default_factory=dict)  # E# -> {chunk_id, path, lines}
    quality: dict[str, object] = Field(default_factory=dict)
