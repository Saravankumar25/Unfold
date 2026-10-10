"""Dense index: fastembed vectors in one shared Qdrant collection per embedding model (LLD §10, ESD §4.3).

Every point carries `project_id`; every search is filtered by it. Embeddings are cached in Redis under
`emb:{sha1(model␟content_hash)}` (float32 bytes, TTL 7 days) so re-indexing unchanged chunks is free.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np
from qdrant_client import models as qm
from qdrant_client.http.exceptions import UnexpectedResponse

from unfold.core.logging import get_logger
from unfold.indexing.embeddings import Embedder, embedder_for
from unfold.indexing.errors import ModelUnavailable
from unfold.indexing.models import CHUNKER_VERSION, Chunk
from unfold.persistence.qdrant_store import QdrantStore
from unfold.persistence.redis_store import RedisStore

if TYPE_CHECKING:
    from unfold.runtime import Runtime

log = get_logger(__name__)

EMBED_CACHE_TTL = 7 * 86_400
UPSERT_BATCH = 256
PAYLOAD_INDEXES = ("project_id", "content_type", "language", "subsystem_id")
_POINT_NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://unfold.dev/chunk")


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_POINT_NS, chunk_id))


def embedding_cache_key(model: str, content_hash: str) -> str:
    digest = hashlib.sha1(f"{model}\x1f{content_hash}".encode(), usedforsecurity=False).hexdigest()
    return f"emb:{digest}"


def chunk_payload(chunk: Chunk) -> dict[str, Any]:
    return {
        "project_id": chunk.project_id,
        "chunk_id": chunk.id,
        "file_path": chunk.path,
        "language": chunk.language,
        "symbol_id": chunk.symbol_id,
        "symbol_name": chunk.symbol_name,
        "symbol_type": chunk.kind,
        "subsystem_id": chunk.subsystem_id,
        "graph_node_ids": chunk.graph_node_ids,
        "importance": chunk.importance,
        "content_type": chunk.content_type.value,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "trust": str(chunk.trust.get("label", "untrusted_source")),
        "content_hash": chunk.content_hash,
    }


def _project_filter(
    project_id: str, content_types: Sequence[str] | None = None, subsystem_ids: Sequence[str] | None = None
) -> qm.Filter:
    must: list[qm.Condition] = [qm.FieldCondition(key="project_id", match=qm.MatchValue(value=project_id))]
    if content_types:
        must.append(
            qm.FieldCondition(key="content_type", match=qm.MatchAny(any=[str(c) for c in content_types]))
        )
    if subsystem_ids:
        must.append(qm.FieldCondition(key="subsystem_id", match=qm.MatchAny(any=list(subsystem_ids))))
    return qm.Filter(must=must)


class DenseStore(Protocol):
    """What retrieval and the pipeline need from a dense index; `DenseIndex` (Qdrant) is the default."""

    async def upsert(self, chunks: Sequence[Chunk]) -> int: ...

    async def search(
        self,
        project_id: str,
        query: str,
        k: int = 40,
        content_types: Sequence[str] | None = None,
        subsystem_ids: Sequence[str] | None = None,
    ) -> list[tuple[str, float]]: ...

    async def set_subsystems(self, project_id: str, mapping: dict[str, str]) -> int: ...

    async def delete_project(self, project_id: str) -> None: ...


def dense_store_for(rt: Runtime) -> DenseStore:
    """The configured dense store (`DENSE_BACKEND`): Qdrant (default, the parity oracle) or the S3 artifact."""
    if rt.settings.dense_backend == "artifact":
        from unfold.indexing.dense_artifact import artifact_store_for

        return artifact_store_for(rt)
    return DenseIndex(rt.qdrant, embedder_for(rt), rt.redis)


class DenseIndex:
    def __init__(self, qdrant: QdrantStore, embedder: Embedder, redis: RedisStore) -> None:
        self.qdrant = qdrant
        self.embedder = embedder
        self.redis = redis
        self.collection = qdrant.collection
        self._ensured = False

    async def ensure_collection(self) -> None:
        if self._ensured:
            return
        client = self.qdrant.client
        dim = self.embedder.dimension
        if not await client.collection_exists(self.collection):
            try:
                await client.create_collection(
                    self.collection,
                    vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
                    metadata={
                        "embedding_model": self.embedder.model_name,
                        "dimension": dim,
                        "chunker_version": CHUNKER_VERSION,
                    },
                )
            except UnexpectedResponse as exc:  # created concurrently by another worker
                if exc.status_code != 409:
                    raise
        else:
            info = await client.get_collection(self.collection)
            vectors = info.config.params.vectors
            size = getattr(vectors, "size", None)
            if size is not None and int(size) != dim:
                raise ModelUnavailable(
                    f"Collection {self.collection} has dimension {size}, embedder produces {dim}.",
                    detail={"collection": self.collection},
                )
        for field_name in PAYLOAD_INDEXES:
            await client.create_payload_index(
                self.collection, field_name=field_name, field_schema=qm.PayloadSchemaType.KEYWORD, wait=True
            )
        self._ensured = True

    async def _vectors(self, chunks: Sequence[Chunk]) -> np.ndarray:
        model = self.embedder.model_name
        keys = [embedding_cache_key(model, c.content_hash) for c in chunks]
        dim = self.embedder.dimension
        out = np.zeros((len(chunks), dim), dtype=np.float32)
        missing: list[int] = []
        cached = await self.redis.client.mget(keys) if keys else []
        for i, raw in enumerate(cached):
            if isinstance(raw, bytes) and len(raw) == dim * 4:
                out[i] = np.frombuffer(raw, dtype=np.float32)
            else:
                missing.append(i)
        if missing:
            arr = await self.embedder.embed_documents_array([chunks[i].text for i in missing])
            pipe = self.redis.client.pipeline(transaction=False)
            for row, i in enumerate(missing):
                vec = np.ascontiguousarray(arr[row], dtype=np.float32)
                out[i] = vec
                pipe.set(keys[i], vec.tobytes(), ex=EMBED_CACHE_TTL)
            await pipe.execute()
        log.info("dense.vectors", total=len(chunks), cache_hits=len(chunks) - len(missing))
        return out

    async def upsert(self, chunks: Sequence[Chunk]) -> int:
        if not chunks:
            return 0
        await self.ensure_collection()
        total = 0
        for i in range(0, len(chunks), UPSERT_BATCH):
            batch = list(chunks[i : i + UPSERT_BATCH])
            vectors = await self._vectors(batch)
            points = [
                qm.PointStruct(id=point_id(c.id), vector=vectors[j].tolist(), payload=chunk_payload(c))
                for j, c in enumerate(batch)
            ]
            await self.qdrant.client.upsert(self.collection, points=points, wait=True)
            total += len(points)
        return total

    async def search(
        self,
        project_id: str,
        query: str,
        k: int = 40,
        content_types: Sequence[str] | None = None,
        subsystem_ids: Sequence[str] | None = None,
    ) -> list[tuple[str, float]]:
        if not query.strip() or k <= 0:
            return []
        await self.ensure_collection()
        vector = await self.embedder.embed_query(query)
        resp = await self.qdrant.client.query_points(
            self.collection,
            query=vector,
            query_filter=_project_filter(project_id, content_types, subsystem_ids),
            limit=k,
            with_payload=["chunk_id"],
        )
        return [(str(p.payload["chunk_id"]), float(p.score)) for p in resp.points if p.payload]

    async def set_subsystems(self, project_id: str, mapping: dict[str, str]) -> int:
        """Update `subsystem_id` payloads (chunk id → subsystem id) after subsystem discovery."""
        if not mapping:
            return 0
        await self.ensure_collection()
        by_sub: dict[str, list[str]] = {}
        for cid, sid in mapping.items():
            by_sub.setdefault(sid, []).append(point_id(cid))
        for sid, ids in sorted(by_sub.items()):
            for i in range(0, len(ids), 1000):
                await self.qdrant.client.set_payload(
                    self.collection,
                    payload={"subsystem_id": sid},
                    points=qm.PointIdsList(points=list(ids[i : i + 1000])),
                    wait=True,
                )
        return len(mapping)

    async def delete_project(self, project_id: str) -> None:
        if not await self.qdrant.client.collection_exists(self.collection):
            return
        await self.qdrant.client.delete(
            self.collection,
            points_selector=qm.FilterSelector(filter=_project_filter(project_id)),
            wait=True,
        )
