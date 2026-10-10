"""Bounded revision of failing blocks (investigation engine §5 Reviser, task 3.7).

Pure: the model call is the injected `rewrite` callable, the checks are the injected `verify` callable. At most
`max_rounds` (2) rewrites; each touches only the blocks that failed; the loop stops early on `accept` or when a
round does not reduce the failures. What still fails is qualified ("Not confirmed in the code:") or removed, never
left standing as fact.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from unfold.domain.enums import Proof
from unfold.guide.models import (
    Block,
    BulletsBlock,
    CalloutBlock,
    CodeBlock,
    ParagraphBlock,
    QABlock,
    ResearchBrief,
    SectionContent,
)
from unfold.guide.verification_models import (
    MAX_REVISION_ROUNDS,
    NOT_CONFIRMED,
    CheckResult,
    Unsupported,
    VerificationResult,
)
from unfold.guide.verify import block_index, block_texts, words

_RANK = {Proof.STATIC: 0, Proof.HEURISTIC: 1, Proof.INFERRED: 2}
_QUERY_CHARS = 240


class RevisionItem(BaseModel):
    """One block to rewrite: why it failed, brief chunks it may use, and the text for one targeted retrieval."""

    block: str
    failures: list[CheckResult]  # empty when only the semantic critique flagged the block
    evidence_ids: list[str]  # chunk ids of the brief's findings that bear on this block
    query: str


@dataclass(frozen=True)
class RevisionOutcome:
    content: SectionContent
    verification: VerificationResult  # of `content` before qualification; verdict is final
    rounds: int
    stopped_by: str  # accept | cap | no_improvement
    gaps: tuple[str, ...]  # block ids qualified or removed


Verify = Callable[[SectionContent, int], Awaitable[VerificationResult]]  # (content, round) -> result
Rewrite = Callable[[SectionContent, Sequence[RevisionItem]], Awaitable[Mapping[str, Block]]]


def plan_revision(
    verification: VerificationResult, content: SectionContent, brief: ResearchBrief, *, max_evidence: int = 3
) -> list[RevisionItem]:
    items: list[RevisionItem] = []
    for bid in verification.failing_blocks:
        block = content.blocks[block_index(bid, len(content.blocks))]
        failures = [r for r in verification.deterministic if r.block == bid and not r.ok]
        text = " ".join(block_texts(block) or [block.code if isinstance(block, CodeBlock) else ""])
        want = words(text + " " + " ".join(r.subject for r in failures))
        scored = sorted(
            (
                (-len(want & words(f.statement)), _RANK[f.proof], i, f.evidence)
                for i, f in enumerate(brief.findings)
            ),
        )
        ids = [c for n, _, _, ev in scored if n < 0 for c in ev]
        items.append(
            RevisionItem(
                block=bid,
                failures=failures,
                evidence_ids=list(dict.fromkeys(ids))[:max_evidence],
                query=" ".join(text.split())[:_QUERY_CHARS]
                or " ".join(r.subject for r in failures)[:_QUERY_CHARS],
            )
        )
    return items


def apply_revision(content: SectionContent, rewrites: Mapping[str, Block]) -> SectionContent:
    """Replace exactly the named blocks; every other block is returned untouched."""
    n = len(content.blocks)
    index = {block_index(bid, n): new for bid, new in rewrites.items()}
    blocks = [index.get(i, b) for i, b in enumerate(content.blocks)]
    return SectionContent(summary=content.summary, blocks=blocks)


def unsupported_blocks(verification: VerificationResult) -> list[Unsupported]:
    """Blocks with a failed deterministic check. A block is `has_evidence` when it also cites a real location."""
    failed = {r.block for r in verification.deterministic if not r.ok and r.block}
    cited = {r.block for r in verification.deterministic if r.ok and r.check == "path_exists"}
    return [Unsupported(block=b, has_evidence=b in cited) for b in sorted(failed, key=lambda b: int(b[1:]))]


def _prefixed(text: str) -> str:
    return text if text.startswith(NOT_CONFIRMED) else f"{NOT_CONFIRMED} {text}"


def qualify_or_remove(content: SectionContent, unsupported: Sequence[Unsupported]) -> SectionContent:
    """Blocks with no evidence at all, quoted code and tables/glossaries (cannot be half-trusted) are removed;
    prose that still cites something real keeps its text behind "Not confirmed in the code:"."""
    n = len(content.blocks)
    drop: set[int] = set()
    qualify: dict[int, Block] = {}
    for u in unsupported:
        i = block_index(u.block, n)
        b = content.blocks[i]
        if not u.has_evidence:
            drop.add(i)
        elif isinstance(b, ParagraphBlock | CalloutBlock):
            qualify[i] = b.model_copy(update={"text": _prefixed(b.text)})
        elif isinstance(b, BulletsBlock):
            qualify[i] = b.model_copy(update={"items": [_prefixed(x) for x in b.items]})
        elif isinstance(b, QABlock):
            qualify[i] = b.model_copy(update={"answer": _prefixed(b.answer)})
        else:
            drop.add(i)
    blocks = [qualify.get(i, b) for i, b in enumerate(content.blocks) if i not in drop]
    return SectionContent(summary=content.summary, blocks=blocks)


def _worse_or_equal(new: VerificationResult, old: VerificationResult) -> bool:
    def size(v: VerificationResult) -> tuple[int, int]:
        return len(v.failing_blocks), sum(1 for r in v.deterministic if not r.ok)

    return size(new) >= size(old)


async def run_revision_loop(
    verify: Verify,
    rewrite: Rewrite,
    content: SectionContent,
    max_rounds: int = MAX_REVISION_ROUNDS,
    *,
    plan: Callable[[VerificationResult, SectionContent], list[RevisionItem]] | None = None,
) -> RevisionOutcome:
    """verify -> (rewrite failing blocks -> verify)* with at most `max_rounds` rewrites.

    `verify(content, round)` runs the deterministic checks (and the critique, if any); `rewrite(content, items)`
    returns replacement blocks keyed by block id (ids that are not failing are ignored). `plan` builds the items
    (default: no findings, queries only; the pipeline passes `plan_revision` bound to the chapter's brief).
    """
    plan = plan or _queries_only
    result = await verify(content, 0)
    rounds, stopped = 0, "accept"
    while result.verdict == "revise":
        if rounds >= max_rounds:
            stopped = "cap"
            break
        items = plan(result, content)
        wanted = {i.block for i in items}
        new_blocks = {b: blk for b, blk in (await rewrite(content, items)).items() if b in wanted}
        candidate = apply_revision(content, new_blocks)
        rounds += 1
        checked = await verify(candidate, rounds)
        if _worse_or_equal(checked, result):
            stopped = "no_improvement"  # keep the version we had: churn is not progress
            break
        content, result = candidate, checked
    if result.verdict == "revise":
        # A critique alone only asks for revision: with no failed check nothing was shown to be unsupported.
        failed = any(not r.ok for r in result.deterministic)
        result = result.model_copy(update={"verdict": "accept_with_gaps" if failed else "accept"})
    if result.verdict != "accept" and stopped == "accept":
        stopped = "cap"  # the last verification itself reported the cap
    gaps = unsupported_blocks(result) if result.verdict == "accept_with_gaps" else []
    if gaps:
        content = qualify_or_remove(content, gaps)
    return RevisionOutcome(content, result, rounds, stopped, tuple(g.block for g in gaps))


def _queries_only(v: VerificationResult, c: SectionContent) -> list[RevisionItem]:
    return [RevisionItem(block=b, failures=[], evidence_ids=[], query="") for b in v.failing_blocks]


__all__ = [
    "RevisionItem",
    "RevisionOutcome",
    "apply_revision",
    "plan_revision",
    "qualify_or_remove",
    "run_revision_loop",
    "unsupported_blocks",
]
