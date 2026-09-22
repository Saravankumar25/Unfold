"""Deterministic project complexity profile (LLD §8, AI infrastructure §31–§32).

`score(ir)` turns IR signals (code files, LOC, languages, services, data stores, infrastructure, AI presence,
relationship count) into a 0–100 score, a size level, the investigators worth running, the guide's target read
time (PRD: 45–60 minutes), retrieval breadth and per-agent budgets. Same IR → same profile; no LLM involved.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field

from unfold.agents.models import InvestigatorKind
from unfold.project_ir.builder import CODE_LANGUAGES
from unfold.project_ir.schema import ProjectIR

__all__ = ["AgentBudget", "ComplexityProfile", "ComplexitySignals", "RetrievalBreadth", "score"]

Level = Literal["small", "medium", "large"]

MIN_READ_MINUTES = 45
MAX_READ_MINUTES = 60
SMALL_BELOW = 30.0
LARGE_FROM = 65.0

# (weight, reference-at-full-weight). Size signals are log-scaled from a floor so tiny repos score ~0.
_W_FILES, _FILES_FLOOR, _FILES_FULL = 25.0, 10, 5000
_W_LOC, _LOC_FLOOR, _LOC_FULL = 25.0, 1_000, 500_000
_W_RELS, _RELS_FLOOR, _RELS_FULL = 12.0, 100, 100_000
_W_LANGS, _LANGS_FULL = 8.0, 5
_W_SERVICES, _SERVICES_FULL = 8.0, 8
_W_STORES, _STORES_FULL = 6.0, 4
_W_INFRA, _INFRA_FULL = 6.0, 15
_W_AI, _AI_FULL = 5.0, 6
_W_ENDPOINTS, _ENDPOINTS_FULL = 5.0, 60

_INVESTIGATOR_ORDER: tuple[InvestigatorKind, ...] = (
    "architecture",
    "workflow",
    "data",
    "ai_ml",
    "infrastructure",
    "security",
)
_MAX_INVESTIGATORS: dict[Level, int] = {"small": 4, "medium": 6, "large": 6}


class ComplexitySignals(BaseModel):
    code_files: int
    code_lines: int
    languages: int
    modules: int
    symbols: int
    endpoints: int
    schemas: int
    services: int
    data_stores: int
    queues: int
    infrastructure: int
    ai_components: int
    relationships: int


class RetrievalBreadth(BaseModel):
    """Per-query retrieval fan-out: candidates per channel, rerank window, graph expansion, follow-ups."""

    candidate_k: int
    rerank_top: int
    graph_depth: int
    max_follow_up_rounds: int


class AgentBudget(BaseModel):
    max_llm_calls: int
    max_tool_calls: int


class ComplexityProfile(BaseModel):
    level: Level
    score: float  # 0–100
    investigators: list[InvestigatorKind] = Field(default_factory=list)
    target_read_minutes: int
    retrieval_breadth: RetrievalBreadth
    agent_budget: AgentBudget
    signals: ComplexitySignals


_BREADTH: dict[Level, RetrievalBreadth] = {
    "small": RetrievalBreadth(candidate_k=30, rerank_top=20, graph_depth=1, max_follow_up_rounds=1),
    "medium": RetrievalBreadth(candidate_k=40, rerank_top=30, graph_depth=1, max_follow_up_rounds=2),
    "large": RetrievalBreadth(candidate_k=60, rerank_top=40, graph_depth=2, max_follow_up_rounds=2),
}
_BUDGET: dict[Level, AgentBudget] = {
    "small": AgentBudget(max_llm_calls=8, max_tool_calls=18),
    "medium": AgentBudget(max_llm_calls=10, max_tool_calls=24),
    "large": AgentBudget(max_llm_calls=14, max_tool_calls=36),
}


def _log_ratio(value: int, floor: int, full: int) -> float:
    if value <= floor:
        return 0.0
    return min(1.0, (math.log10(value) - math.log10(floor)) / (math.log10(full) - math.log10(floor)))


def _lin(value: int, full: int) -> float:
    return min(1.0, max(0, value) / full)


def signals(ir: ProjectIR) -> ComplexitySignals:
    """Size signals; falls back to raw IR counts when `ir.stats` lacks the builder's code counters."""
    code = [f for f in ir.files if f.tier < 4 and (f.language or "").lower() in CODE_LANGUAGES]
    stats = ir.stats or {}
    code_files = int(stats.get("code_files", len(code)))
    code_lines = int(stats.get("code_lines", sum(f.lines for f in code)))
    languages = int(stats.get("code_languages", len({(f.language or "").lower() for f in code})))
    return ComplexitySignals(
        code_files=code_files,
        code_lines=code_lines,
        languages=languages,
        modules=len(ir.modules),
        symbols=len(ir.symbols),
        endpoints=len(ir.endpoints),
        schemas=len(ir.schemas),
        services=len(ir.services),
        data_stores=len(ir.data_stores),
        queues=len(ir.queues),
        infrastructure=len(ir.infrastructure),
        ai_components=len(ir.ai_components),
        relationships=len(ir.relationships),
    )


def _investigators(ir: ProjectIR, sig: ComplexitySignals, level: Level) -> list[InvestigatorKind]:
    web_facing = sig.endpoints > 0 or any(s.service_kind in ("api", "web", "proxy") for s in ir.services)
    relevant: dict[InvestigatorKind, bool] = {
        "architecture": True,
        "workflow": sig.code_files >= 3 or sig.endpoints > 0 or sig.queues > 0,
        "data": sig.schemas > 0 or sig.data_stores > 0 or sig.queues > 0,
        "ai_ml": sig.ai_components > 0,
        "infrastructure": sig.infrastructure > 0 or any(s.image for s in ir.services),
        "security": web_facing or sig.data_stores > 0 or level != "small",
    }
    chosen = [k for k in _INVESTIGATOR_ORDER if relevant[k]]
    return chosen[: _MAX_INVESTIGATORS[level]]


def score(ir: ProjectIR) -> ComplexityProfile:
    sig = signals(ir)
    total = (
        _W_FILES * _log_ratio(sig.code_files, _FILES_FLOOR, _FILES_FULL)
        + _W_LOC * _log_ratio(sig.code_lines, _LOC_FLOOR, _LOC_FULL)
        + _W_RELS * _log_ratio(sig.relationships, _RELS_FLOOR, _RELS_FULL)
        + _W_LANGS * _lin(sig.languages - 1, _LANGS_FULL - 1)
        + _W_SERVICES * _lin(sig.services, _SERVICES_FULL)
        + _W_STORES * _lin(sig.data_stores + sig.queues, _STORES_FULL)
        + _W_INFRA * _lin(sig.infrastructure, _INFRA_FULL)
        + _W_AI * _lin(sig.ai_components, _AI_FULL)
        + _W_ENDPOINTS * _lin(sig.endpoints, _ENDPOINTS_FULL)
    )
    value = round(min(100.0, total), 2)
    level: Level = "small" if value < SMALL_BELOW else "medium" if value < LARGE_FROM else "large"
    minutes = MIN_READ_MINUTES + round((MAX_READ_MINUTES - MIN_READ_MINUTES) * value / 100.0)
    return ComplexityProfile(
        level=level,
        score=value,
        investigators=_investigators(ir, sig, level),
        target_read_minutes=max(MIN_READ_MINUTES, min(MAX_READ_MINUTES, minutes)),
        retrieval_breadth=_BREADTH[level].model_copy(),
        agent_budget=_BUDGET[level].model_copy(),
        signals=sig,
    )
