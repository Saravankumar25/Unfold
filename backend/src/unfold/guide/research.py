"""Per-chapter investigation (investigation engine §5, task 3.5): a plain bounded loop, no agent framework.

For every question of a chapter: symbol lookup for the chapter's targets -> hybrid retrieval -> flow trace for
entry-point targets -> test lookup. Findings are built deterministically from stored chunks, the IR and traced flows
(statements are never model prose); coverage = answered / asked. One extra targeted round runs when coverage is
below `COVERAGE_TARGET`. Caps (rounds, retrievals, LLM calls, brief tokens) come from settings and sit inside the
per-job gateway budget. The only model call is decomposing a plan that has fewer than two questions.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, Field

from unfold.context.budget import estimate_tokens
from unfold.core.config import Settings
from unfold.core.errors import ErrorCode, NotFound, UnfoldError
from unfold.core.logging import get_logger
from unfold.domain.enums import ModelCapability, Priority, Proof, RetrievalMode
from unfold.guide.models import ChapterPlan, EdgeRef, Finding, ResearchBrief, ResearchSpent
from unfold.indexing.models import Chunk
from unfold.indexing.store import ChunkRepository
from unfold.indexing.tokenize import tokenize, tokenize_query
from unfold.llm.schemas import LLMClient, LLMRequest, Message
from unfold.retrieval.errors import RetrievalFailed
from unfold.retrieval.hybrid import HybridRetriever
from unfold.retrieval.models import RetrievalResult
from unfold.security.fence import UNTRUSTED_POLICY, untrusted
from unfold.security.secrets import redact_text
from unfold.understand.flows import trace_flow
from unfold.understand.models import ArchitectureModel, FlowTrace, SymbolRef
from unfold.understand.repomap import RepoMap

log = get_logger(__name__)

COVERAGE_TARGET = 0.7
FINDINGS_PER_QUESTION = 3
MAX_FLOWS = 3
MAX_TESTS = 5
MAX_FOCUS_CHUNKS = 6
FILE_CHUNKS = 3
_RANK = {Proof.STATIC: 0, Proof.HEURISTIC: 1, Proof.INFERRED: 2}
_GENERIC = frozenset(
    {"project", "repository", "code", "file", "files", "part", "parts", "main", "used", "use", "using"}
    | {"work", "works", "each", "one", "call", "calls", "order", "inside", "happens", "exist", "exists"}
)
_FLOWISH = re.compile(
    r"\b(flow|request|lifecycle|happens?|pass(es)? through|call|order|sequence|stages?)\b", re.I
)
_SENTENCE = re.compile(r"(?<=[.!?])\s")


@dataclass(frozen=True)
class ResearchCaps:
    """Per-chapter limits; the defaults live in `Settings` only. `rounds` is the first pass plus at most one
    targeted extra round (the setting refuses more)."""

    rounds: int
    retrievals: int
    llm_calls: int
    brief_tokens: int

    @classmethod
    def from_settings(cls, s: Settings) -> ResearchCaps:
        return cls(
            rounds=s.research_max_rounds,
            retrievals=s.research_max_retrievals,
            llm_calls=s.research_max_llm_calls,
            brief_tokens=s.research_brief_tokens,
        )


class QuestionSet(BaseModel):
    questions: list[str] = Field(default_factory=list)


def render_brief(brief: ResearchBrief, labels: Mapping[str, str] | None = None, *, steps: int = 10) -> str:
    """Text form of a brief for the writer prompt (and for its token cap). `labels` maps chunk id -> `E#`;
    without it a fixed-width placeholder is used so the size can be estimated before labels exist."""

    def cites(ids: Sequence[str]) -> str:
        found = [labels[i] for i in ids if labels and i in labels] if labels else ["E00"] * min(len(ids), 3)
        return "".join(f"[{x}]" for x in dict.fromkeys(found))

    lines: list[str] = []
    for qid, q in enumerate(brief.questions):
        lines.append(f"Q{qid + 1}: {q}")
        mine = [f for f in brief.findings if f.question_id == qid]
        lines += [f"  - {f.statement} {cites(f.evidence)} (proof: {f.proof.value})" for f in mine]
        if not mine:
            lines.append("  - (no finding)")
    for flow in brief.flows:
        names = [s.symbol.name for s in flow.steps]
        shown = " -> ".join(names[:steps]) + (
            f" -> ... (+{len(names) - steps} more)" if len(names) > steps else ""
        )
        extra = "; some calls were not resolved: " + ", ".join(flow.unresolved[:3]) if flow.unresolved else ""
        cut = "; trace was cut at its depth or fan-out cap" if flow.truncated else ""
        lines.append(f"Flow from {flow.steps[0].note or flow.entry.name}: {shown}{extra}{cut}")
    if brief.tests:
        lines.append("Tests that exercise these targets: " + ", ".join(brief.tests))
    if brief.open_questions:
        lines.append(
            "Not established by the research (say so plainly, do not guess): "
            + " | ".join(brief.open_questions)
        )
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


class Investigator:
    def __init__(
        self,
        *,
        llm: LLMClient,
        retriever: HybridRetriever,
        chunks: ChunkRepository,
        rm: RepoMap,
        arch: ArchitectureModel,
        caps: ResearchCaps,
        project_id: str,
        job_id: str,
    ) -> None:
        self.llm, self.retriever, self.chunks, self.rm, self.arch = llm, retriever, chunks, rm, arch
        self.caps, self.project_id, self.job_id = caps, project_id, job_id
        self._sym = {s.id: s for s in rm.ir.symbols}
        self._file = {f.id: f for f in rm.ir.files}
        self._by_path = {r.path: r for r in rm.files()}
        self._comp = {c.id: c for c in arch.components}

    # --- targets ------------------------------------------------------------------------------------------

    def _target_refs(self, targets: Sequence[str], avoid: Collection[str] = ()) -> list[SymbolRef]:
        """`avoid`: files other chapters name as targets; a component's files are picked from the rest first, so
        two chapters that both cover a component do not read the same four files."""
        out: dict[str, SymbolRef] = {}
        for t in targets:
            comp = self._comp.get(t)
            if comp is not None:
                known = [p for p in comp.member_paths if p in self._by_path]
                paths = sorted(
                    [p for p in known if p not in avoid] or known,
                    key=lambda p: (-self._by_path[p].end_line, p),
                )
                for p in paths[:4]:
                    out.setdefault(self._by_path[p].id, self._by_path[p])
            elif (ref := self.rm.ref(t)) is not None:
                out.setdefault(ref.id, ref)
        return list(out.values())

    async def _chunks_of(self, ref: SymbolRef) -> list[Chunk]:
        if ref.kind == "file":
            found = await self.chunks.by_path(self.project_id, ref.path)
            code = [c for c in found if c.symbol_id] or found
            return sorted(code, key=lambda c: (-c.importance, c.start_line))[:FILE_CHUNKS]
        found = await self.chunks.for_symbols(self.project_id, [ref.id])
        if found:
            return found[:FILE_CHUNKS]
        near = await self.chunks.by_path(
            self.project_id, ref.path
        )  # endpoints/schemas: chunk by line overlap
        return [c for c in near if c.start_line <= ref.end_line and c.end_line >= ref.start_line][
            :FILE_CHUNKS
        ]

    # --- evidence -> statement ----------------------------------------------------------------------------

    def _describe(self, c: Chunk) -> tuple[str, Proof, list[EdgeRef]]:
        loc = f"{c.path}:{c.start_line}-{c.end_line}"
        sym = self._sym.get(c.symbol_id or "")
        if sym is None:
            what = c.symbol_name or c.content_type.value
            return f"`{_clip(what, 80)}` ({c.kind}) at {loc}", Proof.STATIC, []
        parts = [f"`{_clip(sym.qualname, 80)}` ({sym.kind}) at {loc}"]
        if sym.signature:
            parts.append(f"signature `{_clip(sym.signature, 120)}`")
        if sym.docstring:
            parts.append(_clip(_SENTENCE.split(sym.docstring.strip(), maxsplit=1)[0], 160))
        if label := self.rm.entry_label(sym.id):
            parts.append(f"entry point: {label}")
        proof = Proof.STATIC
        calls: dict[str, EdgeRef] = {}
        for e in self.rm.out_edges(sym.id):
            ref = self.rm.ref(e.dst)
            if e.rel == "CALLS" and e.proof != Proof.INFERRED and ref and ref.name not in calls:
                calls[ref.name] = EdgeRef(src=sym.id, dst=ref.id, src_name=sym.qualname, dst_name=ref.name)
                if _RANK[e.proof] > _RANK[proof]:
                    proof = e.proof
        shown = list(calls)[:4]
        if shown:
            parts.append("calls " + ", ".join(f"`{_clip(n, 60)}`" for n in shown))
        return "; ".join(parts), proof, [calls[n] for n in shown]

    # --- one question -------------------------------------------------------------------------------------

    def _focus(self, question: str, refs: list[SymbolRef]) -> tuple[list[SymbolRef], bool]:
        """Targets the question names (by symbol name or file path), else all of them."""
        low = question.lower()
        hit = [
            r
            for r in refs
            if r.name.lower() in low or r.path.lower() in low or r.name.rsplit(".", 1)[-1].lower() in low
        ]
        return (hit, True) if hit else (refs, False)

    def _findings(
        self,
        qid: int,
        question: str,
        lookup: list[Chunk],
        named: bool,
        retrieved: RetrievalResult | None,
    ) -> tuple[list[Finding], bool]:
        """(findings, answered). A lookup chunk answers when the question names its target or shares terms with it;
        a retrieved chunk answers when it shares terms and retrieval was judged sufficient."""
        qt = {t for t in tokenize_query(question) if t not in _GENERIC}
        need = 1 if len(qt) <= 3 else 2
        pool: list[tuple[Chunk, bool]] = [(c, True) for c in lookup]
        if retrieved is not None:
            pool += [(i.chunk, False) for i in retrieved.items if not i.via_expansion]
            pool += [(i.chunk, False) for i in retrieved.items if i.via_expansion]
        scored: list[tuple[int, int, int, str, Chunk, Proof, list[EdgeRef]]] = []
        seen: set[str] = set()
        for order, (c, from_lookup) in enumerate(pool):
            if c.id in seen:
                continue
            seen.add(c.id)
            overlap = len(qt & set(tokenize(f"{c.path} {c.symbol_name or ''} {c.text[:1500]}")))
            statement, statement_proof, edges = self._describe(c)
            if from_lookup and named:
                proof = statement_proof
            else:
                proof = Proof.HEURISTIC if overlap >= need else Proof.INFERRED
            scored.append((_RANK[proof], -overlap, order, statement, c, proof, edges))
        scored.sort(key=lambda t: t[:3])
        findings = [
            Finding(question_id=qid, statement=statement, evidence=[c.id], proof=proof, edges=edges)
            for _, _, _, statement, c, proof, edges in scored[:FINDINGS_PER_QUESTION]
        ]
        solid = [f for f in findings if f.proof is not Proof.INFERRED]
        sufficient = retrieved is None or retrieved.sufficient
        by_lookup = {c.id for c in lookup} if named else set()
        answered = any(f.evidence[0] in by_lookup or sufficient for f in solid)
        return findings, answered

    # --- decomposition (the only model call) ------------------------------------------------------------------

    async def _decompose(self, plan: ChapterPlan, refs: list[SymbolRef]) -> list[str]:
        body = f"Chapter: {plan.title}\nObjective: {plan.objective}\nTargets: " + ", ".join(
            r.name for r in refs[:8]
        )
        req = LLMRequest(
            purpose="guide.research",
            capability=ModelCapability.FAST,
            priority=Priority.P1,
            system=(
                "Split a guide chapter's objective into 3-5 short research questions that can be answered by "
                "reading the named code: what it is, how it is called, what it reads or writes, what happens on "
                "failure.\n\n" + UNTRUSTED_POLICY
            ),
            messages=[Message(role="user", content=untrusted(redact_text(body), source="chapter_plan"))],
            temperature=0.2,
            max_output_tokens=500,
            cacheable=True,
            job_id=self.job_id,
            project_id=self.project_id,
        )
        out = await self.llm.generate_structured(req, QuestionSet)
        return [q for q in (_clip(q, 200) for q in out.questions) if q][:6]

    # --- flows and tests ----------------------------------------------------------------------------------

    async def _flows(
        self, refs: list[SymbolRef], questions: list[str]
    ) -> tuple[list[FlowTrace], list[Finding]]:
        flows: list[FlowTrace] = []
        findings: list[Finding] = []
        for ref in refs:
            if len(flows) >= MAX_FLOWS:
                break
            if self.rm.entry_label(ref.id) is None:
                continue
            try:
                flow = trace_flow(self.rm, ref.id)
            except NotFound:
                continue
            if len(flow.steps) < 2:
                continue
            flows.append(flow)
            step_ids = [s.symbol.id for s in flow.steps if s.symbol.kind != "file"][:8]
            evidence = [c.id for c in await self.chunks.for_symbols(self.project_id, step_ids)][:6]
            proofs = [s.via.proof for s in flow.steps if s.via] or [Proof.STATIC]
            names = " -> ".join(s.symbol.name for s in flow.steps[:8])
            label = flow.steps[0].note or flow.entry.name
            qid = next((i for i, q in enumerate(questions) if _FLOWISH.search(q)), 0)
            if evidence:
                findings.append(
                    Finding(
                        question_id=qid,
                        statement=f"Flow from `{_clip(label, 80)}`: {names} ({len(flow.steps)} steps)",
                        evidence=evidence,
                        proof=max(proofs, key=lambda p: _RANK[p]),
                    )
                )
        return flows, findings

    def _tests(self, refs: list[SymbolRef]) -> list[str]:
        tests = [r for r in self._by_path.values() if self._file[r.id].role == "test"]
        if not tests:
            return []
        out: set[str] = set()
        for ref in refs:
            file_id = ref.id if ref.kind == "file" else self.rm.file_of(ref.id)
            file_ref = self.rm.ref(file_id) if file_id else None
            if file_ref is None:
                continue
            stem = file_ref.path.rsplit("/", 1)[-1].split(".")[0].lower()
            importers = {
                e.src for e in self.rm.neighbours(file_ref.id, rels=("IMPORTS",)) if e.dst == file_ref.id
            }
            for t in tests:
                base = t.path.rsplit("/", 1)[-1].split(".")[0].lower()
                if t.id in importers or (len(stem) > 2 and stem in base):
                    out.add(t.path)
        return sorted(out)[:MAX_TESTS]

    # --- the loop -----------------------------------------------------------------------------------------

    async def research(self, plan: ChapterPlan, avoid: Collection[str] = ()) -> ResearchBrief:
        caps = self.caps
        spent = ResearchSpent()
        stopped: str | None = None
        refs = self._target_refs(plan.targets, avoid)
        questions = list(plan.questions)
        if len(questions) < 2:
            if spent.llm_calls < caps.llm_calls:
                spent.llm_calls += 1
                try:
                    questions += await self._decompose(plan, refs)
                except UnfoldError as exc:  # the plan's own question stands in for a refused or invalid split
                    if exc.code not in (ErrorCode.INVALID_MODEL_OUTPUT, ErrorCode.LLM_BUDGET_EXHAUSTED):
                        raise
                    stopped = "budget" if exc.code is ErrorCode.LLM_BUDGET_EXHAUSTED else None
            else:
                stopped = "llm_calls"
        questions = list(dict.fromkeys(questions)) or [plan.objective]

        target_chunks = {r.id: await self._chunks_of(r) for r in refs}
        flows, flow_findings = await self._flows(refs, questions)
        findings: list[Finding] = list(flow_findings)
        answered: set[int] = {f.question_id for f in flow_findings if f.proof is not Proof.INFERRED}
        facets: dict[int, str] = {}

        for round_no in range(caps.rounds):
            # the extra round is targeted: a question without a missing facet would only repeat its query
            todo = [i for i in range(len(questions)) if i not in answered and (not round_no or facets.get(i))]
            if not todo:
                stopped = stopped or ("exhausted" if round_no else None)
                break
            spent.rounds += 1
            for qid in todo:
                focus, named = self._focus(questions[qid], refs)
                lookup = [c for r in focus for c in target_chunks.get(r.id, [])][:MAX_FOCUS_CHUNKS]
                # the target lookup is free: when it already answers a question that names its target, no retrieval
                found, ok = self._findings(qid, questions[qid], lookup, named, None) if named else ([], False)
                result: RetrievalResult | None = None
                if not ok:
                    if spent.retrievals >= caps.retrievals:
                        stopped = "retrievals"
                        break
                    query = " ".join(
                        [questions[qid], *(r.name for r in focus[:3]), facets.get(qid, "")]
                    ).strip()
                    spent.retrievals += 1
                    with contextlib.suppress(RetrievalFailed):  # the lookup chunks still answer what they can
                        result = await self.retriever.search(query, RetrievalMode.GUIDE)
                    found, ok = self._findings(qid, questions[qid], lookup, named, result)
                have = {(f.question_id, f.evidence[0]) for f in findings}
                findings += [f for f in found if (f.question_id, f.evidence[0]) not in have]
                if ok:
                    answered.add(qid)
                elif result is not None:
                    suff = result.trace.sufficiency
                    missing = [*(suff.get("missing_identifiers") or []), *(suff.get("missing_paths") or [])]
                    kinds = [k for k in plan.expected_evidence if k not in ("definition", "flow")]
                    facets[qid] = " ".join([*(str(m) for m in missing[:4]), *kinds[:2]])
            if stopped == "retrievals":
                break
            if len(answered) / len(questions) >= COVERAGE_TARGET:
                stopped = stopped or "coverage"
                break
        else:
            stopped = stopped or ("rounds" if len(answered) < len(questions) else None)
        if stopped is None and len(answered) / len(questions) >= COVERAGE_TARGET:
            stopped = "coverage"

        brief = self._fit(
            ResearchBrief(
                chapter_key=plan.key,
                questions=questions,
                findings=[],
                flows=flows,
                tests=self._tests(refs),
                open_questions=[],
                coverage=0.0,
                spent=spent,
                stopped_by=stopped,
            ),
            findings,
            answered,
        )
        log.info(
            "research.done",
            chapter=plan.key,
            coverage=brief.coverage,
            findings=len(brief.findings),
            rounds=brief.spent.rounds,
            retrievals=brief.spent.retrievals,
            stopped_by=brief.stopped_by,
        )
        return brief

    def _assemble(self, brief: ResearchBrief, findings: list[Finding], answered: set[int]) -> ResearchBrief:
        solid = answered & {f.question_id for f in findings if f.proof is not Proof.INFERRED}
        ordered = sorted(findings, key=lambda f: (f.question_id, _RANK[f.proof]))
        out = brief.model_copy(
            update={
                "findings": ordered,
                "coverage": round(len(solid) / len(brief.questions), 2),
                "open_questions": [q for i, q in enumerate(brief.questions) if i not in solid],
            }
        )
        tokens = estimate_tokens(render_brief(out))
        return out.model_copy(update={"spent": out.spent.model_copy(update={"tokens": tokens})})

    def _fit(self, brief: ResearchBrief, findings: list[Finding], answered: set[int]) -> ResearchBrief:
        """Drop the least trusted findings until the rendered brief fits the token cap; coverage and open
        questions are computed from what remains, so a cut is never reported as answered."""
        ranked = sorted(findings, key=lambda f: (_RANK[f.proof], f.question_id))  # most trusted first
        keep = len(ranked)
        out = self._assemble(brief, ranked, answered)
        while out.spent.tokens > self.caps.brief_tokens and keep > 0:
            keep -= 1
            out = self._assemble(brief, ranked[:keep], answered).model_copy(update={"stopped_by": "tokens"})
        if out.spent.tokens > self.caps.brief_tokens:  # flows and tests alone exceed the cap
            out = self._assemble(brief.model_copy(update={"flows": [], "tests": []}), [], answered)
            out = out.model_copy(update={"stopped_by": "tokens"})
        return out
