"""Chapter writer (investigation engine §5, task 3.6): writes from the research brief, not from a chunk dump.

The prompt carries the compiled evidence excerpts (fetched by the brief's chunk ids; redacted, fenced, `[E#]`
labelled by `compile_context`), the rendered brief (findings with their labels, flows, tests, open questions), one
line per chapter this one depends on, and compact facts. Output is the same `SectionDraft` the verifier and the
frontend already consume.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from unfold.context.budget import estimate_tokens
from unfold.context.compiler import compile_context
from unfold.context.models import safe_header_field
from unfold.core.errors import ErrorCode, GenerationError
from unfold.domain.enums import ClaimKind, ModelCapability, Priority, Proof, RetrievalMode
from unfold.domain.models import ClaimRecord, SessionProfile
from unfold.guide.models import (
    Block,
    ChapterPlan,
    DiagramBlock,
    ResearchBrief,
    SectionContent,
    SectionDraft,
)
from unfold.guide.research import render_brief
from unfold.indexing.store import ChunkRepository
from unfold.llm.schemas import LLMClient, LLMRequest, Message
from unfold.retrieval.models import RetrievalPlan, RetrievalResult, RetrievalTrace, RetrievedItem
from unfold.security.fence import UNTRUSTED_POLICY, untrusted
from unfold.security.secrets import redact_text

SECTION_CONTEXT_TOKENS = 9_000  # brief + excerpts + facts
MIN_EXCERPT_TOKENS = 2_000
WRITER_PROMPT_VERSION = "2"

_WRITER_SYSTEM = (
    "You write one chapter of a reading guide that teaches a software repository. You are given a research brief "
    "(what was found for each question, with the evidence labels that support it, traced flows, tests, and "
    "questions that were NOT established) and the evidence excerpts. Use ONLY the brief, the excerpts and the "
    "verified facts. Cite evidence inline with its label, e.g. [E3], in paragraph/bullet text. For every claim "
    "about the project add an entry to `claims` with the labels that support it in `evidence`; use kind "
    "'project_fact' for things the code shows, 'inference' for reasoned conclusions, 'improvement' for "
    "suggestions. Where the brief lists a question as not established, or the evidence does not cover something, "
    "say so plainly instead of guessing. Explain how things work in order (what calls what, what is read or "
    "written, what is returned) and name the real symbols and files. Add a Q&A block with kind 'failure' (what "
    "can go wrong) only when a finding in the brief shows error or failure behaviour, and a Q&A block with kind "
    "'extension' (how to change it) only when a finding shows where and how the code is changed or extended; "
    "otherwise omit both. Chapters this one builds on are summarised in one line each: refer to them, do not "
    "repeat them. Write for the stated reader level. Use headings (level 2/3), paragraphs, bullets, callouts, "
    "code blocks quoting evidence verbatim with its path, tables and Q&A. Do not emit diagram blocks. summary is "
    "one sentence.\n\n" + UNTRUSTED_POLICY
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


async def write_chapter(
    llm: LLMClient,
    chunks: ChunkRepository,
    plan: ChapterPlan,
    brief: ResearchBrief,
    facts: Sequence[str],
    dependencies: Sequence[tuple[str, str]] = (),
    *,
    technical_level: str,
    instruction: str | None,
    job_id: str,
    project_id: str,
) -> WrittenSection:
    """`dependencies` are (title, one-line summary) of the chapters in `plan.depends_on`, nothing else."""
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
    claims = [
        ClaimRecord(text=c.text, kind=ClaimKind(c.kind), evidence_ids=ctx.resolve_labels(c.evidence))
        for c in draft.claims
    ]
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
    return WrittenSection(
        _strip_diagrams(draft.content), claims, evidence, ctx.token_count_estimate + brief_tokens
    )
