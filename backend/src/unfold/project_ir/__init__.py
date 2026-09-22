"""Project IR (LLD §8): schema, pure builder, complexity profile and object-store snapshots."""

from unfold.project_ir.builder import build_ir, validate_ir
from unfold.project_ir.complexity import AgentBudget, ComplexityProfile, RetrievalBreadth, score
from unfold.project_ir.schema import IR_SCHEMA_VERSION, ProjectIdentity, ProjectIR
from unfold.project_ir.snapshot import clear_ir_cache, ir_object_key, load_ir, save_ir

__all__ = [
    "IR_SCHEMA_VERSION",
    "AgentBudget",
    "ComplexityProfile",
    "ProjectIR",
    "ProjectIdentity",
    "RetrievalBreadth",
    "build_ir",
    "clear_ir_cache",
    "ir_object_key",
    "load_ir",
    "save_ir",
    "score",
    "validate_ir",
]
