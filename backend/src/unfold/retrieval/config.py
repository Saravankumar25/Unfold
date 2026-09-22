"""Versioned retrieval configuration (LLD §11, ESD §12 "Reciprocal Rank Fusion", AI infra §15-§24).

Every knob that changes ranking lives here and is covered by `RETRIEVAL_CONFIG_VERSION`, which is written into
every `RetrievalTrace` so evaluation runs can be compared. Bump the version whenever a value below changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from unfold.domain.enums import RetrievalIntent, RetrievalMode

RETRIEVAL_CONFIG_VERSION = "retrieval-1"

Source = Literal["lexical", "dense", "graph", "structural"]
SOURCES: tuple[Source, ...] = ("lexical", "dense", "graph", "structural")
Breadth = Literal["narrow", "medium", "broad"]


@dataclass(frozen=True)
class SourceWeights:
    lexical: float
    dense: float
    graph: float
    structural: float

    def as_dict(self) -> dict[str, float]:
        return {
            "lexical": self.lexical,
            "dense": self.dense,
            "graph": self.graph,
            "structural": self.structural,
        }


# Symbol lookup → lexical heavy; conceptual / architecture → dense + graph; workflow → graph + structural
# (AI infra §15 "adaptive weighting"). Tuned later against the retrieval gold set.
INTENT_WEIGHTS: MappingProxyType[RetrievalIntent, SourceWeights] = MappingProxyType(
    {
        RetrievalIntent.OVERVIEW: SourceWeights(lexical=0.8, dense=1.0, graph=0.6, structural=0.8),
        RetrievalIntent.WORKFLOW: SourceWeights(lexical=0.8, dense=0.9, graph=1.2, structural=1.1),
        RetrievalIntent.ARCHITECTURE: SourceWeights(lexical=0.7, dense=1.0, graph=1.1, structural=1.0),
        RetrievalIntent.IMPLEMENTATION_LOCATION: SourceWeights(
            lexical=1.3, dense=0.8, graph=1.0, structural=1.0
        ),
        RetrievalIntent.TECHNICAL_CONCEPT: SourceWeights(lexical=1.0, dense=1.1, graph=0.7, structural=0.6),
        RetrievalIntent.DESIGN_REASONING: SourceWeights(lexical=0.8, dense=1.2, graph=0.7, structural=0.6),
        RetrievalIntent.FAILURE_ANALYSIS: SourceWeights(lexical=1.1, dense=1.0, graph=0.9, structural=0.6),
        RetrievalIntent.SECURITY: SourceWeights(lexical=1.1, dense=1.0, graph=0.8, structural=0.8),
        RetrievalIntent.INTERVIEW_QUESTION: SourceWeights(lexical=0.9, dense=1.1, graph=0.8, structural=0.7),
    }
)

# Relationship types followed when expanding around reranked hits (edge allow-list against graph explosion,
# AI infra §23). Containment / imports are excluded: they connect almost everything to everything.
EXPANSION_REL_TYPES: tuple[str, ...] = (
    "CALLS",
    "HANDLES",
    "INHERITS",
    "IMPLEMENTS",
    "USES",
    "READS_FROM",
    "WRITES_TO",
    "PUBLISHES_TO",
    "CONSUMES_FROM",
    "DEPENDS_ON",
    "EXPOSES",
    "DEPLOYS_TO",
)
# Relationship types the graph retriever walks from seed entities (a little wider than expansion).
GRAPH_RETRIEVAL_REL_TYPES: tuple[str, ...] = (*EXPANSION_REL_TYPES, "IMPORTS", "CONFIGURES")
# Graph labels whose uids map to chunks via `chunks.symbol_id`.
CHUNKABLE_LABELS: frozenset[str] = frozenset({"Function", "Method", "Class", "APIEndpoint", "Schema"})


@dataclass(frozen=True)
class RetrievalConfig:
    version: str = RETRIEVAL_CONFIG_VERSION
    # candidate counts per retriever
    k_lexical: int = 40
    k_dense: int = 40
    k_graph: int = 20
    k_structural: int = 20
    # fusion
    rrf_k: int = 60
    variant_weight: float = 0.6  # weight of secondary query variants inside one retriever (primary = 1.0)
    followup_weight_decay: float = 0.8  # weight multiplier for rankings produced by follow-up rounds
    # reranking
    rerank_top: int = 30
    rerank_batch: int = 16
    rerank_max_chars: int = 2000  # cross-encoder input is truncated (MiniLM max 512 tokens)
    # graph retrieval
    graph_seed_limit: int = 8
    graph_seed_expand: int = 5
    graph_neighbor_limit: int = 30
    graph_file_limit: int = 3
    # graph expansion
    expansion_max_chunks: int = 6
    expansion_seed_items: int = 5
    expansion_depth: int = 1
    expansion_depth_workflow: int = 2
    expansion_neighbor_limit: int = 12
    expansion_rel_types: tuple[str, ...] = EXPANSION_REL_TYPES
    # graph paths (workflow / architecture)
    path_max_entities: int = 4
    path_max_pairs: int = 4
    path_max_hops: int = 4
    # sufficiency / follow-up
    max_followup_rounds: int = 2
    min_term_coverage: float = 0.6
    min_rerank_score: float = -2.0  # ms-marco logits: clearly irrelevant passages score far below 0
    min_fused_ratio: float = (
        0.3  # top fused / best achievable: needs a top-4 hit in a heavy source or agreement
    )
    weights: MappingProxyType[RetrievalIntent, SourceWeights] = field(default=INTENT_WEIGHTS)

    def weights_for(self, intent: RetrievalIntent) -> dict[str, float]:
        return self.weights.get(intent, self.weights[RetrievalIntent.OVERVIEW]).as_dict()

    def expansion_depth_for(self, intent: RetrievalIntent) -> int:
        return self.expansion_depth_workflow if intent is RetrievalIntent.WORKFLOW else self.expansion_depth

    def k_for(self, source: str) -> int:
        return {
            "lexical": self.k_lexical,
            "dense": self.k_dense,
            "graph": self.k_graph,
            "structural": self.k_structural,
        }[source]


DEFAULT_CONFIG = RetrievalConfig()

# Default number of final items by breadth (callers may pass an explicit `k`).
FINAL_K_BY_BREADTH: MappingProxyType[str, int] = MappingProxyType({"narrow": 8, "medium": 12, "broad": 20})


def default_breadth(intent: RetrievalIntent, mode: RetrievalMode) -> Breadth:
    if intent in (RetrievalIntent.OVERVIEW, RetrievalIntent.ARCHITECTURE):
        return "broad"
    if intent is RetrievalIntent.IMPLEMENTATION_LOCATION:
        return "medium" if mode is RetrievalMode.GUIDE else "narrow"
    return "broad" if mode is RetrievalMode.INVESTIGATION else "medium"
