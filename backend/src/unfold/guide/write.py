"""Chapter writer (investigation engine §5, task 3.6): writes from the research brief, not from a chunk dump.

The prompt carries the compiled evidence excerpts (fetched by the brief's chunk ids; redacted, fenced, `[E#]`
labelled by `compile_context`), the rendered brief (findings with their labels, flows, tests, open questions), one
line per chapter this one depends on, and compact facts. Output is the same `SectionDraft` the verifier and the
frontend already consume.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from unfold.context.budget import estimate_tokens
from unfold.context.compiler import compile_context
from unfold.context.models import safe_header_field
from unfold.core.errors import ErrorCode, GenerationError
from unfold.core.logging import get_logger
from unfold.domain.enums import ClaimKind, ModelCapability, Priority, Proof, RetrievalMode
from unfold.domain.models import ClaimRecord, SessionProfile
from unfold.guide.models import (
    Block,
    ChapterPlan,
    CodeBlock,
    DiagramBlock,
    EdgeRef,
    ResearchBrief,
    SectionContent,
    SectionDraft,
)
from unfold.guide.research import render_brief
from unfold.indexing.models import Chunk
from unfold.indexing.store import ChunkRepository
from unfold.llm.schemas import LLMClient, LLMRequest, Message
from unfold.retrieval.models import RetrievalPlan, RetrievalResult, RetrievalTrace, RetrievedItem
from unfold.security.fence import UNTRUSTED_POLICY, untrusted
from unfold.security.secrets import redact_text

log = get_logger(__name__)

SECTION_CONTEXT_TOKENS = 9_000  # brief + excerpts + facts
MIN_EXCERPT_TOKENS = 2_000
WRITER_PROMPT_VERSION = "5"
_RELATION = re.compile(
    r"\b(calls?|invokes?|imports?|reads?|writes?|queries|uses?|depends on|delegates? to|hands|forwards?|"
    r"enqueues?|publishes?|consumes?|passes)\b",
    re.I,
)

_WRITER_SYSTEM = (
    "You write one chapter of a reading guide that teaches a software repository. You are given a research brief "
    "(what was found for each question, with the evidence labels that support it, traced flows, tests, and "
    "questions that were NOT established) and the evidence excerpts. Use ONLY the brief, the excerpts and the "
    "verified facts. Every paragraph, bullet and callout that states a fact about the code ends with an evidence "
    "label, e.g. [E3], or a `path:L12-L40` location. Every code block quotes the excerpt verbatim (no ellipses) "
    "and sets `path` and `start_line` (the file line of its first line). For every claim "
    "about the project add an entry to `claims` whose text is the sentence of your chapter that makes it, in the "
    "chapter's own words, with the labels that support it in `evidence`; use kind "
    "'project_fact' for things the code shows, 'inference' for reasoned conclusions, 'improvement' for "
    "suggestions. Where the brief lists a question as not established, or the evidence does not cover something, "
    'say so plainly, one sentence per gap (for example: "The evidence does not show how X is retried."), instead '
    "of guessing. Explain how things work in order: when the brief contains a traced flow, write it as an ordered "
    "bullets block (ordered=true), one step per item in execution order, naming each real symbol and file. Add a "
    "Q&A block with kind 'failure' (what can go wrong) only when a finding in the brief shows error or failure "
    "behaviour, and a Q&A block with kind 'extension' (how to change it) only when a finding shows where and how "
    "the code is changed or extended; otherwise omit both. Stay inside this chapter's objective. Chapters this "
    "one builds on are summarised in one line each: refer to one by its title in your own words, never copy or "
    "paraphrase its summary. Other chapters of the guide cover the topics listed under 'Covered in other "
    "chapters': do not explain those again; at most name the chapter. Write for the stated "
    "reader level. Use headings (level 2/3), paragraphs, bullets, callouts, code blocks quoting evidence "
    "verbatim with its path, tables and Q&A. Do not emit diagram blocks. summary is one sentence.\n\n"
    + UNTRUSTED_POLICY
)


class SectionEvidenceMissing(GenerationError):
    """One chapter found no repository evidence: the pipeline marks that section FAILED, not the whole job."""

    code = ErrorCode.RETRIEVAL_FAILED


@dataclass(frozen=True)
class WrittenSection:
    content: SectionContent
    claims: list[ClaimRecord]  # status UNKNOWN until `grade_claims`
    evidence: dict[str, dict[str, object]]
    context_tokens: int


def _strip_diagrams(content: SectionContent) -> SectionContent:
    blocks: list[Block] = [b for b in content.blocks if not isinstance(b, DiagramBlock)]
    return SectionContent(summary=content.summary, blocks=blocks)


def _norm(text: str) -> str:
    return " ".join(text.split())


def _locate(code: str, chunks: Sequence[Chunk], prefer: str | None) -> tuple[str, int] | None:
    """(path, file line of the first quoted line) when every non-blank quoted line appears, in order and
    adjacent, in the body of one shown chunk (whitespace-normalised). Chunk text is `header\nbody`."""
    quoted = [_norm(x) for x in code.splitlines() if x.strip()]
    if not quoted:
        return None
    for c in sorted(chunks, key=lambda c: c.path != prefer):
        body = c.text.split("\n", 1)[1] if "\n" in c.text else ""
        rows = [(i, _norm(x)) for i, x in enumerate(body.splitlines()) if x.strip()]
        norms = [n for _, n in rows]
        for k in range(len(norms) - len(quoted) + 1):
            if norms[k : k + len(quoted)] == quoted:
                return c.path, c.start_line + rows[k][0]
    return None


def anchor_code(content: SectionContent, shown: Sequence[Chunk]) -> SectionContent:
    """Every code block gets the `path` and `start_line` of the excerpt it quotes. A block that quotes nothing
    in the evidence shown (and names no location itself) is dropped: it would be unverifiable code."""
    blocks: list[Block] = []
    dropped = 0
    for b in content.blocks:
        if isinstance(b, CodeBlock):
            hit = _locate(b.code, shown, b.path)
            if hit is not None:
                b = b.model_copy(update={"path": hit[0], "start_line": hit[1]})
            elif not (b.path and b.start_line):
                dropped += 1
                continue
        blocks.append(b)
    if dropped:
        log.warning("guide.code_block_dropped", count=dropped)
    return SectionContent(summary=content.summary, blocks=blocks)


def _edges(brief: ResearchBrief) -> list[EdgeRef]:
    """Call edges the brief states: those of its findings and every proof-bearing step of its flows."""
    out: dict[tuple[str, str], EdgeRef] = {}
    for f in brief.findings:
        for e in f.edges:
            out.setdefault((e.src, e.dst), e)
    for flow in brief.flows:
        name = {s.symbol.id: s.symbol.name for s in flow.steps}
        for step in flow.steps:
            via = step.via
            if via is not None and via.src in name and via.rel == "CALLS":
                out.setdefault(
                    (via.src, via.dst),
                    EdgeRef(src=via.src, dst=via.dst, src_name=name[via.src], dst_name=step.symbol.name),
                )
    return list(out.values())


def _word_at(text: str, name: str) -> int | None:
    short = name.rsplit(".", 1)[-1]
    m = re.search(rf"(?<![A-Za-z0-9_]){re.escape(short)}(?![A-Za-z0-9_])", text) if short else None
    return m.start() if m else None


def _edge_ids(text: str, edges: Sequence[EdgeRef]) -> list[str]:
    """[source, target] of the brief's edge a claim states ("A calls B"): both names, source first, and a
    relation verb. Otherwise []: only claims about an edge the research found are checked as edges."""
    if not _RELATION.search(text):
        return []
    for e in edges:
        a, b = _word_at(text, e.src_name), _word_at(text, e.dst_name)
        if e.src != e.dst and a is not None and b is not None and a < b:
            return [e.src, e.dst]
    return []


async def write_chapter(
    llm: LLMClient,
    chunks: ChunkRepository,
    plan: ChapterPlan,
    brief: ResearchBrief,
    facts: Sequence[str],
    dependencies: Sequence[tuple[str, str]] = (),
    others: Sequence[tuple[str, str]] = (),
    *,
    technical_level: str,
    instruction: str | None,
    job_id: str,
    project_id: str,
) -> WrittenSection:
    """`dependencies` are (title, one-line summary) of the chapters in `plan.depends_on`; `others` are (title,
    objective) of every other chapter in the plan, so this one does not explain what another chapter owns."""
    rank = {Proof.STATIC: 0, Proof.HEURISTIC: 1, Proof.INFERRED: 2}
    by_trust = sorted(brief.findings, key=lambda f: (rank[f.proof], f.question_id))
    ids = list(dict.fromkeys(i for f in by_trust for i in f.evidence))
    found = await chunks.get_many(project_id, ids)
    items = [RetrievedItem(chunk=c, score=round(1.0 - i / (len(found) + 1), 4)) for i, c in enumerate(found)]
    retrieval = RetrievalResult(
        plan=RetrievalPlan(project_id=project_id, query=plan.objective, mode=RetrievalMode.GUIDE),
        items=items,
        sufficient=not brief.open_questions,
        trace=RetrievalTrace(config_version=f"research-brief-v{WRITER_PROMPT_VERSION}"),
    )
    brief_tokens = estimate_tokens(render_brief(brief))
    ctx = compile_context(
        objective=f"Write the guide chapter '{safe_header_field(plan.title)}'. "
        f"Objective: {safe_header_field(plan.objective)}. Depth: {plan.depth}. "
        f"Aim for about {plan.target_minutes:g} minutes of reading.",
        retrieval=retrieval,
        facts=facts,
        profile=SessionProfile(technical_level=technical_level, instruction=instruction),
        budget_tokens=max(MIN_EXCERPT_TOKENS, SECTION_CONTEXT_TOKENS - brief_tokens),
    )
    if not ctx.evidence:
        raise SectionEvidenceMissing(
            f"No repository evidence was found for section '{plan.title}'.",
            code=ErrorCode.RETRIEVAL_FAILED,
            retryable=True,
        )
    labels = {chunk_id: label for label, chunk_id in ctx.label_map.items()}
    prompt = (
        ctx.render()
        + "\n\n## Research brief\n"
        + untrusted(
            redact_text(render_brief(brief, labels)),
            source=f"research:{plan.key}",
            trust="repository_derived",
        )
    )
    if dependencies:
        lines = "\n".join(f"- {t}: {s}" for t, s in dependencies)
        prompt += "\n\n## Earlier chapters this one builds on\n" + untrusted(
            redact_text(lines), source="prior_chapters", trust="derived_summary"
        )
    if others:
        lines = "\n".join(f"- {t}: {o}" for t, o in others)
        prompt += "\n\n## Covered in other chapters\n" + untrusted(
            redact_text(lines), source="other_chapters", trust="derived_summary"
        )
    req = LLMRequest(
        purpose="guide.section",
        capability=ModelCapability.BALANCED,
        priority=Priority.P1,
        system=_WRITER_SYSTEM,
        messages=[Message(role="user", content=prompt)],
        temperature=0.3,
        max_output_tokens=4096,
        job_id=job_id,
        project_id=project_id,
    )
    draft = await llm.generate_structured(req, SectionDraft)
    edges = _edges(brief)
    claims = [
        ClaimRecord(
            text=c.text,
            kind=ClaimKind(c.kind),
            evidence_ids=ctx.resolve_labels(c.evidence),
            graph_node_ids=_edge_ids(c.text, edges) if c.kind == "project_fact" else [],
        )
        for c in draft.claims
    ]
    shown_ids = {e.chunk_id for e in ctx.evidence}
    evidence: dict[str, dict[str, object]] = {
        e.label: {
            "chunk_id": e.chunk_id,
            "path": e.path,
            "start_line": e.start_line,
            "end_line": e.end_line,
            "symbol": e.symbol,
        }
        for e in ctx.evidence
    }
    content = anchor_code(_strip_diagrams(draft.content), [c for c in found if c.id in shown_ids])
    return WrittenSection(content, claims, evidence, ctx.token_count_estimate + brief_tokens)
