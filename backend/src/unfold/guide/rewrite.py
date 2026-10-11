"""Model-backed rewrite of failing blocks (investigation engine §5 Reviser, task 3.7): the `Rewrite` callable
`guide.revise.run_revision_loop` is given.

Only the failing blocks are sent, each with why it failed and fresh evidence: the brief's chunks that bear on it
(`RevisionItem.evidence_ids`) plus one targeted retrieval for its text. Evidence is compiled like everywhere else
(redacted, fenced, budgeted, `[E#]` labelled); labels the model writes are mapped back onto the chapter's own label
set, so a rewritten block cites exactly what `GuideSectionRow.evidence_summary` can resolve. The result for a block
whose rewrite quotes code that is not in the evidence is simply absent: the block keeps failing and ends up
qualified or removed, never invented.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, Field

from unfold.context.compiler import compile_context
from unfold.core.logging import get_logger
from unfold.domain.enums import ClaimKind, ModelCapability, Priority, RetrievalMode
from unfold.domain.models import ClaimRecord, SessionProfile
from unfold.guide.models import Block, ChapterPlan, CodeBlock, DraftClaim, SectionContent
from unfold.guide.revise import RevisionItem
from unfold.guide.verification_models import CheckResult
from unfold.guide.verify import block_index, block_texts
from unfold.guide.write import anchor_code
from unfold.indexing.models import Chunk
from unfold.indexing.store import ChunkRepository
from unfold.llm.schemas import LLMClient, LLMRequest, Message
from unfold.retrieval.errors import RetrievalFailed
from unfold.retrieval.hybrid import HybridRetriever
from unfold.retrieval.models import RetrievalPlan, RetrievalResult, RetrievalTrace, RetrievedItem
from unfold.security.fence import UNTRUSTED_POLICY, untrusted
from unfold.security.secrets import redact_text

log = get_logger(__name__)

REWRITE_PROMPT_VERSION = "2"
REWRITE_CONTEXT_TOKENS = 5_000
_LABEL = re.compile(r"\bE(\d+)\b")
_BLOCK_CHARS = 900

_SYSTEM = (
    "You repair blocks of one chapter of a reading guide that teaches a software repository. Each block below "
    "failed verification; the reason is given. Rewrite ONLY the listed blocks, one replacement per block id. Use "
    "ONLY the evidence excerpts given: every sentence that states a fact about the code ends with an evidence "
    "label such as [E3] or a `path:L12-L40` location that exists in the excerpts. A code block quotes the "
    "excerpt verbatim (no ellipses) and sets `path` and `start_line`. If the evidence does not support what the "
    'block said, say so plainly in one sentence ("The evidence does not show how X is retried.") or state only '
    "what the evidence does show; never guess and never keep a statement you cannot ground. Keep the block type "
    "unless a code block cannot be quoted. For every project fact in a replacement add an entry to `claims` with "
    "the labels that support it in `evidence`.\n\n" + UNTRUSTED_POLICY
)

_REASON = {
    "path_exists": "cites {subject}, which is not available: {detail}",
    "line_in_bounds": "cites lines {subject} outside the file ({detail})",
    "symbol_exists": "names the symbol {subject}, which is not in the repository",
    "excerpt_matches": "the quoted code of {subject} is not in the file",
    "edge_exists": "claims the call {subject}: {detail}",
    "claim_supported": 'the statement "{subject}" is not supported by the evidence it cites ({detail})',
}


class RewrittenBlock(BaseModel):
    block: str  # id such as "b3"
    new: Block


class RewriteDraft(BaseModel):
    blocks: list[RewrittenBlock] = Field(default_factory=list)
    claims: list[DraftClaim] = Field(default_factory=list)  # facts stated by the replacements


def _reason(f: CheckResult) -> str:
    return _REASON[f.check].format(subject=f.subject, detail=f.detail or "no detail")


def _render_block(content: SectionContent, bid: str) -> str:
    b = content.blocks[block_index(bid, len(content.blocks))]
    body = getattr(b, "code", None) or " | ".join(block_texts(b))
    return f"{b.type}: {body}"[:_BLOCK_CHARS]


def _relabel(block: Block, mapping: Mapping[str, str]) -> Block:
    raw = json.dumps(block.model_dump(mode="json"))
    swapped = _LABEL.sub(lambda m: mapping.get(m.group(0), m.group(0)), raw)
    return type(block).model_validate(json.loads(swapped))


class Rewriter:
    """Callable `(content, items) -> {block id: replacement}`. After a call, `new_claims` holds the claims the
    model made for the replacements (labels resolved to chunk ids) and `evidence` has grown by the excerpts it
    was shown. Both belong to the chapter: the pipeline reads them back."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        retriever: HybridRetriever,
        chunks: ChunkRepository,
        plan: ChapterPlan,
        evidence: dict[str, dict[str, object]],
        facts: Sequence[str],
        technical_level: str,
        instruction: str | None,
        job_id: str,
        project_id: str,
    ) -> None:
        self.llm, self.retriever, self.chunks = llm, retriever, chunks
        self.plan, self.evidence, self.facts = plan, evidence, facts
        self.level, self.instruction = technical_level, instruction
        self.job_id, self.project_id = job_id, project_id
        self.new_claims: list[ClaimRecord] = []

    async def _evidence(self, items: Sequence[RevisionItem]) -> list[Chunk]:
        found = {
            c.id: c
            for c in await self.chunks.get_many(self.project_id, [i for it in items for i in it.evidence_ids])
        }
        for it in items:
            if not it.query:
                continue
            result: RetrievalResult | None = None
            with contextlib.suppress(RetrievalFailed):  # one targeted retrieval per failing block
                result = await self.retriever.search(it.query, RetrievalMode.GUIDE)
            for r in (result.items if result else [])[:3]:
                found.setdefault(r.chunk.id, r.chunk)
        return list(found.values())

    async def __call__(self, content: SectionContent, items: Sequence[RevisionItem]) -> Mapping[str, Block]:
        self.new_claims = []
        shown = await self._evidence(items)
        retrieval = RetrievalResult(
            plan=RetrievalPlan(
                project_id=self.project_id, query=self.plan.objective, mode=RetrievalMode.GUIDE
            ),
            items=[
                RetrievedItem(chunk=c, score=round(1.0 - i / (len(shown) + 1), 4))
                for i, c in enumerate(shown)
            ],
            sufficient=True,
            trace=RetrievalTrace(config_version=f"rewrite-v{REWRITE_PROMPT_VERSION}"),
        )
        ctx = compile_context(
            objective="Repair blocks of the guide chapter whose title and objective are given under 'Chapter topic'.",
            retrieval=retrieval,
            facts=self.facts,
            profile=SessionProfile(technical_level=self.level, instruction=self.instruction),
            budget_tokens=REWRITE_CONTEXT_TOKENS,
        )
        if not ctx.evidence:
            return {}
        lines = [
            f"[{it.block}] why it failed: "
            + (
                "; ".join(_reason(f) for f in it.failures)
                or "a reviewer found it weak (vague, repeated from another chapter, or missing how and why)"
            )
            + f"\n    current: {_render_block(content, it.block)}"
            for it in items
        ]
        prompt = (
            ctx.render()
            + "\n\n## Chapter topic\n"
            + untrusted(
                redact_text(f"Title: {self.plan.title}\nObjective: {self.plan.objective}"),
                source=f"chapter:{self.plan.key}",
                trust="derived_summary",
            )
            + "\n\n## Blocks to repair\n"
            + untrusted(
                redact_text("\n".join(lines)),
                source=f"chapter_draft:{self.plan.key}",
                trust="repository_derived",
            )
        )
        req = LLMRequest(
            purpose="guide.revise",
            capability=ModelCapability.BALANCED,
            priority=Priority.P2,  # an enrichment: refused first when the per-job budget runs low
            system=_SYSTEM,
            messages=[Message(role="user", content=prompt)],
            temperature=0.2,
            max_output_tokens=3072,
            job_id=self.job_id,
            project_id=self.project_id,
        )
        draft = await self.llm.generate_structured(req, RewriteDraft)

        # this prompt's labels -> the chapter's labels; excerpts the chapter has not cited yet extend its set
        by_chunk = {str(ref["chunk_id"]): label for label, ref in self.evidence.items()}
        top = max((int(m.group(1)) for k in self.evidence if (m := _LABEL.fullmatch(k))), default=0)
        mapping: dict[str, str] = {}
        for e in ctx.evidence:
            if e.chunk_id not in by_chunk:
                top += 1
                by_chunk[e.chunk_id] = f"E{top}"
                self.evidence[f"E{top}"] = {
                    "chunk_id": e.chunk_id,
                    "path": e.path,
                    "start_line": e.start_line,
                    "end_line": e.end_line,
                    "symbol": e.symbol,
                }
            mapping[e.label] = by_chunk[e.chunk_id]

        shown_ids = {e.chunk_id for e in ctx.evidence}
        excerpts = [c for c in shown if c.id in shown_ids]
        asked = {it.block for it in items}
        out: dict[str, Block] = {}
        for r in draft.blocks:
            if r.block not in asked or r.block in out:
                continue
            block = _relabel(r.new, mapping)
            if isinstance(block, CodeBlock):  # a location the model claims is not trusted: it must be found
                block = block.model_copy(update={"path": None, "start_line": None})
            # code that is in none of the excerpts is dropped by `anchor_code`: that block keeps failing
            one = anchor_code(SectionContent(summary="", blocks=[block]), excerpts)
            if one.blocks:
                out[r.block] = one.blocks[0]
        if out:
            self.new_claims = [
                ClaimRecord(
                    text=c.text,
                    kind=ClaimKind(c.kind),
                    evidence_ids=[i for i in ctx.resolve_labels(c.evidence) if i in shown_ids],
                )
                for c in draft.claims
            ]
        log.info("guide.rewrite", chapter=self.plan.key, requested=len(items), rewritten=len(out))
        return out
