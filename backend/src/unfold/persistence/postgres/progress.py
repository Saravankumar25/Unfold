"""`ProgressStore` adapter on Postgres: progress survives restarts and needs no Redis stream (HLD D10)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select

from unfold.persistence.postgres.db import Database
from unfold.persistence.postgres.models import JobEventRow
from unfold.ports import ProgressStore


class PostgresProgressStore(ProgressStore):
    def __init__(self, db: Database) -> None:
        self._db = db

    async def publish(self, project_id: str, event: dict[str, Any]) -> int:
        row = JobEventRow(project_id=uuid.UUID(project_id), event=event)
        async with self._db.transaction() as s:
            s.add(row)
            await s.flush()
            return row.id

    async def read_after(
        self, project_id: str, after: int = 0, *, limit: int = 100
    ) -> list[tuple[int, dict[str, Any]]]:
        stmt = (
            select(JobEventRow.id, JobEventRow.event)
            .where(JobEventRow.project_id == uuid.UUID(project_id), JobEventRow.id > after)
            .order_by(JobEventRow.id)
            .limit(limit)
        )
        async with self._db.session() as s:
            return [(int(i), dict(e)) for i, e in (await s.execute(stmt)).all()]

    async def delete_project(self, project_id: str) -> int:
        async with self._db.transaction() as s:
            result = await s.execute(
                delete(JobEventRow).where(JobEventRow.project_id == uuid.UUID(project_id))
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]
