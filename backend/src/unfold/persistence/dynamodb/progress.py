"""`ProgressStore` on DynamoDB: ordered, replayable per-project events (ADR 0012, invariant 6).

`publish` allocates the next sequence number with one conditional `ADD` on the project record (so a deleted
project accepts nothing), then writes the event item. Publishers are sequential per project (the worker, then the
API before it enqueues), so readers never see a later seq before an earlier one.
"""

from __future__ import annotations

import time
from typing import Any

from unfold.core.errors import StoreUnavailable
from unfold.core.logging import get_logger
from unfold.persistence.dynamodb.table import ConditionFailed, DynamoTable
from unfold.ports import ProgressStore

log = get_logger(__name__)
_EVT = "EVT#"
_MAX_SEQ = 999_999_999_999
EVENT_TTL_SECONDS = 7 * 86_400  # cosmetic backstop; cleanup deletes the partition explicitly


def event_sk(seq: int) -> str:
    return f"{_EVT}{seq:012d}"


class DynamoProgressStore(ProgressStore):
    def __init__(self, table: DynamoTable) -> None:
        self._table = table

    async def publish(self, project_id: str, event: dict[str, Any]) -> int:
        """Best effort like the Postgres adapter: a deleted project or an outage must not fail the job (0)."""
        pk = f"P#{project_id}"
        try:
            meta = await self._table.update(
                pk,
                "META",
                "ADD evt_seq :one",
                values={":one": 1},
                condition="attribute_exists(PK)",
            )
            seq = int(meta["evt_seq"])
            await self._table.put(
                {"PK": pk, "SK": event_sk(seq), "event": event, "ttl": int(time.time()) + EVENT_TTL_SECONDS},
                condition="attribute_not_exists(PK)",
            )
        except (ConditionFailed, StoreUnavailable) as exc:
            log.warning("progress.publish_failed", project_id=project_id, error=type(exc).__name__)
            return 0
        return seq

    async def read_after(
        self, project_id: str, after: int = 0, *, limit: int = 100
    ) -> list[tuple[int, dict[str, Any]]]:
        items = await self._table.query(
            f"P#{project_id}",
            sk_between=(event_sk(min(after + 1, _MAX_SEQ)), event_sk(_MAX_SEQ)),
            consistent=True,
            limit=limit,
        )
        return [(int(i["SK"][len(_EVT) :]), dict(i["event"])) for i in items]

    async def delete_project(self, project_id: str) -> int:
        items = await self._table.query(f"P#{project_id}", sk_prefix=_EVT, consistent=True)
        await self._table.batch_write(deletes=[(i["PK"], i["SK"]) for i in items])
        return len(items)
