"""Chunk persistence (LLD §4) — the evidence store behind every retrieval hit.

`ChunkRepository` keeps chunks in Postgres (local default). `ObjectChunkStore` keeps one gzipped JSON object per
project in the blob store (ADR 0012): an object PUT is atomic, so "replace every chunk" needs no transaction, and
a project's chunks are read as one object and served from a small per-process cache.
"""

from __future__ import annotations

import asyncio
import gzip
import time
import uuid
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any

import orjson
from sqlalchemy import delete, insert, select, update

from unfold.domain.enums import ContentType
from unfold.indexing.models import Chunk
from unfold.persistence.postgres.db import Database
from unfold.persistence.postgres.models import ChunkRow
from unfold.ports import BlobStore, project_prefix

INSERT_BATCH = 500
_IN_BATCH = 1000


def _pid(project_id: str) -> uuid.UUID:
    return uuid.UUID(str(project_id))


def _to_row(chunk: Chunk) -> dict[str, Any]:
    return {
        "id": chunk.id,
        "project_id": _pid(chunk.project_id),
        "path": chunk.path,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "kind": chunk.kind,
        "symbol_id": chunk.symbol_id,
        "symbol_name": chunk.symbol_name,
        "language": chunk.language,
        "content_type": chunk.content_type.value,
        "importance": chunk.importance,
        "subsystem_id": chunk.subsystem_id,
        "graph_node_ids": list(chunk.graph_node_ids),
        "trust": dict(chunk.trust),
        "text": chunk.text,
        "content_hash": chunk.content_hash,
    }


def _from_row(row: ChunkRow) -> Chunk:
    return Chunk(
        id=row.id,
        project_id=str(row.project_id),
        path=row.path,
        start_line=row.start_line,
        end_line=row.end_line,
        kind=row.kind,
        symbol_id=row.symbol_id,
        symbol_name=row.symbol_name,
        language=row.language,
        content_type=ContentType(row.content_type),
        importance=row.importance,
        subsystem_id=row.subsystem_id,
        graph_node_ids=list(row.graph_node_ids or []),
        trust=dict(row.trust or {}),
        text=row.text,
        content_hash=row.content_hash,
    )


class ChunkRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def replace_project_chunks(self, project_id: str, chunks: Sequence[Chunk]) -> int:
        """Delete every chunk of the project and insert `chunks` — one transaction, batched inserts."""
        pid = _pid(project_id)
        rows = [_to_row(c) for c in chunks]
        for r in rows:
            if r["project_id"] != pid:
                raise ValueError("chunk belongs to a different project")
        async with self.db.transaction() as s:
            await s.execute(delete(ChunkRow).where(ChunkRow.project_id == pid))
            for i in range(0, len(rows), INSERT_BATCH):
                await s.execute(insert(ChunkRow), rows[i : i + INSERT_BATCH])
        return len(rows)

    async def get_many(self, project_id: str, ids: Sequence[str]) -> list[Chunk]:
        """Chunks by id in the order requested (unknown ids are skipped)."""
        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return []
        found: dict[str, Chunk] = {}
        async with self.db.session() as s:
            for i in range(0, len(wanted), _IN_BATCH):
                res = await s.execute(
                    select(ChunkRow).where(
                        ChunkRow.project_id == _pid(project_id), ChunkRow.id.in_(wanted[i : i + _IN_BATCH])
                    )
                )
                for row in res.scalars():
                    found[row.id] = _from_row(row)
        return [found[i] for i in wanted if i in found]

    async def by_path(self, project_id: str, path: str) -> list[Chunk]:
        async with self.db.session() as s:
            res = await s.execute(
                select(ChunkRow)
                .where(ChunkRow.project_id == _pid(project_id), ChunkRow.path == path)
                .order_by(ChunkRow.start_line, ChunkRow.end_line, ChunkRow.id)
            )
            return [_from_row(r) for r in res.scalars()]

    async def for_symbols(self, project_id: str, symbol_ids: Sequence[str]) -> list[Chunk]:
        ids = list(dict.fromkeys(symbol_ids))
        if not ids:
            return []
        async with self.db.session() as s:
            res = await s.execute(
                select(ChunkRow)
                .where(ChunkRow.project_id == _pid(project_id), ChunkRow.symbol_id.in_(ids))
                .order_by(ChunkRow.path, ChunkRow.start_line, ChunkRow.id)
            )
            return [_from_row(r) for r in res.scalars()]

    async def all_ids(self, project_id: str) -> list[str]:
        async with self.db.session() as s:
            res = await s.execute(
                select(ChunkRow.id)
                .where(ChunkRow.project_id == _pid(project_id))
                .order_by(ChunkRow.path, ChunkRow.start_line, ChunkRow.id)
            )
            return [str(x) for x in res.scalars()]

    async def list_project(self, project_id: str) -> list[Chunk]:
        """Every chunk of the project (path, line order) — e.g. to rebuild the BM25 artifact."""
        async with self.db.session() as s:
            res = await s.execute(
                select(ChunkRow)
                .where(ChunkRow.project_id == _pid(project_id))
                .order_by(ChunkRow.path, ChunkRow.start_line, ChunkRow.id)
            )
            return [_from_row(r) for r in res.scalars()]

    async def set_subsystems(self, project_id: str, mapping: dict[str, str]) -> int:
        """chunk id → subsystem id. Returns the number of rows updated."""
        by_sub: dict[str, list[str]] = {}
        for cid, sid in mapping.items():
            by_sub.setdefault(sid, []).append(cid)
        updated = 0
        async with self.db.transaction() as s:
            for sid, ids in sorted(by_sub.items()):
                for i in range(0, len(ids), _IN_BATCH):
                    res = await s.execute(
                        update(ChunkRow)
                        .where(
                            ChunkRow.project_id == _pid(project_id), ChunkRow.id.in_(ids[i : i + _IN_BATCH])
                        )
                        .values(subsystem_id=sid)
                    )
                    updated += int(getattr(res, "rowcount", 0) or 0)
        return updated


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
