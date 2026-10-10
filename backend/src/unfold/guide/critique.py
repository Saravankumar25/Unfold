"""Semantic critique of a written chapter (investigation engine §5 Verifier, task 3.7).

One model call that may only *score* the five dimensions and *name* weak blocks; the schema (`SemanticCritique`)
has no field for replacement text, so the model can request a revision but never rewrite. The chapter text is
repository-derived: redacted, fenced and covered by the untrusted-data policy like every other prompt.
"""

from __future__ import annotations

from collections.abc import Sequence

from unfold.core.errors import ErrorCode, GenerationError
from unfold.domain.enums import ModelCapability, Priority
from unfold.guide.models import CodeBlock, SectionContent
from unfold.guide.verification_models import SemanticCritique
from unfold.guide.verify import block_id, block_texts
from unfold.llm.schemas import LLMClient, LLMRequest, Message
from unfold.security.fence import UNTRUSTED_POLICY, untrusted
from unfold.security.secrets import redact_text

CRITIQUE_PROMPT_VERSION = "1"
_CODE_CHARS = 400
_BLOCK_CHARS = 700

_SYSTEM = (
    "You review one chapter of a reading guide that teaches a software repository. The chapter is a list of "
    "blocks, each with an id such as b3. Score the chapter from 0 to 1 on: coverage (does it answer its "
    "objective), depth (does it explain how and in what order, not only what), coherence (a clear sequence), "
    "specificity (names this repository's real files and symbols rather than generic advice) and redundancy "
    "(1 = nothing repeated, also against the other chapters listed). In failing_blocks list the ids of the "
    "blocks that are weak on any dimension; use only ids that appear in the chapter. You can only score and "
    "name blocks: never write replacement text, wording or suggestions.\n\n" + UNTRUSTED_POLICY
)


def render_blocks(content: SectionContent) -> str:
    lines = [f"Summary: {content.summary}"]
    for i, b in enumerate(content.blocks):
        if isinstance(b, CodeBlock):
            body = f"code {b.path or ''}\n{b.code[:_CODE_CHARS]}"
        else:
            body = f"{b.type}: " + " | ".join(block_texts(b))
        lines.append(f"[{block_id(i)}] {body[:_BLOCK_CHARS]}")
    return "\n".join(lines)


def build_critique_request(
    *,
    title: str,
    objective: str,
    content: SectionContent,
    others: Sequence[tuple[str, str]] = (),
    job_id: str,
    project_id: str,
) -> LLMRequest:
    """`others`: (title, one-line summary) of the other chapters, for the redundancy score."""
    body = f"Chapter: {title}\nObjective: {objective}\n\n{render_blocks(content)}"
    if others:
        body += "\n\nOther chapters:\n" + "\n".join(f"- {t}: {s}" for t, s in others)
    return LLMRequest(
        purpose="guide.critique",
        capability=ModelCapability.FAST,
        priority=Priority.P2,  # an enrichment: refused first when the per-job budget runs low
        system=_SYSTEM,
        messages=[
            Message(
                role="user",
                content=untrusted(redact_text(body), source="chapter_draft", trust="repository_derived"),
            )
        ],
        temperature=0.0,
        max_output_tokens=400,
        job_id=job_id,
        project_id=project_id,
    )


def validate_critique(critique: SemanticCritique, content: SectionContent) -> SemanticCritique:
    """Reject block ids the chapter does not have: a hallucinated id would silently revise nothing."""
    known = {block_id(i) for i in range(len(content.blocks))}
    unknown = [b for b in critique.failing_blocks if b not in known]
    if unknown:
        raise GenerationError(
            f"The critique named blocks that do not exist: {', '.join(unknown[:4])}.",
            code=ErrorCode.INVALID_MODEL_OUTPUT,
            retryable=True,
        )
    return critique.model_copy(update={"failing_blocks": list(dict.fromkeys(critique.failing_blocks))})


async def critique_section(
    llm: LLMClient,
    *,
    title: str,
    objective: str,
    content: SectionContent,
    others: Sequence[tuple[str, str]] = (),
    job_id: str,
    project_id: str,
) -> SemanticCritique:
    req = build_critique_request(
        title=title, objective=objective, content=content, others=others, job_id=job_id, project_id=project_id
    )
    return validate_critique(await llm.generate_structured(req, SemanticCritique), content)
