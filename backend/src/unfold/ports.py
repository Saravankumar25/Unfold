"""Ports: the only infrastructure surface the pipeline, API and tests depend on (HLD §3).

Local adapters live next to the SDK they wrap (`persistence/`, `worker/queue.py`); phase 4 adds the AWS ones
(S3, SQS, DynamoDB) behind the same protocols. Nothing outside an adapter imports `redis`, `arq` or `minio`
(enforced by `tests/unit/test_architecture.py`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


def project_prefix(project_id: str) -> str:
    """Key prefix under which every blob of one project lives (cleanup deletes by this prefix)."""
    return f"projects/{project_id}/"


class BlobStore(Protocol):
    bucket: str  # namespace; also keys the in-process snapshot cache

    async def ensure_bucket(self) -> None: ...
    async def put_bytes(self, key: str, data: bytes, content_type: str = ...) -> None: ...
    async def put_file(self, key: str, path: Path, content_type: str = ...) -> None: ...
    async def get_bytes(self, key: str) -> bytes: ...
    async def get_file(self, key: str, path: Path) -> Path: ...
    async def delete_prefix(self, prefix: str) -> int: ...
    async def ping(self) -> None: ...


class JobQueue(Protocol):
    """Enqueue is idempotent per key: a duplicate (job, attempt) is dropped, never run twice."""

    async def enqueue_analysis(self, job_id: str, attempt: int, *, defer_s: float | None = None) -> None: ...
    async def enqueue_project_cleanup(self, project_id: str) -> None: ...
    async def aclose(self) -> None: ...


class RateLimiter(Protocol):
    async def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Count one call; True when it is within `limit` for the current fixed window."""
        ...

    async def delete_prefix(self, prefix: str) -> int: ...
    async def ping(self) -> None: ...


class ProgressStore(Protocol):
    """Durable, ordered, replayable per-project progress events. `seq` is strictly increasing."""

    async def publish(self, project_id: str, event: dict[str, Any]) -> int: ...
    async def read_after(
        self, project_id: str, after: int = 0, *, limit: int = 100
    ) -> list[tuple[int, dict[str, Any]]]: ...
    async def delete_project(self, project_id: str) -> int: ...
