"""`ChunkStore` over the blob store (ADR 0012): one gzipped JSON object per project.

An object PUT is atomic, so "replace every chunk" needs no transaction, and a project's chunks are read as one
object and served from a small per-process cache. Imports no database client, so the API image stays lean.
"""

from __future__ import annotations

import asyncio
import gzip
import time
from collections import OrderedDict
from collections.abc import Sequence

import orjson

from unfold.indexing.models import Chunk
from unfold.ports import BlobStore, project_prefix


def _chunks_key(project_id: str) -> str:
    return f"{project_prefix(project_id)}chunks.json.gz"


class ObjectChunkStore:
    """`ChunkStore` over a `BlobStore`. One writer per project (the analysis job), many readers."""

    def __init__(self, objects: BlobStore, *, cache_projects: int = 4, cache_seconds: float = 300.0) -> None:
        self._objects = objects
        self._cache: OrderedDict[str, tuple[float, list[Chunk]]] = OrderedDict()
        self._max = cache_projects
        self._ttl = cache_seconds

    async def _load(self, project_id: str) -> list[Chunk]:
        hit = self._cache.get(project_id)
        if hit is not None and time.monotonic() - hit[0] < self._ttl:
            self._cache.move_to_end(project_id)
            return hit[1]
        raw = await self._objects.get_bytes(_chunks_key(project_id))
        chunks = await asyncio.to_thread(
            lambda: [Chunk.model_validate(c) for c in orjson.loads(gzip.decompress(raw))]
        )
        self._remember(project_id, chunks)
        return chunks

    def _remember(self, project_id: str, chunks: list[Chunk]) -> None:
        self._cache[project_id] = (time.monotonic(), chunks)
        self._cache.move_to_end(project_id)
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)

    async def _store(self, project_id: str, chunks: list[Chunk]) -> None:
        payload = await asyncio.to_thread(
            lambda: gzip.compress(orjson.dumps([c.model_dump(mode="json") for c in chunks]), compresslevel=6)
        )
        await self._objects.put_bytes(_chunks_key(project_id), payload, "application/gzip")
        self._remember(project_id, chunks)

    async def replace_project_chunks(self, project_id: str, chunks: Sequence[Chunk]) -> int:
        if any(c.project_id != project_id for c in chunks):
            raise ValueError("chunk belongs to a different project")
        await self._store(project_id, sorted(chunks, key=_order))
        return len(chunks)

    async def get_many(self, project_id: str, ids: Sequence[str]) -> list[Chunk]:
        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return []
        by_id = {c.id: c for c in await self._load(project_id)}
        return [by_id[i] for i in wanted if i in by_id]

    async def by_path(self, project_id: str, path: str) -> list[Chunk]:
        return [c for c in await self._load(project_id) if c.path == path]

    async def for_symbols(self, project_id: str, symbol_ids: Sequence[str]) -> list[Chunk]:
        wanted = set(symbol_ids)
        return [c for c in await self._load(project_id) if c.symbol_id in wanted]

    async def all_ids(self, project_id: str) -> list[str]:
        return [c.id for c in await self._load(project_id)]

    async def list_project(self, project_id: str) -> list[Chunk]:
        return list(await self._load(project_id))

    async def set_subsystems(self, project_id: str, mapping: dict[str, str]) -> int:
        chunks = await self._load(project_id)
        updated = 0
        changed: list[Chunk] = []
        for c in chunks:
            sid = mapping.get(c.id)
            if sid is not None and c.subsystem_id != sid:
                c = c.model_copy(update={"subsystem_id": sid})
                updated += 1
            changed.append(c)
        if updated:
            await self._store(project_id, changed)
        return updated


def _order(c: Chunk) -> tuple[str, int, int, str]:
    return (c.path, c.start_line, c.end_line, c.id)
