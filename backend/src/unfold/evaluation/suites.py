"""Offline evaluation suites (LLD §23): groundedness, retrieval, graph usefulness, prompt injection.

Every suite is deterministic and needs no datastore, network or model key. Gold data lives in `evals/gold/*.json`
(hand-written judgements about the fixture repositories in `evals/fixtures/repos`); each suite returns plain
numbers that `offline.py` compares with `evals/baseline.json`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from unfold.chat.answer import AnswerRequest, generate_answer
from unfold.domain.enums import ClaimKind, RetrievalMode
from unfold.domain.models import ClaimRecord
from unfold.evaluation.corpus import GOLD, FixtureProject, load_fixture
from unfold.evaluation.scripted import ScriptedLLM, extractive_answer
from unfold.graph.communities import detect
from unfold.guide.generate import grade_claims
from unfold.llm.schemas import LLMRequest
from unfold.project_ir.schema import EndpointIR, Entity
from unfold.security.fence import UNTRUSTED_POLICY, untrusted
from unfold.security.injection import scan

REPOS = ("fastapi_rag", "express_ts_prisma", "terraform_k8s")
RETRIEVAL_K = 8


@dataclass
class SuiteResult:
    name: str
    metrics: dict[str, float] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)  # individual cases that missed, for the report


@cache
def _fixture(name: str) -> FixtureProject:
    return load_fixture(name)


def _gold(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((GOLD / f"{name}.json").read_text(encoding="utf-8"))
    return data


def _ratio(num: float, den: float) -> float:
    return round(num / den, 4) if den else 0.0


# --- groundedness --------------------------------------------------------------------------------------------


async def groundedness() -> SuiteResult:
    """Does the deterministic grader agree with hand-labelled claims, and are chat answers grounded?"""
    out = SuiteResult("groundedness")
    agree = total = halluc = halluc_verified = 0
    for repo in REPOS:
        fx = _fixture(repo)
        by_id = {c.id: c.text for c in fx.chunks}
        for item in _gold(repo)["claims"]:
            ids = [cid for path in item["cite"] for cid in fx.chunk_ids_for(path)]
            claim = ClaimRecord(text=item["text"], kind=ClaimKind(item["kind"]), evidence_ids=ids)
            (graded,), _ = grade_claims([claim], {i: by_id[i] for i in ids})
            total += 1
            agree += graded.status.value == item["expect"]
            if graded.status.value != item["expect"]:
                out.failures.append(f"{repo}: {item['text'][:60]} -> {graded.status.value}")
            if item.get("hallucinated"):
                halluc += 1
                halluc_verified += graded.status.value == "VERIFIED"
    out.metrics["claim_grade_accuracy"] = _ratio(agree, total)
    out.metrics["hallucination_verified_rate"] = _ratio(halluc_verified, halluc)

    verified = graded_total = answered = valid_cites = cases = 0
    for repo in REPOS:
        fx = _fixture(repo)
        for question in _gold(repo)["chat"]:
            cases += 1
            done = await _run_answer(fx, question, ScriptedLLM())
            grounding = done.grounding or {}
            claims = int(grounding.get("claims", 0))
            verified += int(grounding.get("verified", 0))
            graded_total += claims
            answered += bool(done.evidence)
            valid_cites += int(grounding.get("invalid_citations", 0)) == 0 and claims > 0
    out.metrics["chat_grounded_ratio"] = _ratio(verified, graded_total)
    out.metrics["chat_answered_with_evidence"] = _ratio(answered, cases)
    out.metrics["chat_citations_valid"] = _ratio(valid_cites, cases)
    return out


async def _run_answer(fx: FixtureProject, question: str, llm: ScriptedLLM, **extra: Any) -> Any:
    request = AnswerRequest(chat_id="eval", project_id=fx.project_id, question=question, **extra)
    done = None
    async for event in generate_answer(llm, fx.retriever(), request, context_tokens=6000, transport="eval"):
        if event.type == "response.completed":
            done = event
    assert done is not None
    return done


# --- retrieval -----------------------------------------------------------------------------------------------


async def retrieval() -> SuiteResult:
    out = SuiteResult("retrieval")
    hits = total = 0
    reciprocal = 0.0
    for repo in REPOS:
        fx = _fixture(repo)
        retriever = fx.retriever()
        for item in _gold(repo)["retrieval"]:
            total += 1
            result = await retriever.search(item["q"], RetrievalMode.CHAT, k=RETRIEVAL_K)
            paths = [it.chunk.path for it in result.items[:RETRIEVAL_K]]
            rank = next((i for i, p in enumerate(paths, start=1) if p in item["expect"]), None)
            if rank is None:
                out.failures.append(f"{repo}: {item['q']} -> {paths[:3]}")
                continue
            hits += 1
            reciprocal += 1.0 / rank
    out.metrics[f"hit_rate_at_{RETRIEVAL_K}"] = _ratio(hits, total)
    out.metrics["mrr"] = round(reciprocal / total, 4) if total else 0.0
    return out


# --- graph usefulness ----------------------------------------------------------------------------------------


def _label(e: Entity) -> str:
    return f"{e.method} {e.route}" if isinstance(e, EndpointIR) else e.name


async def graph() -> SuiteResult:
    """Are the relationships a reader would ask about present, and do subsystems group files sensibly?"""
    out = SuiteResult("graph")
    found = expected = satisfied = constraints = 0
    for repo in REPOS:
        fx = _fixture(repo)
        gold = _gold(repo)
        index = fx.ir.entity_index()
        edges = {
            (str(r.type), _label(index[r.source_id]), _label(index[r.target_id]))
            for r in fx.ir.relationships
            if r.source_id in index and r.target_id in index
        }
        for rel in gold["relations"]:
            expected += 1
            if (rel["type"], rel["source"], rel["target"]) in edges:
                found += 1
            else:
                out.failures.append(f"{repo}: missing {rel['type']} {rel['source']} -> {rel['target']}")

        path_of = {f.id: f.path for f in fx.ir.files}
        group_of = {
            path_of[fid]: sub.id for sub in detect(fx.ir) for fid in sub.member_file_ids if fid in path_of
        }
        for a, b in gold["subsystems"]["together"]:
            constraints += 1
            ok = a in group_of and group_of.get(a) == group_of.get(b)
            satisfied += ok
            if not ok:
                out.failures.append(f"{repo}: {a} and {b} should share a subsystem")
        for a, b in gold["subsystems"]["apart"]:
            constraints += 1
            ok = a in group_of and b in group_of and group_of[a] != group_of[b]
            satisfied += ok
            if not ok:
                out.failures.append(f"{repo}: {a} and {b} should be in different subsystems")
    out.metrics["relation_recall"] = _ratio(found, expected)
    out.metrics["subsystem_agreement"] = _ratio(satisfied, constraints)
    return out


# --- prompt injection ----------------------------------------------------------------------------------------

_OPEN = re.compile(r"<repository_data[ >]", re.I)
_CLOSE = re.compile(r"</repository_data>", re.I)
_FENCED = re.compile(r"<repository_data[^>]*>.*?</repository_data>", re.S)
LEAK = "sk-leakedleakedleakedleaked123456789"


def _leaky(req: LLMRequest) -> str:
    """A model that also prints a secret-shaped string: output redaction must catch it."""
    return f"{extractive_answer(req)} (token: {LEAK})"


async def _chat_case(case: dict[str, Any]) -> list[str]:
    """Return the invariants this case violated (empty = safe)."""
    fx = _fixture(case["repo"])
    llm = ScriptedLLM(_leaky)
    done = await _run_answer(fx, case["question"], llm)
    problems: list[str] = []
    if not llm.requests:
        return ["no model call was made (retrieval found nothing)"]
    req = llm.requests[-1]
    prompt = req.messages[-1].content
    everything = req.system + "\n" + prompt
    if UNTRUSTED_POLICY not in req.system:
        problems.append("system prompt lacks the untrusted-data policy")
    if 'source="user_question"' not in prompt:
        problems.append("the question is not fenced")
    if len(_OPEN.findall(prompt)) != len(_CLOSE.findall(prompt)):
        problems.append("fence tags are unbalanced")
    secret = case.get("secret")
    if secret and secret in everything:
        problems.append("a secret reached the prompt")
    marker = case.get("fenced_marker")
    if marker and (marker not in prompt or marker in _FENCED.sub("", prompt)):
        problems.append("repository text escaped the fence (or was never retrieved)")
    if LEAK in (done.text or ""):
        problems.append("a secret-shaped model output was not redacted")
    return problems


async def injection() -> SuiteResult:
    out = SuiteResult("injection")
    gold = _gold("injection")
    caught = sum(
        {rule for s in scan(a["text"]) for rule in [s.rule]} >= set(a["rules"]) for a in gold["attacks"]
    )
    flagged = sum(bool(scan(text)) for text in gold["benign"])
    out.metrics["scanner_recall"] = _ratio(caught, len(gold["attacks"]))
    out.metrics["scanner_false_positive_rate"] = _ratio(flagged, len(gold["benign"]))
    for a in gold["attacks"]:
        if not {s.rule for s in scan(a["text"])} >= set(a["rules"]):
            out.failures.append(f"scanner missed {a['id']}")

    intact = 0
    for text in gold["fence_escapes"]:
        wrapped = untrusted(text, "eval")
        ok = len(_OPEN.findall(wrapped)) == 1 and len(_CLOSE.findall(wrapped)) == 1
        intact += ok
        if not ok:
            out.failures.append(f"fence escape not neutralised: {text[:40]!r}")
    out.metrics["fence_integrity"] = _ratio(intact, len(gold["fence_escapes"]))

    safe = 0
    for case in gold["chat_cases"]:
        problems = await _chat_case(case)
        safe += not problems
        out.failures += [f"chat {case['id']}: {p}" for p in problems]
    out.metrics["chat_prompt_safety"] = _ratio(safe, len(gold["chat_cases"]))
    return out


SUITES: dict[str, Callable[[], Awaitable[SuiteResult]]] = {
    "groundedness": groundedness,
    "retrieval": retrieval,
    "graph": graph,
    "injection": injection,
}
