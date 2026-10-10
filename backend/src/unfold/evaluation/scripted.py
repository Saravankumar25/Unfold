"""A deterministic stand-in for the model, for offline evaluation and for tests of the chat path and the pipeline.

`ScriptedLLM` records every request it receives (so prompts can be inspected) and answers with a function of the
prompt. The default chat answerer is extractive: it cites the evidence labels it was shown and quotes identifiers
from them, which makes a grounded answer a property of the context wiring rather than of any model. For the guide
pipeline it echoes the deterministic outline it was shown (`scripted_refinement`) and writes each chapter from the
evidence it was shown, citing real labels (plus one invented identifier and one uncited claim, so grading always has
something to catch).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable

from pydantic import BaseModel

from unfold.core.errors import ProviderCapacityError, UnfoldError
from unfold.guide.models import DraftClaim, ParagraphBlock, SectionContent, SectionDraft
from unfold.guide.plan import ChapterRefinement, PlanRefinement
from unfold.guide.research import QuestionSet
from unfold.guide.rewrite import RewriteDraft, RewrittenBlock
from unfold.guide.verification_models import SemanticCritique
from unfold.llm.schemas import LLMRequest, LLMResponse, T, Usage

_EVIDENCE = re.compile(
    r"\[(E\d+)\] (\S+?):(\d+)-(\d+)[^\n]*\n<repository_data[^>]*>\n(.*?)\n</repository_data>", re.DOTALL
)
_IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{3,}(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\b")
_SKIP = frozenset({"self", "return", "import", "from", "class", "async", "await", "None", "True", "False"})
_OUTLINE = re.compile(r"^- key=(\S+) \| title=(.*?) \| objective=(.*?) \| depends_on=", re.MULTILINE)
_QUESTION = re.compile(r"^    \? (.+)$", re.MULTILINE)
_REPAIR = re.compile(r"^\[(b\d+)\] why it failed", re.MULTILINE)


def evidence_blocks(prompt: str) -> list[tuple[str, str, str]]:
    """(label, path, text) for every evidence block rendered into a prompt."""
    return [(m.group(1), m.group(2), m.group(5)) for m in _EVIDENCE.finditer(prompt)]


def scripted_refinement(prompt: str) -> PlanRefinement:
    """Echo the outline rendered in a planner prompt: same keys, titles, objectives and questions."""
    heads = list(_OUTLINE.finditer(prompt))
    chapters = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(prompt)
        qs = _QUESTION.findall(prompt[m.end() : end])
        chapters.append(
            ChapterRefinement(key=m.group(1), title=m.group(2), objective=m.group(3), questions=qs)
        )
    return PlanRefinement(chapters=chapters)


def scripted_critique() -> SemanticCritique:
    """A content-free critique that flags nothing: the deterministic checks decide in scripted runs."""
    return SemanticCritique(coverage=0.8, depth=0.7, coherence=0.8, specificity=0.7, redundancy=1.0)


def scripted_rewrite(prompt: str) -> RewriteDraft:
    """Replace every block named in a repair prompt by one that states the gap and cites nothing."""
    return RewriteDraft(
        blocks=[
            RewrittenBlock(block=b, new=ParagraphBlock(text="The evidence does not show this."))
            for b in dict.fromkeys(_REPAIR.findall(prompt))
        ]
    )


def extractive_answer(req: LLMRequest, *, max_blocks: int = 3) -> str:
    """One cited sentence per evidence block: names an identifier that really occurs in that block."""
    prompt = req.messages[-1].content
    sentences: list[str] = []
    for label, path, text in evidence_blocks(prompt)[:max_blocks]:
        names = [
            n for n in _IDENT.findall(text) if n not in _SKIP and ("_" in n or any(c.isupper() for c in n))
        ]
        if names:
            sentences.append(f"`{names[0]}` is defined or used in `{path}` [{label}].")
    return " ".join(sentences) or "The evidence does not answer this question."


class ScriptedLLM:
    def __init__(
        self,
        answer: Callable[[LLMRequest], str] = extractive_answer,
        *,
        piece_chars: int = 40,
        fail_with: UnfoldError | None = None,
        fail_on_section: int | None = None,
    ) -> None:
        self._fail_on_section = fail_on_section
        self.calls: list[str] = []  # purposes, in order
        self.sections_written = 0
        self._answer = answer
        self._piece_chars = piece_chars
        self._fail_with = fail_with
        self.requests: list[LLMRequest] = []

    async def generate(self, req: LLMRequest) -> LLMResponse:
        self.requests.append(req)
        self.calls.append(req.purpose)
        if self._fail_with is not None:
            raise self._fail_with
        return LLMResponse(
            text=self._answer(req), provider="scripted", model="scripted", usage=Usage(), finish_reason="stop"
        )

    async def generate_structured(self, req: LLMRequest, schema: type[T]) -> T:
        self.requests.append(req)
        self.calls.append(req.purpose)
        out: BaseModel
        if schema is PlanRefinement:
            out = scripted_refinement(req.messages[0].content)
        elif schema is SectionDraft:
            out = self._section(req)
        elif schema is QuestionSet:
            out = QuestionSet(questions=["What does this part do?", "How is it used?"])
        elif schema is SemanticCritique:
            out = scripted_critique()
        elif schema is RewriteDraft:
            out = scripted_rewrite(req.messages[0].content)
        else:
            raise NotImplementedError(f"ScriptedLLM has no script for {schema.__name__}")
        return schema.model_validate(out.model_dump())

    def _section(self, req: LLMRequest) -> SectionDraft:
        if self._fail_on_section is not None and self.sections_written >= self._fail_on_section:
            raise ProviderCapacityError("scripted capacity failure", retryable=True)
        self.sections_written += 1
        blocks = evidence_blocks(req.messages[0].content)
        assert blocks, "the writer must always be given labelled evidence"
        label, path, _text = blocks[0]
        name = path.rsplit("/", 1)[-1].split(".")[0]
        return SectionDraft(
            content=SectionContent(
                summary=f"How {name} fits in.",
                blocks=[ParagraphBlock(text=f"The code in `{path}` is part of the project [{label}].")],
            ),
            claims=[
                DraftClaim(text=f"`{path}` is part of the project", kind="project_fact", evidence=[label]),
                DraftClaim(
                    text="There is a `NonexistentWidgetFactory` class", kind="project_fact", evidence=[label]
                ),
                DraftClaim(text="Nothing cites this", kind="project_fact", evidence=[]),
            ],
        )

    async def stream(self, req: LLMRequest) -> AsyncIterator[str]:
        self.requests.append(req)
        self.calls.append(req.purpose)
        if self._fail_with is not None:
            raise self._fail_with
        text = self._answer(req)
        for i in range(0, len(text), self._piece_chars):
            yield text[i : i + self._piece_chars]
