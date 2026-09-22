"""Hybrid retrieval: BM25 lexical + dense vector search fused with Reciprocal Rank Fusion."""

from dataclasses import dataclass

from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Chunk
from app.services.embeddings import embed_query
from app.services.vector_store import VectorStore

RRF_K = 60


@dataclass
class RetrievedChunk:
    chunk_id: int
    document_id: int
    text: str
    score: float


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Fuse several ranked id lists: score(d) = sum(1 / (k + rank))."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


class HybridRetriever:
    """Combines BM25 over chunk text with Qdrant vector search."""

    def __init__(self, db: AsyncSession) -> None:
        settings = get_settings()
        self.db = db
        self.store = VectorStore(settings.qdrant_url, settings.collection_name)

    async def _load_chunks(self) -> list[Chunk]:
        result = await self.db.execute(select(Chunk))
        return list(result.scalars())

    async def lexical_search(self, question: str, chunks: list[Chunk], limit: int) -> list[int]:
        """Rank chunks with BM25."""
        corpus = [c.text.lower().split() for c in chunks]
        bm25 = BM25Okapi(corpus)
        scores = bm25.get_scores(question.lower().split())
        ranked = sorted(zip(chunks, scores), key=lambda pair: pair[1], reverse=True)[:limit]
        return [c.id for c, _ in ranked]

    async def dense_search(self, question: str, limit: int) -> list[int]:
        """Rank chunks by vector similarity."""
        hits = await self.store.search_vectors(embed_query(question), limit)
        return [chunk_id for chunk_id, _ in hits]

    async def search(self, question: str, top_k: int = 8) -> list[RetrievedChunk]:
        """Run both retrievers and fuse their rankings."""
        chunks = await self._load_chunks()
        by_id = {c.id: c for c in chunks}
        lexical = await self.lexical_search(question, chunks, top_k * 4)
        dense = await self.dense_search(question, top_k * 4)
        fused = reciprocal_rank_fusion([lexical, dense])
        best = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [
            RetrievedChunk(chunk_id=i, document_id=by_id[i].document_id, text=by_id[i].text, score=s)
            for i, s in best
            if i in by_id
        ]
