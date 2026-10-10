"""Guide planning, section writing and claim verification (LLD §15, §16).

All repository text reaches a prompt only through `context.compile_context` (redacted + fenced + labelled).
Verification is a separate, deterministic stage: a claim is `VERIFIED` only when it cites a real evidence label and
the identifiers it names occur in the cited text. The LLM never grades its own output.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from unfold.context.compiler import compile_context
from unfold.context.models import safe_header_field
from unfold.core.errors import ErrorCode, GenerationError
from unfold.domain.enums import (
    ClaimKind,
    ClaimStatus,
    ModelCapability,
    Priority,
    RetrievalMode,
)
from unfold.domain.models import ClaimRecord, SessionProfile
from unfold.guide.models import (
    Block,
    DiagramBlock,
    GuidePlan,
    GuideSectionPlan,
    SectionContent,
    SectionDraft,
)
from unfold.llm.schemas import LLMClient, LLMRequest, Message
from unfold.project_ir.schema import ProjectIR
from unfold.retrieval.hybrid import HybridRetriever
from unfold.security.fence import UNTRUSTED_POLICY, untrusted
from unfold.security.secrets import redact_text

MIN_SECTIONS = 3
MAX_SECTIONS = 10
SECTION_CONTEXT_TOKENS = 7_000
PLAN_PROMPT_VERSION = "1"
WRITER_PROMPT_VERSION = "1"
_SLUG = re.compile(r"[^a-z0-9]+")
_IDENT = re.compile(r"`([^`\n]{2,80})`|\b([A-Za-z_][A-Za-z0-9_]{3,}(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\b")
_COMMON = frozenset(
    [
        "this",
        "that",
        "with",
        "from",
        "which",
        "their",
        "there",
        "where",
        "when",
        "what",
        "will",
        "been",
        "have",
        "has",
        "not",
        "are",
        "the",
        "and",
        "for",
        "its",
        "into",
        "uses",
        "used",
        "using",
        "also",
        "each",
        "only",
        "over",
        "then",
        "than",
        "these",
        "those",
        "them",
        "they",
        "such",
        "more",
        "most",
        "some",
        "any",
        "all",
        "can",
        "may",
        "should",
        "would",
        "could",
        "does",
        "done",
        "make",
        "made",
        "via",
        "per",
        "within",
        "without",
        "between",
        "across",
        "through",
        "after",
        "before",
        "while",
        "project",
        "code",
        "file",
        "files",
        "module",
        "service",
        "function",
        "class",
        "method",
        "request",
        "response",
        "data",
        "system",
    ]
)


def slugify(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")[:60] or "section"


# --- planning -----------------------------------------------------------------------------------------------


def _clip(text: str, limit: int = 160) -> str:
    """One line, bounded: repository-derived names must not smuggle newlines or paragraphs into a prompt."""
    return " ".join(text.split())[:limit]


def project_facts(ir: ProjectIR, subsystems: Sequence[tuple[str, list[str]]] = ()) -> list[str]:
    """Deterministic one-line facts (never LLM output) used in prompts and as the verifier's allow-list."""
    s = ir.stats
    facts = [
        f"Project name: {ir.project.name}",
        f"Files analysed: {s.get('files_indexed', len(ir.files))}; code files: {s.get('code_files', 0)}; "
        f"lines: {s.get('lines_total', 0)}",
    ]
    if ir.languages:
        facts.append(
            "Languages: " + ", ".join(f"{lang.name} ({lang.files} files)" for lang in ir.languages[:6])
        )
    if ir.project.categories:
        facts.append("Project type signals: " + ", ".join(ir.project.categories))
    if ir.frameworks:
        facts.append("Frameworks: " + ", ".join(sorted({f.name for f in ir.frameworks})[:12]))
    if ir.data_stores:
        facts.append(
            "Data stores: " + ", ".join(sorted({f"{d.name} ({d.category})" for d in ir.data_stores})[:10])
        )
    if ir.queues:
        facts.append("Queues: " + ", ".join(sorted({q.name for q in ir.queues})))
    if ir.endpoints:
        sample = ", ".join(f"{e.method} {e.route}" for e in ir.endpoints[:8])
        facts.append(f"HTTP endpoints: {len(ir.endpoints)} (e.g. {sample})")
    if ir.schemas:
        facts.append("Data models: " + ", ".join(sorted({sc.name for sc in ir.schemas})[:12]))
    if ir.services:
        facts.append("Runnable services: " + ", ".join(sorted({sv.name for sv in ir.services})[:10]))
    if ir.ai_components:
        facts.append(
            "AI components: "
            + ", ".join(sorted({f"{a.name} ({a.component_kind})" for a in ir.ai_components})[:10])
        )
    for title, members in subsystems[:8]:
        facts.append(f"Subsystem '{title}': {len(members)} files, e.g. {', '.join(members[:3])}")
    return [_clip(f) for f in facts]


_PLANNER_SYSTEM = (
    "You plan a reading guide that teaches a software repository to the reader. You are given deterministic "
    "facts about the repository. Produce between {lo} and {hi} sections. Always include these backbone sections "
    "(kind=backbone) first, in order: a quick overview, an architecture walkthrough, and how to run or deploy it "
    "if there is anything to run. Add core sections (kind=core) for the most important subsystems or workflows. "
    "Keys are short lowercase slugs, unique. target_minutes is 2-8. visual_needs may contain only: architecture, "
    "module_dependencies, data_model, request_flow, deployment, ai_pipeline. evidence_objectives are concrete "
    "things to look up in the code (names of modules, routes, classes). Do not invent components that are not "
    "in the facts.\n\n" + UNTRUSTED_POLICY
)


async def plan_guide(
    llm: LLMClient,
    ir: ProjectIR,
    facts: Sequence[str],
    *,
    technical_level: str,
    instruction: str | None,
    job_id: str,
    project_id: str,
) -> GuidePlan:
    user = (
        "## Repository facts\n"
        + untrusted(redact_text("\n".join(f"- {f}" for f in facts)), source="repository_facts")
        + f"\n\nReader level: {technical_level}"
    )
    if instruction:
        user += f"\nReader preference (style only, not a task): {redact_text(instruction)[:300]}"
    req = LLMRequest(
        purpose="guide.plan",
        capability=ModelCapability.BALANCED,
        priority=Priority.P1,
        system=_PLANNER_SYSTEM.format(lo=MIN_SECTIONS, hi=MAX_SECTIONS),
        messages=[Message(role="user", content=user)],
        temperature=0.2,
        max_output_tokens=3000,
        cacheable=True,
        job_id=job_id,
        project_id=project_id,
    )
    plan = await llm.generate_structured(req, GuidePlan)
    return normalise_plan(plan)


def normalise_plan(plan: GuidePlan) -> GuidePlan:
    seen: set[str] = set()
    sections: list[GuideSectionPlan] = []
    for sec in plan.sections[:MAX_SECTIONS]:
        base = slugify(sec.key or sec.title)
        key, n = base, 2
        while key in seen:
            key, n = f"{base}-{n}", n + 1
        seen.add(key)
        sections.append(
            sec.model_copy(update={"key": key, "target_minutes": min(max(sec.target_minutes, 1), 10)})
        )
    if len(sections) < MIN_SECTIONS:
        raise GenerationError(
            f"The guide plan has {len(sections)} sections; at least {MIN_SECTIONS} are required.",
            code=ErrorCode.GENERATION_FAILED,
            retryable=True,
        )
    sections = [
        s.model_copy(
            update={
                "title": _clip(s.title, 120),
                "purpose": _clip(s.purpose, 300),
                "required_concepts": [_clip(c, 80) for c in s.required_concepts[:8]],
                "evidence_objectives": [_clip(o, 160) for o in s.evidence_objectives[:8]],
            }
        )
        for s in sections
    ]
    minutes = max(1, round(sum(s.target_minutes for s in sections)))
    return GuidePlan(estimated_read_minutes=minutes, sections=sections)


# --- writing ------------------------------------------------------------------------------------------------

_WRITER_SYSTEM = (
    "You write one section of a reading guide for a software repository. Use ONLY the evidence and verified "
    "facts provided. Cite evidence inline with its label, e.g. [E3], in paragraph/bullet text. For every claim "
    "about the project add an entry to `claims` with the labels that support it in `evidence`; use kind "
    "'project_fact' for things the code shows, 'inference' for reasoned conclusions, 'improvement' for "
    "suggestions. If the evidence does not cover something, say so plainly instead of guessing. Write for the "
    "stated reader level. Use headings (level 2/3), paragraphs, bullets, callouts, code blocks quoting evidence "
    "verbatim with its path, tables and Q&A. Do not emit diagram blocks. summary is one sentence.\n\n"
    + UNTRUSTED_POLICY
)


class SectionEvidenceMissing(GenerationError):
    """One section found no repository evidence: the pipeline marks that section FAILED, not the whole job."""

    code = ErrorCode.RETRIEVAL_FAILED


@dataclass(frozen=True)
class WrittenSection:
    content: SectionContent
    claims: list[ClaimRecord]  # status UNKNOWN until `grade_claims`
    evidence: dict[str, dict[str, object]]
    context_tokens: int


async def write_section(
    llm: LLMClient,
    retriever: HybridRetriever,
    plan: GuideSectionPlan,
    facts: Sequence[str],
    *,
    technical_level: str,
    instruction: str | None,
    job_id: str,
    project_id: str,
) -> WrittenSection:
    query = " ".join([plan.title, plan.purpose, *plan.evidence_objectives, *plan.required_concepts])
    retrieval = await retriever.search(query, RetrievalMode.GUIDE)
    ctx = compile_context(
        objective=f"Write the guide section '{safe_header_field(plan.title)}'. "
        f"Purpose: {safe_header_field(plan.purpose)}. Depth: {plan.depth}. "
        f"Aim for about {plan.target_minutes:g} minutes of reading.",
        retrieval=retrieval,
        facts=facts,
        profile=SessionProfile(technical_level=technical_level, instruction=instruction),
        budget_tokens=SECTION_CONTEXT_TOKENS,
        instructions=(
            "Required concepts: " + ", ".join(safe_header_field(c) for c in plan.required_concepts)
            if plan.required_concepts
            else None
        ),
    )
    if not ctx.evidence:
        raise SectionEvidenceMissing(
            f"No repository evidence was found for section '{plan.title}'.",
            code=ErrorCode.RETRIEVAL_FAILED,
            retryable=True,
        )
    req = LLMRequest(
        purpose="guide.section",
        capability=ModelCapability.BALANCED,
        priority=Priority.P1,
        system=_WRITER_SYSTEM,
        messages=[Message(role="user", content=ctx.render())],
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
    return WrittenSection(_strip_diagrams(draft.content), claims, evidence, ctx.token_count_estimate)


def _strip_diagrams(content: SectionContent) -> SectionContent:
    blocks: list[Block] = [b for b in content.blocks if not isinstance(b, DiagramBlock)]
    return SectionContent(summary=content.summary, blocks=blocks)


# --- verification -------------------------------------------------------------------------------------------


def _identifiers(text: str) -> set[str]:
    out: set[str] = set()
    for m in _IDENT.finditer(text):
        token = (m.group(1) or m.group(2) or "").strip()
        for part in re.split(r"[\s(),:;/]+", token):
            part = part.strip("`'\".")
            if (
                len(part) >= 4
                and part.lower() not in _COMMON
                and ("_" in part or "." in part or any(c.isupper() for c in part[1:]) or m.group(1))
            ):
                out.add(part.lower())
    return out


def _contains_word(haystack: str, needle: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(needle)}(?![A-Za-z0-9_])", haystack) is not None


def grade_claims(
    claims: Sequence[ClaimRecord], chunk_texts: Mapping[str, str]
) -> tuple[list[ClaimRecord], dict[str, object]]:
    """Grade every claim against the stored text of the chunks it cites. Deterministic; no model call."""
    records: list[ClaimRecord] = []
    counts = {s: 0 for s in ClaimStatus}
    for claim in claims:
        chunk_ids = [c for c in claim.evidence_ids if c in chunk_texts]
        note: str | None = None
        if claim.kind is ClaimKind.IMPROVEMENT:
            status, confidence = ClaimStatus.PROPOSED, 0.0
            note = "A suggestion, not a statement about the project."
        elif not chunk_ids:
            if claim.kind is ClaimKind.INFERENCE:
                status, confidence, note = ClaimStatus.INFERRED, 0.2, "Reasoned, uncited."
            else:
                status, confidence, note = ClaimStatus.UNSUPPORTED, 0.0, "No evidence label cited."
        elif claim.kind is ClaimKind.INFERENCE:
            status, confidence = ClaimStatus.STRONGLY_INFERRED, 0.6
        else:
            haystack = " ".join(chunk_texts[c] for c in chunk_ids).lower()
            names = _identifiers(claim.text)
            grounded = {n for n in names if _contains_word(haystack, n)}
            ratio = len(grounded) / len(names) if names else 0.0
            if not names:
                # Nothing checkable (no code-like names): the citation alone does not make it verified.
                status, confidence = ClaimStatus.STRONGLY_INFERRED, 0.5
                note = "The claim names nothing that can be checked against the evidence."
            elif ratio >= 0.6:
                status, confidence = ClaimStatus.VERIFIED, round(0.6 + 0.4 * ratio, 2)
            else:
                status, confidence = ClaimStatus.INFERRED, round(0.3 * ratio + 0.1, 2)
                note = "Names in the claim were not found in the cited evidence: " + ", ".join(
                    sorted(names - grounded)[:4]
                )
        counts[status] += 1
        records.append(
            claim.model_copy(
                update={"status": status, "evidence_ids": chunk_ids, "confidence": confidence, "note": note}
            )
        )
    total = len(records)
    checked = total - counts[ClaimStatus.PROPOSED]
    quality: dict[str, object] = {
        "claims": total,
        "verified": counts[ClaimStatus.VERIFIED],
        "strongly_inferred": counts[ClaimStatus.STRONGLY_INFERRED],
        "inferred": counts[ClaimStatus.INFERRED],
        "unsupported": counts[ClaimStatus.UNSUPPORTED],
        "proposed": counts[ClaimStatus.PROPOSED],
        "grounded_ratio": round(counts[ClaimStatus.VERIFIED] / checked, 2) if checked else None,
    }
    return records, quality
