"""Chunk persistence (LLD §4) — the evidence store behind every retrieval hit.

`ChunkRepository` keeps chunks in Postgres (the local stack). The AWS profile uses `indexing.object_chunks`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, insert, select, update

from unfold.domain.enums import ContentType
from unfold.indexing.models import Chunk
from unfold.persistence.postgres.db import Database
from unfold.persistence.postgres.models import ChunkRow

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
