"""The model-backed rewrite of failing blocks: evidence wiring, label mapping, verifiability, budget refusal."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import BaseModel

from unfold.core.errors import ErrorCode, UnfoldError
from unfold.domain.enums import ClaimKind, Priority
from unfold.guide.models import ChapterPlan, CodeBlock, DraftClaim, ParagraphBlock, SectionContent
from unfold.guide.revise import RevisionItem
from unfold.guide.rewrite import RewriteDraft, Rewriter, RewrittenBlock
from unfold.guide.verification_models import CheckResult
from unfold.indexing.models import Chunk
from unfold.llm.schemas import LLMRequest
from unfold.retrieval.models import RetrievalPlan, RetrievalResult, RetrievalTrace, RetrievedItem

PLAN = ChapterPlan(key="k", title="Retries", kind="core", purpose="How retries work", target_minutes=3)
BODY = "def retry(n):\n    for i in range(n):\n        call()\n"


def _chunk(cid: str, path: str, start: int, body: str = BODY) -> Chunk:
    lines = body.count("\n")
    return Chunk(
        id=cid,
        project_id="p",
        path=path,
        start_line=start,
        end_line=start + lines - 1,
        kind="function",
        text=f"{path} :: retry\n{body}",
        content_hash="h-" + cid,
    )


class _Repo:
    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self.by_id = {c.id: c for c in chunks}
        self.asked: list[list[str]] = []

    async def get_many(self, project_id: str, ids: Sequence[str]) -> list[Chunk]:
        self.asked.append(list(ids))
        return [self.by_id[i] for i in ids if i in self.by_id]


class _Retriever:
    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self.chunks = list(chunks)
        self.queries: list[str] = []

    async def search(self, query: str, mode: Any) -> RetrievalResult:
        self.queries.append(query)
        return RetrievalResult(
            plan=RetrievalPlan(project_id="p", query=query, mode=mode),
            items=[RetrievedItem(chunk=c, score=0.5) for c in self.chunks],
            sufficient=True,
            trace=RetrievalTrace(config_version="t"),
        )


class _LLM:
    def __init__(self, draft: RewriteDraft | UnfoldError) -> None:
        self.draft = draft
        self.requests: list[LLMRequest] = []

    async def generate_structured(self, req: LLMRequest, schema: type[BaseModel]) -> Any:
        self.requests.append(req)
        if isinstance(self.draft, UnfoldError):
            raise self.draft
        return self.draft


def _rewriter(
    llm: _LLM, repo: _Repo, retriever: _Retriever, evidence: dict[str, dict[str, object]]
) -> Rewriter:
    return Rewriter(
        llm=llm,  # type: ignore[arg-type]
        retriever=retriever,  # type: ignore[arg-type]
        chunks=repo,  # type: ignore[arg-type]
        plan=PLAN,
        evidence=evidence,
        facts=[],
        technical_level="intermediate",
        instruction=None,
        job_id="job-1",
        project_id="p",
    )


ITEM = RevisionItem(
    block="b1",
    failures=[CheckResult(check="path_exists", ok=False, subject="pkg/gone.py:1-9", block="b1", detail="no")],
    evidence_ids=["c-new"],
    query="retry loop",
)
CONTENT = SectionContent(
    summary="s", blocks=[ParagraphBlock(text="fine [E1]."), ParagraphBlock(text="See pkg/gone.py:1-9.")]
)


async def test_replacement_is_relabelled_onto_the_chapters_labels_and_new_evidence_extends_them() -> None:
    new, old = _chunk("c-new", "pkg/retry.py", 10), _chunk("c-old", "pkg/old.py", 1)
    evidence = {
        "E1": {"chunk_id": "c-old", "path": "pkg/old.py", "start_line": 1, "end_line": 3, "symbol": None}
    }
    # the prompt numbers its excerpts from E1 in its own order; the chapter already uses E1 for another chunk
    llm = _LLM(
        RewriteDraft(
            blocks=[RewrittenBlock(block="b1", new=ParagraphBlock(text="`retry` loops n times [E1]."))],
            claims=[DraftClaim(text="`retry` loops n times", kind="project_fact", evidence=["E1"])],
        )
    )
    repo, retriever = _Repo([new, old]), _Retriever([])
    r = _rewriter(llm, repo, retriever, evidence)
    out = await r(CONTENT, [ITEM])

    assert set(out) == {"b1"}
    (req,) = llm.requests
    assert req.purpose == "guide.revise" and req.priority is Priority.P2 and req.job_id == "job-1"
    assert "pkg/gone.py:1-9" in req.messages[0].content
    assert repo.asked == [["c-new"]] and retriever.queries == ["retry loop"]
    # the excerpt the model cited as E1 is the new chunk: it became E2 of the chapter, E1 is untouched
    assert out["b1"].text == "`retry` loops n times [E2]."  # type: ignore[union-attr]
    assert evidence["E2"]["chunk_id"] == "c-new" and evidence["E2"]["path"] == "pkg/retry.py"
    assert evidence["E1"]["chunk_id"] == "c-old"
    (claim,) = r.new_claims
    assert claim.kind is ClaimKind.PROJECT_FACT and claim.evidence_ids == ["c-new"]


async def test_a_quoted_excerpt_that_is_not_in_the_evidence_is_not_accepted() -> None:
    chunk = _chunk("c-new", "pkg/retry.py", 10)
    fake = CodeBlock(
        language="python", code="def retry(n):\n    sleep(99)", path="pkg/retry.py", start_line=10
    )
    real = CodeBlock(language="python", code="def retry(n):\n    for i in range(n):", path="x", start_line=1)
    llm = _LLM(
        RewriteDraft(
            blocks=[
                RewrittenBlock(block="b1", new=fake),
                RewrittenBlock(block="b2", new=real),
                RewrittenBlock(block="b0", new=ParagraphBlock(text="not asked for")),
            ]
        )
    )
    r = _rewriter(llm, _Repo([chunk]), _Retriever([]), {})
    items = [ITEM, ITEM.model_copy(update={"block": "b2"})]
    out = await r(SectionContent(summary="s", blocks=[*CONTENT.blocks, ParagraphBlock(text="c")]), items)
    assert "b1" not in out  # unverifiable code is dropped, so that block keeps failing and is qualified
    assert "b0" not in out  # only the blocks that were asked for
    assert out["b2"].path == "pkg/retry.py" and out["b2"].start_line == 10  # type: ignore[union-attr]


async def test_no_evidence_means_no_model_call_and_a_refused_budget_propagates() -> None:
    llm = _LLM(RewriteDraft())
    empty = _rewriter(llm, _Repo([]), _Retriever([]), {})
    assert await empty(CONTENT, [ITEM]) == {} and llm.requests == []

    refused = UnfoldError("budget", code=ErrorCode.LLM_BUDGET_EXHAUSTED, retryable=False)
    r = _rewriter(_LLM(refused), _Repo([_chunk("c-new", "pkg/retry.py", 10)]), _Retriever([]), {})
    with pytest.raises(UnfoldError) as exc:
        await r(CONTENT, [ITEM])
    assert exc.value.code is ErrorCode.LLM_BUDGET_EXHAUSTED
