"""Qdrant client lifecycle (ESD §4.3). Collection/points logic lives in `unfold.indexing.dense`."""

from __future__ import annotations

import re

from qdrant_client import AsyncQdrantClient

from unfold.core.config import Settings


def collection_name(settings: Settings) -> str:
    """One shared collection per embedding model; projects are separated by a `project_id` payload filter."""
    slug = re.sub(r"[^a-z0-9]+", "_", settings.embedding_model.lower()).strip("_")
    return f"{settings.qdrant_collection_prefix}_{slug}"


class QdrantStore:
    def __init__(self, settings: Settings) -> None:
        self.client = AsyncQdrantClient(url=settings.qdrant_url, timeout=60)
        self.collection = collection_name(settings)

    async def ping(self) -> None:
        await self.client.get_collections()

    async def aclose(self) -> None:
        await self.client.close()
