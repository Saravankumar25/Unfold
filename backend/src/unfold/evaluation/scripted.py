"""A deterministic stand-in for the model, for offline evaluation and for tests of the chat path and the pipeline.

`ScriptedLLM` records every request it receives (so prompts can be inspected) and answers with a function of the
prompt. The default chat answerer is extractive: it cites the evidence labels it was shown and quotes identifiers
from them, which makes a grounded answer a property of the context wiring rather than of any model. For the guide
pipeline it plans four sections and writes each from the evidence it was shown, citing real labels (plus one
invented identifier and one uncited claim, so grading always has something to catch).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable

from pydantic import BaseModel

from unfold.core.errors import ProviderCapacityError, UnfoldError
from unfold.guide.models import (
    DraftClaim,
    GuidePlan,
    GuideSectionPlan,
    ParagraphBlock,
    SectionContent,
    SectionDraft,
)
from unfold.llm.schemas import LLMRequest, LLMResponse, T, Usage

_EVIDENCE = re.compile(
    r"\[(E\d+)\] (\S+?):(\d+)-(\d+)[^\n]*\n<repository_data[^>]*>\n(.*?)\n</repository_data>", re.DOTALL
)
_IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{3,}(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\b")
_SKIP = frozenset({"self", "return", "import", "from", "class", "async", "await", "None", "True", "False"})


def evidence_blocks(prompt: str) -> list[tuple[str, str, str]]:
    """(label, path, text) for every evidence block rendered into a prompt."""
    return [(m.group(1), m.group(2), m.group(5)) for m in _EVIDENCE.finditer(prompt)]


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
        if schema is GuidePlan:
            out = self._plan()
        elif schema is SectionDraft:
            out = self._section(req)
        else:
            raise NotImplementedError(f"ScriptedLLM has no script for {schema.__name__}")
        return schema.model_validate(out.model_dump())

    @staticmethod
    def _plan() -> GuidePlan:
        titles = [
            ("overview", "Overview"),
            ("architecture", "Architecture"),
            ("data-flow", "Data flow"),
            ("running", "Running it"),
        ]
        return GuidePlan(
            estimated_read_minutes=12,
            sections=[
                GuideSectionPlan(
                    key=key,
                    title=title,
                    kind="backbone" if i < 2 else "core",
                    purpose=f"Explain {title.lower()} of this project",
                    target_minutes=3,
                    evidence_objectives=["app", "api", "routes", "retrieval"],
                    visual_needs=["architecture"] if i == 1 else [],
                )
                for i, (key, title) in enumerate(titles)
            ],
        )

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
