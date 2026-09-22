"""Qdrant wrapper for chunk vectors."""

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams


class VectorStore:
    """Stores and searches chunk embeddings in a Qdrant collection."""

    def __init__(self, url: str, collection: str) -> None:
        self.client = AsyncQdrantClient(url=url)
        self.collection = collection

    async def ensure_collection(self, dim: int) -> None:
        """Create the collection if it does not exist."""
        if not await self.client.collection_exists(self.collection):
            await self.client.create_collection(
                self.collection, vectors_config=VectorParams(size=dim, distance=Distance.COSINE)
            )

    async def upsert_chunks(self, ids: list[int], vectors: list[list[float]], payloads: list[dict]) -> None:
        """Insert or replace chunk vectors."""
        points = [PointStruct(id=i, vector=v, payload=p) for i, v, p in zip(ids, vectors, payloads)]
        await self.client.upsert(self.collection, points=points)

    async def search_vectors(self, vector: list[float], limit: int) -> list[tuple[int, float]]:
        """Return (chunk id, score) pairs for the nearest vectors."""
        hits = await self.client.query_points(self.collection, query=vector, limit=limit)
        return [(int(h.id), float(h.score)) for h in hits.points]

    async def delete_document(self, document_id: int) -> None:
        await self.client.delete(
            self.collection,
            points_selector={"filter": {"must": [{"key": "document_id", "match": {"value": document_id}}]}},
        )
