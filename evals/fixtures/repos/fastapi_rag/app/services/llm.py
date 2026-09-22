"""LLM client that answers questions from retrieved context with citations."""

from dataclasses import dataclass, field

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.schemas import Citation
from app.services.retriever import RetrievedChunk

SYSTEM_PROMPT = (
    "Answer only from the provided context. Cite chunks as [n]. If the context is insufficient, say so."
)


@dataclass
class Answer:
    text: str
    citations: list[Citation] = field(default_factory=list)


class LLMClient:
    """Thin wrapper over the OpenAI chat completions API."""

    def __init__(self) -> None:
        settings = get_settings()
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.chat_model

    def build_prompt(self, question: str, hits: list[RetrievedChunk]) -> str:
        """Render retrieved chunks into a numbered context block."""
        context = "\n\n".join(f"[{n}] {hit.text}" for n, hit in enumerate(hits, start=1))
        return f"Context:\n{context}\n\nQuestion: {question}"

    async def answer(self, question: str, hits: list[RetrievedChunk]) -> Answer:
        """Ask the model for a grounded answer."""
        response = await self.client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self.build_prompt(question, hits)},
            ],
        )
        text = response.choices[0].message.content or ""
        citations = [Citation(chunk_id=h.chunk_id, document_id=h.document_id, score=h.score) for h in hits]
        return Answer(text=text, citations=citations)
