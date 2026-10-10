"""Domain enumerations shared by every layer (LLD §5, §11, §13, §16)."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class TechnicalLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    EXPERIENCED = "experienced"


class Proof(StrEnum):
    """How an edge or fact is known. `INFERRED` is never drawn as a diagram edge (investigation engine §4)."""

    STATIC = "static"  # parser saw it: import, decorator route, class definition
    HEURISTIC = "heuristic"  # name-resolution match or framework convention
    INFERRED = "inferred"  # LLM or retrieval proximity only


class InputType(StrEnum):
    GITHUB = "github"
    ZIP = "zip"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobStage(StrEnum):
    RECEIVED = "RECEIVED"
    VALIDATING_INPUT = "VALIDATING_INPUT"
    INGESTING = "INGESTING"
    SECURITY_SCANNING = "SECURITY_SCANNING"
    INVENTORY = "INVENTORY"
    STRUCTURAL_ANALYSIS = "STRUCTURAL_ANALYSIS"
    BUILDING_IR = "BUILDING_IR"
    BUILDING_GRAPH = "BUILDING_GRAPH"
    INDEXING_LEXICAL = "INDEXING_LEXICAL"
    INDEXING_DENSE = "INDEXING_DENSE"
    SUMMARIZING_HIERARCHY = "SUMMARIZING_HIERARCHY"
    DISCOVERING_SUBSYSTEMS = "DISCOVERING_SUBSYSTEMS"
    INVESTIGATING_PROJECT = "INVESTIGATING_PROJECT"
    PLANNING_GUIDE = "PLANNING_GUIDE"
    RESEARCHING_GUIDE = "RESEARCHING_GUIDE"
    GENERATING_GUIDE = "GENERATING_GUIDE"
    VERIFYING_GUIDE = "VERIFYING_GUIDE"
    GENERATING_VISUALS = "GENERATING_VISUALS"
    FINALIZING = "FINALIZING"
    READY = "READY"
    FAILED_INPUT = "FAILED_INPUT"
    FAILED_SECURITY = "FAILED_SECURITY"
    FAILED_INGESTION = "FAILED_INGESTION"
    FAILED_ANALYSIS = "FAILED_ANALYSIS"
    FAILED_PROVIDER_CAPACITY = "FAILED_PROVIDER_CAPACITY"
    FAILED_GENERATION = "FAILED_GENERATION"
    FAILED_EXPORT = "FAILED_EXPORT"

    @property
    def is_failure(self) -> bool:
        return self.value.startswith("FAILED_")

    @property
    def is_terminal(self) -> bool:
        return self is JobStage.READY or self.is_failure

    @property
    def order(self) -> int:
        return PIPELINE.index(self) if self in PIPELINE else -1

    @property
    def user_label(self) -> str:
        return USER_LABELS.get(self, self.value.replace("_", " ").capitalize())


PIPELINE: list[JobStage] = [
    JobStage.RECEIVED,
    JobStage.VALIDATING_INPUT,
    JobStage.INGESTING,
    JobStage.SECURITY_SCANNING,
    JobStage.INVENTORY,
    JobStage.STRUCTURAL_ANALYSIS,
    JobStage.BUILDING_IR,
    JobStage.BUILDING_GRAPH,
    JobStage.INDEXING_LEXICAL,
    JobStage.INDEXING_DENSE,
    JobStage.SUMMARIZING_HIERARCHY,
    JobStage.DISCOVERING_SUBSYSTEMS,
    JobStage.INVESTIGATING_PROJECT,
    JobStage.PLANNING_GUIDE,
    JobStage.RESEARCHING_GUIDE,
    JobStage.GENERATING_GUIDE,
    JobStage.VERIFYING_GUIDE,
    JobStage.GENERATING_VISUALS,
    JobStage.FINALIZING,
    JobStage.READY,
]

# PRD §7.2 — progress reflects real backend stages.
USER_LABELS: dict[JobStage, str] = {
    JobStage.RECEIVED: "Queued",
    JobStage.VALIDATING_INPUT: "Validating input",
    JobStage.INGESTING: "Fetching project",
    JobStage.SECURITY_SCANNING: "Inspecting repository",
    JobStage.INVENTORY: "Detecting languages and frameworks",
    JobStage.STRUCTURAL_ANALYSIS: "Mapping repository structure",
    JobStage.BUILDING_IR: "Building the project model",
    JobStage.BUILDING_GRAPH: "Building project knowledge graph",
    JobStage.INDEXING_LEXICAL: "Indexing project knowledge",
    JobStage.INDEXING_DENSE: "Indexing project knowledge (semantic)",
    JobStage.SUMMARIZING_HIERARCHY: "Summarizing files and modules",
    JobStage.DISCOVERING_SUBSYSTEMS: "Understanding major subsystems",
    JobStage.INVESTIGATING_PROJECT: "Reconstructing workflows",
    JobStage.PLANNING_GUIDE: "Planning your guide",
    JobStage.RESEARCHING_GUIDE: "Researching each chapter",
    JobStage.GENERATING_GUIDE: "Writing your guide",
    JobStage.VERIFYING_GUIDE: "Verifying explanations",
    JobStage.GENERATING_VISUALS: "Generating diagrams",
    JobStage.FINALIZING: "Finalizing Project Guide",
    JobStage.READY: "Ready",
    JobStage.FAILED_INPUT: "The input could not be processed",
    JobStage.FAILED_SECURITY: "The upload was rejected by security checks",
    JobStage.FAILED_INGESTION: "The project could not be fetched",
    JobStage.FAILED_ANALYSIS: "Analysis failed",
    JobStage.FAILED_PROVIDER_CAPACITY: "AI capacity is exhausted — retry later",
    JobStage.FAILED_GENERATION: "Guide generation failed",
    JobStage.FAILED_EXPORT: "Export failed",
}


class InvalidTransition(ValueError):
    pass


def check_transition(current: JobStage, new: JobStage) -> None:
    """Forward-only pipeline; re-entering the current stage (resume) is allowed; failures from any live stage."""
    if current.is_terminal and current is not new:
        raise InvalidTransition(f"{current} is terminal")
    if new.is_failure:
        return
    if new.order < current.order:
        raise InvalidTransition(f"cannot move back from {current} to {new}")


class SectionStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"


class ClaimStatus(StrEnum):
    VERIFIED = "VERIFIED"
    STRONGLY_INFERRED = "STRONGLY_INFERRED"
    INFERRED = "INFERRED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"
    PROPOSED = "PROPOSED"


class ClaimKind(StrEnum):
    PROJECT_FACT = "project_fact"
    INFERENCE = "inference"
    IMPROVEMENT = "improvement"


class ModelCapability(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    REASONING = "reasoning"
    LONG_CONTEXT = "long_context"
    STRUCTURED = "structured"
    EVALUATOR = "evaluator"


class Priority(IntEnum):
    P0 = 0  # user-blocking (chat, section needed by UI)
    P1 = 1  # core analysis
    P2 = 2  # enrichments
    P3 = 3  # background evaluation


class RetrievalIntent(StrEnum):
    OVERVIEW = "overview"
    WORKFLOW = "workflow"
    ARCHITECTURE = "architecture"
    IMPLEMENTATION_LOCATION = "implementation_location"
    TECHNICAL_CONCEPT = "technical_concept"
    DESIGN_REASONING = "design_reasoning"
    FAILURE_ANALYSIS = "failure_analysis"
    SECURITY = "security"
    INTERVIEW_QUESTION = "interview_question"


class RetrievalMode(StrEnum):
    GUIDE = "guide"
    CHAT = "chat"
    INVESTIGATION = "investigation"


class ContentType(StrEnum):
    SOURCE = "source"
    DOCS = "docs"
    CONFIG = "config"
    TEST = "test"
    INFRA = "infra"


class ChatMode(StrEnum):
    DEFAULT = "default"
    SIMPLER = "simpler"
    DEEPER = "deeper"
    QUIZ = "quiz"
    INTERVIEW = "interview"


class ArtifactKind(StrEnum):
    GUIDE_PDF = "guide_pdf"
    GUIDE_MD = "guide_md"
    CHAT_PDF = "chat_pdf"
    CHAT_MD = "chat_md"

    @property
    def is_guide(self) -> bool:
        return self in (ArtifactKind.GUIDE_PDF, ArtifactKind.GUIDE_MD)

    @property
    def extension(self) -> str:
        return "pdf" if self in (ArtifactKind.GUIDE_PDF, ArtifactKind.CHAT_PDF) else "md"

    @property
    def content_type(self) -> str:
        return "application/pdf" if self.extension == "pdf" else "text/markdown; charset=utf-8"


class ArtifactStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
