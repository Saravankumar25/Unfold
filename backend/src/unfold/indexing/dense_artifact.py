"""Dense store as one object per project: `projects/{id}/dense/{model_slug}-{ANALYSIS_VERSION}.npz` (ADR 0013).

The worker writes ids + L2-normalised float32 vectors + the filterable columns; readers load it once per process
(LRU, like `LexicalIndexCache`) and rank by exact cosine `V @ q`. Same filters and result shape as
`DenseIndex.search`, so retrieval code cannot tell the two apart. Embeddings are not cached: a re-run re-embeds.
"""

from __future__ import annotations

import asyncio
import io
import re
import time
import zipfile
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

from unfold import ANALYSIS_VERSION
from unfold.core.errors import ErrorCode, NotFound, UnfoldError
from unfold.core.logging import get_logger
from unfold.indexing.embeddings import Embedder, embedder_for
from unfold.indexing.errors import ModelUnavailable
from unfold.indexing.models import Chunk
from unfold.ports import BlobStore, project_prefix

if TYPE_CHECKING:
    from unfold.runtime import Runtime

log = get_logger(__name__)

EMBED_BATCH = 256
_FloatMatrix = npt.NDArray[np.float32]


def dense_key(project_id: str, model: str, version: str = ANALYSIS_VERSION) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    return f"{project_prefix(project_id)}dense/{slug}-{version}.npz"


def _normalise(v: _FloatMatrix) -> _FloatMatrix:
    norms = np.linalg.norm(v, axis=-1, keepdims=True)
    out: _FloatMatrix = (v / np.where(norms == 0, 1.0, norms)).astype(np.float32, copy=False)
    return out


class DenseArtifact:
    """Immutable in practice: `with_subsystems` returns a new instance."""

    def __init__(
        self,
        ids: Sequence[str],
        vectors: _FloatMatrix,
        subsystem: Sequence[str],
        content_type: Sequence[str],
        language: Sequence[str],
    ) -> None:
        if vectors.ndim != 2 or vectors.shape[0] != len(ids):
            raise ModelUnavailable("Dense artifact is inconsistent: ids and vectors differ in length.")
        self.ids = list(ids)
        self.vectors = vectors
        self.subsystem = np.array(subsystem, dtype=str)
        self.content_type = np.array(content_type, dtype=str)
        self.language = np.array(language, dtype=str)

    @classmethod
    def from_chunks(cls, chunks: Sequence[Chunk], vectors: _FloatMatrix) -> DenseArtifact:
        return cls(
            [c.id for c in chunks],
            _normalise(vectors),
            [c.subsystem_id or "" for c in chunks],
            [c.content_type.value for c in chunks],
            [c.language or "" for c in chunks],
        )

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        np.savez(
            buf,
            ids=np.array(self.ids, dtype=str),
            vectors=self.vectors,
            subsystem=self.subsystem,
            content_type=self.content_type,
            language=self.language,
        )
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> DenseArtifact:
        try:
            with np.load(io.BytesIO(data), allow_pickle=False) as z:
                return cls(
                    [str(i) for i in z["ids"]],
                    np.ascontiguousarray(z["vectors"], dtype=np.float32),
                    z["subsystem"],
                    z["content_type"],
                    z["language"],
                )
        except (ValueError, KeyError, OSError, EOFError, zipfile.BadZipFile) as exc:
            raise ModelUnavailable(f"Dense artifact is unreadable: {type(exc).__name__}") from exc

    def with_subsystems(self, mapping: Mapping[str, str]) -> DenseArtifact:
        # a list, not an in-place write: a fixed-width numpy string array would truncate longer ids
        sub = [mapping.get(cid, old) for cid, old in zip(self.ids, self.subsystem.tolist(), strict=True)]
        return DenseArtifact(self.ids, self.vectors, sub, self.content_type.tolist(), self.language.tolist())

    def search(
        self,
        query: Sequence[float],
        k: int,
        content_types: Sequence[str] | None = None,
        subsystem_ids: Sequence[str] | None = None,
    ) -> list[tuple[str, float]]:
        q = np.asarray(query, dtype=np.float32)
        if not self.ids or k <= 0 or not np.any(q):
            return []
        mask = np.ones(len(self.ids), dtype=bool)
        if content_types:
            mask &= np.isin(self.content_type, [str(c) for c in content_types])
        if subsystem_ids:
            mask &= np.isin(
                self.subsystem, [s for s in subsystem_ids if s]
            )  # "" = no subsystem, never a match
        rows = np.flatnonzero(mask)
        if not rows.size:
            return []
        scores = (self.vectors @ _normalise(q[None, :])[0])[rows]
        order = np.lexsort((rows, -scores))[:k]  # score desc, then insertion order: deterministic over ties
        return [(self.ids[int(rows[i])], float(scores[i])) for i in order]


class DenseArtifactCache:
    """In-process LRU of loaded artifacts keyed by object key.

    The key is fixed per project, model and analysis version while the worker rewrites the object (once after
    embedding, again when subsystems are set), so an entry is trusted for `ttl_seconds` only: a long-lived API
    process picks up a re-analysis within that bound. Analysis finishes before a project is readable, so this
    only matters for re-runs and for the subsystem rewrite.
    """

    def __init__(self, max_items: int = 8, ttl_seconds: float = 300.0) -> None:
        self.max_items = max_items
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, tuple[float, DenseArtifact]] = OrderedDict()
        self._lock = asyncio.Lock()

    def put(self, key: str, artifact: DenseArtifact) -> None:
        self._items[key] = (time.monotonic(), artifact)
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def invalidate(self, key: str) -> None:
        self._items.pop(key, None)

    def _fresh(self, key: str) -> DenseArtifact | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry[0] >= self.ttl_seconds:
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return entry[1]

    async def get(self, objects: BlobStore, key: str) -> DenseArtifact:
        """The artifact at `key`; raises `NotFound` when it has not been written."""
        hit = self._fresh(key)
        if hit is not None:
            return hit
        async with self._lock:
            hit = self._fresh(key)
            if hit is not None:
                return hit
            artifact = await asyncio.to_thread(DenseArtifact.from_bytes, await objects.get_bytes(key))
            self.put(key, artifact)
            return artifact


class ArtifactDenseStore:
    """`DenseStore` over the blob store; `upsert` writes a full snapshot of the chunks it is given."""

    def __init__(self, objects: BlobStore, embedder: Embedder, cache: DenseArtifactCache) -> None:
        self.objects = objects
        self.embedder = embedder
        self.cache = cache

    def _key(self, project_id: str) -> str:
        return dense_key(project_id, self.embedder.model_name)

    async def _save(self, key: str, artifact: DenseArtifact) -> None:
        data = await asyncio.to_thread(artifact.to_bytes)
        await self.objects.put_bytes(key, data, content_type="application/octet-stream")
        self.cache.put(key, artifact)
        log.info("dense.artifact_saved", key=key, vectors=len(artifact.ids), bytes=len(data))

    async def upsert(self, chunks: Sequence[Chunk]) -> int:
        if not chunks:
            return 0
        project_ids = {c.project_id for c in chunks}
        if len(project_ids) != 1:
            raise UnfoldError(
                "One dense artifact holds exactly one project.",
                code=ErrorCode.INTERNAL_ERROR,
                retryable=False,
            )
        parts = [
            await self.embedder.embed_documents_array([c.text for c in chunks[i : i + EMBED_BATCH]])
            for i in range(0, len(chunks), EMBED_BATCH)
        ]
        vectors = np.concatenate(parts).astype(np.float32, copy=False)
        await self._save(self._key(project_ids.pop()), DenseArtifact.from_chunks(chunks, vectors))
        return len(chunks)

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
        try:
            artifact = await self.cache.get(self.objects, self._key(project_id))
        except NotFound:
            return []  # nothing indexed yet: same as an empty collection
        vector = await self.embedder.embed_query(query)
        return await asyncio.to_thread(artifact.search, vector, k, content_types, subsystem_ids)

    async def set_subsystems(self, project_id: str, mapping: dict[str, str]) -> int:
        if not mapping:
            return 0
        key = self._key(project_id)
        try:
            artifact = await self.cache.get(self.objects, key)
        except NotFound:
            log.warning("dense.subsystems_without_artifact", project_id=project_id)
            return 0
        await self._save(key, artifact.with_subsystems(mapping))
        return len(mapping)

    async def delete_project(self, project_id: str) -> None:
        self.cache.invalidate(self._key(project_id))
        await self.objects.delete_prefix(f"{project_prefix(project_id)}dense/")


def artifact_store_for(runtime: Runtime) -> ArtifactDenseStore:
    cache = runtime.extras.get("dense_cache")
    if not isinstance(cache, DenseArtifactCache):
        cache = DenseArtifactCache()
        runtime.extras["dense_cache"] = cache
    return ArtifactDenseStore(runtime.objects, embedder_for(runtime), cache)
