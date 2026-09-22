"""Retrieval contracts (LLD §11). Shared by the retrieval pipeline, context compiler, agents, guide and chat."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from unfold.domain.enums import RetrievalIntent, RetrievalMode
from unfold.indexing.models import Chunk


class RetrievalPlan(BaseModel):
    project_id: str
    query: str
    intent: RetrievalIntent = RetrievalIntent.OVERVIEW
    mode: RetrievalMode = RetrievalMode.CHAT
    lexical_queries: list[str] = Field(default_factory=list)
    semantic_queries: list[str] = Field(default_factory=list)
    target_subsystems: list[str] = Field(default_factory=list)
    target_symbols: list[str] = Field(default_factory=list)
    graph_seed_entities: list[str] = Field(default_factory=list)
    structural_filters: dict[str, Any] = Field(default_factory=dict)
    desired_evidence_types: list[str] = Field(default_factory=list)  # ContentType values
    breadth: Literal["narrow", "medium", "broad"] = "medium"


class RetrievedItem(BaseModel):
    chunk: Chunk
    score: float  # final score (rerank if available, else fused)
    fused_score: float = 0.0
    rerank_score: float | None = None
    sources: dict[str, int] = Field(default_factory=dict)  # retriever -> 1-based rank
    via_expansion: bool = False


class RetrievalTrace(BaseModel):
    config_version: str
    per_source_counts: dict[str, int] = Field(default_factory=dict)
    latency_ms: dict[str, float] = Field(default_factory=dict)
    rounds: int = 1
    degraded: list[str] = Field(default_factory=list)
    sufficiency: dict[str, Any] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)  # degraded component -> exception type / reason
    stage_counts: dict[str, int] = Field(default_factory=dict)  # fused, hydrated, reranked, expanded, ...


class GraphPath(BaseModel):
    nodes: list[dict[str, Any]]  # [{uid, name, label, path?}]
    relationships: list[str]  # rel types along the path


class RetrievalResult(BaseModel):
    plan: RetrievalPlan
    items: list[RetrievedItem] = Field(default_factory=list)
    graph_paths: list[GraphPath] = Field(default_factory=list)
    sufficient: bool = True
    trace: RetrievalTrace
