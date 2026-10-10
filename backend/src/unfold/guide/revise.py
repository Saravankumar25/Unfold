"""Bounded revision of failing blocks (investigation engine §5 Reviser, task 3.7).

Pure: the model call is the injected `rewrite` callable, the checks are the injected `verify` callable. At most
`max_rounds` (2) rewrites; each touches only the blocks that failed; the loop stops early on `accept` or when a
round does not reduce the failures. What still fails is qualified ("Not confirmed in the code:") or removed, never
left standing as fact.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field

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
from unfold.guide.verify import (
    CLAIM_MATCH,
    LABEL,
    PATHLINE,
    best_unit,
    block_index,
    block_texts,
    sentences,
    words,
)

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
    content: SectionContent  # after qualification or removal of what still failed
    verified: SectionContent  # the content `verification` describes (before that step)
    verification: VerificationResult  # of `verified`; verdict is final
    rounds: int
    stopped_by: str  # accept | cap | no_improvement
    gaps: tuple[str, ...]  # block ids qualified or removed
    dispositions: Mapping[str, str] = field(default_factory=dict)  # failing claim text -> qualified | removed


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
    full = {c.text[:120]: c.text for c in verification.claims}
    out: list[Unsupported] = []
    for b in sorted(failed, key=lambda b: int(b[1:])):
        mine = [r for r in verification.deterministic if r.block == b and not r.ok]
        claims = [full.get(r.subject, r.subject) for r in mine if r.check == "claim_supported"]
        out.append(
            Unsupported(block=b, has_evidence=b in cited, claims=claims, claim_only=len(claims) == len(mine))
        )
    return out


def _prefixed(text: str) -> str:
    return text if text.startswith(NOT_CONFIRMED) else f"{NOT_CONFIRMED} {text}"


def _cites(text: str) -> bool:
    return LABEL.search(text) is not None or PATHLINE.search(text) is not None


def _sentence_level(block: Block, claims: Sequence[str]) -> tuple[list[Block], dict[str, str]] | None:
    """Fix only the sentences (or bullet items) the failing claims were made in. A sentence that cites something
    real is kept behind the prefix as its own block (so the reader sees it marked); one that cites nothing is
    removed. None when a claim cannot be placed in a sentence: the caller falls back to the whole block."""
    if not isinstance(block, ParagraphBlock | CalloutBlock | BulletsBlock):
        return None
    units = list(block.items) if isinstance(block, BulletsBlock) else sentences(block)
    culprit: dict[int, list[str]] = {}
    for claim in claims:
        best, score = best_unit(claim, units)
        if best < 0 or score < CLAIM_MATCH:
            return None
        culprit.setdefault(best, []).append(claim)
    disposed = {c: "qualified" if _cites(units[i]) else "removed" for i, cs in culprit.items() for c in cs}
    if isinstance(block, BulletsBlock):
        items = [
            _prefixed(u) if i in culprit else u for i, u in enumerate(units) if i not in culprit or _cites(u)
        ]
        return ([block.model_copy(update={"items": items})] if items else []), disposed
    out: list[Block] = []
    run: list[str] = []
    for i, u in enumerate(units):
        if i not in culprit:
            run.append(u)
            continue
        if _cites(u):  # a marked sentence stands alone so the reader sees it marked
            if run:
                out.append(block.model_copy(update={"text": " ".join(run)}))
                run = []
            out.append(block.model_copy(update={"text": _prefixed(u)}))
    if run:
        out.append(block.model_copy(update={"text": " ".join(run)}))
    return out, disposed


def resolve_gaps(
    content: SectionContent, unsupported: Sequence[Unsupported]
) -> tuple[SectionContent, dict[str, str]]:
    """Qualify or remove what still fails. Returns the content and, per failing claim text, what happened to
    the statement that made it (`qualified` | `removed`), so the claim records can follow the prose."""
    n = len(content.blocks)
    replace: dict[int, list[Block]] = {}
    disposed: dict[str, str] = {}
    for u in unsupported:
        i = block_index(u.block, n)
        b = content.blocks[i]
        if u.claim_only and u.claims and (fine := _sentence_level(b, u.claims)) is not None:
            replace[i], part = fine
            disposed.update(part)
            continue
        if not u.has_evidence:
            replace[i] = []
        elif isinstance(b, ParagraphBlock | CalloutBlock):
            replace[i] = [b.model_copy(update={"text": _prefixed(b.text)})]
        elif isinstance(b, BulletsBlock):
            replace[i] = [b.model_copy(update={"items": [_prefixed(x) for x in b.items]})]
        elif isinstance(b, QABlock):
            replace[i] = [b.model_copy(update={"answer": _prefixed(b.answer)})]
        else:
            replace[i] = []  # code, tables, glossaries cannot be half-trusted
        disposed.update({c: "qualified" if replace[i] else "removed" for c in u.claims})
    blocks = [x for i, b in enumerate(content.blocks) for x in replace.get(i, [b])]
    return SectionContent(summary=content.summary, blocks=blocks), disposed


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
    plan: Callable[[VerificationResult, SectionContent], list[RevisionItem]],
) -> RevisionOutcome:
    """verify -> (rewrite failing blocks -> verify)* with at most `max_rounds` rewrites.

    `verify(content, round)` runs the deterministic checks (and the critique, if any); `rewrite(content, items)`
    returns replacement blocks keyed by block id (ids that are not failing are ignored). `plan` builds the items
    (the pipeline passes `plan_revision` bound to the chapter's brief).
    """
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
    verified = content
    disposed: dict[str, str] = {}
    if gaps:
        content, disposed = resolve_gaps(content, gaps)
    return RevisionOutcome(content, verified, result, rounds, stopped, tuple(g.block for g in gaps), disposed)


__all__ = [
    "RevisionItem",
    "RevisionOutcome",
    "apply_revision",
    "plan_revision",
    "resolve_gaps",
    "run_revision_loop",
    "unsupported_blocks",
]
