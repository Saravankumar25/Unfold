"""Ports: the only infrastructure surface the pipeline, API and tests depend on (HLD §3).

Local adapters live next to the SDK they wrap (`persistence/`, `worker/queue.py`); phase 4 adds the AWS ones
(S3, SQS, DynamoDB) behind the same protocols. Nothing outside an adapter imports `redis`, `arq`, `minio` or
`boto3` (enforced by `tests/unit/test_architecture.py`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from unfold.domain.records import (
    ArtifactRec,
    ChatMessageRec,
    ChatRec,
    DiagramRec,
    JobRec,
    ProjectRec,
    SectionRec,
    SessionRec,
    SummaryRec,
)
from unfold.indexing.models import Chunk


def project_prefix(project_id: str) -> str:
    """Key prefix under which every blob of one project lives (cleanup deletes by this prefix)."""
    return f"projects/{project_id}/"


# Rate-limit keys. Per-session and per-project keys are removed with their owner (cleanup); per-IP and global
# counters are fixed windows that carry no owner and expire by TTL alone.
SESSION_RATE_BUCKETS = ("projects", "chat")
PROJECT_RATE_BUCKETS = ("export", "regenerate")


def session_rate_key(bucket: str, session_id: object) -> str:
    return f"{bucket}:{session_id}"


def project_rate_key(bucket: str, project_id: object) -> str:
    return f"{bucket}:project:{project_id}"


class BlobStore(Protocol):
    bucket: str  # namespace; also keys the in-process snapshot cache

    async def ensure_bucket(self) -> None: ...
    async def put_bytes(self, key: str, data: bytes, content_type: str = ...) -> None: ...
    async def put_file(self, key: str, path: Path, content_type: str = ...) -> None: ...
    async def get_bytes(self, key: str) -> bytes: ...
    async def get_file(self, key: str, path: Path) -> Path: ...
    async def delete_prefix(self, prefix: str) -> int: ...
    async def presigned_get_url(self, key: str, expires_s: int, *, filename: str) -> str:
        """A time-limited URL a browser can GET directly (S3: presigned GetObject)."""
        ...

    async def ping(self) -> None: ...


class JobQueue(Protocol):
    """Enqueue is idempotent per key: a duplicate (job, attempt) is dropped, never run twice."""

    async def enqueue_analysis(self, job_id: str, attempt: int, *, defer_s: float | None = None) -> None: ...
    async def enqueue_project_cleanup(self, project_id: str) -> None: ...
    async def enqueue_export(self, export_id: str, *, defer_s: float | None = None) -> None: ...
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


@dataclass(frozen=True)
class PresignedPost:
    """Browser-direct upload: POST `fields` plus the file to `url`. Size and key are signed into the policy."""

    url: str
    fields: dict[str, str]
    key: str
    max_bytes: int
    expires_at: datetime


@dataclass(frozen=True)
class ObjectStat:
    size: int
    content_type: str


@runtime_checkable
class UploadStore(BlobStore, Protocol):
    """A `BlobStore` that can take browser uploads straight to the bucket (ADR 0014)."""

    async def presign_upload(
        self, key: str, *, max_bytes: int, content_type: str, expires_s: int
    ) -> PresignedPost: ...
    async def stat(self, key: str) -> ObjectStat | None: ...
    async def get_range(self, key: str, start: int, length: int) -> bytes: ...
    async def delete(self, key: str) -> None: ...


class CounterStore(Protocol):
    """Atomic named counters (rate-limit windows, per-job LLM usage, provider windows).

    `add` applies every increment in one step. When `limits` is given and any resulting value would exceed its
    limit nothing is written and None is returned (conditional update); otherwise the new values.
    Expiry (`ttl_seconds`) only bounds storage: correctness never depends on it (ADR 0012).
    """

    async def add(
        self,
        key: str,
        amounts: dict[str, int],
        *,
        limits: dict[str, int] | None = None,
        ttl_seconds: int = 0,
    ) -> dict[str, int] | None: ...
    async def get(self, key: str) -> dict[str, int]: ...
    async def delete(self, keys: Sequence[str]) -> int: ...


# A mutator edits the record in place; returning False means "nothing changed, do not write". It may run more
# than once (optimistic retry), so it must be a pure function of the record it is given.
Mutator = Callable[[Any], bool | None]


class ControlStore(Protocol):
    """Sessions, projects, jobs, checkpoints and guide content (ADR 0012). Two adapters: Postgres, DynamoDB."""

    # sessions
    async def create_session(self, token_hash: str, now: datetime, expires_at: datetime) -> SessionRec: ...
    async def session_by_token(self, token_hash: str) -> SessionRec | None: ...
    async def update_session(self, token_hash: str, fn: Mutator) -> SessionRec: ...
    async def expired_sessions(self, now: datetime, limit: int) -> list[SessionRec]: ...
    async def delete_session(self, token_hash: str) -> None: ...
    # projects and jobs
    async def create_project(
        self,
        project: ProjectRec,
        job: JobRec | None,
        *,
        max_active_per_session: int = 0,
        max_queued_global: int = 0,
    ) -> None:
        """Atomic. Raises `RateLimited` when a cap (0 = no cap) is exceeded; nothing is written then."""
        ...

    async def get_project(self, project_id: str) -> ProjectRec | None: ...
    async def update_project(self, project_id: str, fn: Mutator) -> ProjectRec: ...
    async def add_job(
        self, job: JobRec, *, max_active_per_session: int = 0, max_queued_global: int = 0
    ) -> None: ...
    async def projects_of_session(self, session_id: str) -> list[tuple[ProjectRec, JobRec | None]]: ...
    async def expired_projects(self, now: datetime, limit: int) -> list[ProjectRec]:
        """Live projects (no delete marker yet) whose `expires_at` has passed."""
        ...

    async def deleting_projects(self, limit: int) -> list[ProjectRec]:
        """Projects carrying the delete marker: the sweeper finishes their cleanup."""
        ...

    async def delete_project_data(self, project_id: str) -> int:
        """Every record of the project except progress events (the `ProgressStore` owns those). Idempotent."""
        ...

    async def get_job(self, job_id: str) -> JobRec | None: ...
    async def latest_job(self, project_id: str, kind: str | None = None) -> JobRec | None:
        """The newest job of the project, optionally of one `kind` (analysis | regenerate)."""
        ...

    async def jobs_of_project(self, project_id: str) -> list[JobRec]: ...
    async def add_regeneration(
        self, job: JobRec, *, max_active_per_session: int = 0, max_queued_global: int = 0
    ) -> tuple[JobRec, bool]:
        """One chapter regeneration per project at a time. Returns (job, True) when `job` was created and
        (running job, False) when one is already queued or running. Raises `NotFound` for a missing or deleting
        project and `RateLimited` over a cap (same caps as `add_job`)."""
        ...

    async def update_job(self, job_id: str, fn: Mutator) -> JobRec: ...
    async def recoverable_jobs(
        self, *, queued_before: datetime, heartbeat_before: datetime, limit: int
    ) -> list[JobRec]:
        """Jobs `queued` since before `queued_before`, or `running` with a heartbeat before `heartbeat_before`."""
        ...

    async def active_job_counts(self) -> tuple[int, int]:
        """(queued, running) as derived from the job records, to heal drifted counters."""
        ...

    async def checkpoints(self, job_id: str) -> set[str]: ...
    async def put_checkpoint(self, job_id: str, name: str, data: dict[str, Any]) -> None: ...
    # guide content (replace = write the new set, then drop what is not in it)
    async def replace_summaries(self, project_id: str, rows: Sequence[SummaryRec]) -> None: ...
    async def summaries(self, project_id: str, level: str | None = None) -> list[SummaryRec]: ...
    async def replace_plan(
        self, project_id: str, plan: dict[str, Any], planner_version: str, sections: Sequence[SectionRec]
    ) -> None: ...
    async def get_plan(self, project_id: str) -> dict[str, Any] | None: ...
    async def sections(self, project_id: str) -> list[SectionRec]: ...
    async def get_section(self, project_id: str, key: str) -> SectionRec | None: ...
    async def update_section(self, project_id: str, key: str, fn: Mutator) -> SectionRec: ...
    async def replace_diagrams(self, project_id: str, rows: Sequence[DiagramRec]) -> None: ...
    async def diagrams(self, project_id: str, kind: str | None = None) -> list[DiagramRec]: ...
    async def get_diagram(self, project_id: str, diagram_id: str) -> DiagramRec | None: ...
    # chats (messages are written as whole turns; `message_count` and `seq` are the store's)
    async def create_chat(self, chat: ChatRec, *, max_per_project: int) -> None:
        """Atomic. Raises `Conflict` at the per-project cap and `NotFound` for a missing or deleting project."""
        ...

    async def get_chat(self, chat_id: str) -> ChatRec | None: ...
    async def chats_of_project(self, project_id: str) -> list[ChatRec]:
        """Most recently active first."""
        ...

    async def chat_messages(
        self, chat_id: str, *, after_seq: int = 0, last: int | None = None
    ) -> list[ChatMessageRec]:
        """Ascending by seq. `after_seq` keeps seq > it; `last` keeps the final N."""
        ...

    async def append_turn(
        self, chat_id: str, user: ChatMessageRec, assistant: ChatMessageRec, *, title_chars: int
    ) -> int:
        """Write both messages with the next two seq values (one atomic step) and bump the chat's activity time;
        an untitled chat takes the first `title_chars` of the question. Returns the assistant message's seq."""
        ...

    # exports
    async def pending_artifact(
        self, project_id: str, kind: str, chat_id: str | None, since: datetime
    ) -> ArtifactRec | None:
        """A pending artifact of the same kind (and chat) created after `since`."""
        ...

    async def add_artifact(self, rec: ArtifactRec, *, reuse_since: datetime) -> ArtifactRec:
        """Insert, or return the pending one of the same kind and chat created after `reuse_since`. Raises
        `NotFound` for a missing or deleting project."""
        ...

    async def get_artifact(self, artifact_id: str) -> ArtifactRec | None: ...
    async def artifacts_of_project(self, project_id: str) -> list[ArtifactRec]:
        """Newest first."""
        ...

    async def update_artifact(self, artifact_id: str, fn: Mutator) -> ArtifactRec: ...
    async def delete_artifact(self, artifact_id: str) -> None: ...
    async def expired_artifacts(self, now: datetime, limit: int) -> list[ArtifactRec]: ...
    async def stale_pending_artifacts(
        self, *, created_before: datetime, now: datetime, limit: int
    ) -> list[ArtifactRec]:
        """Pending, not yet expired, created before `created_before`, of projects without a delete marker."""
        ...

    async def ping(self) -> None: ...


class ChunkStore(Protocol):
    """Evidence chunks of a project: the store behind every retrieval hit (LLD §4)."""

    async def replace_project_chunks(self, project_id: str, chunks: Sequence[Chunk]) -> int: ...
    async def get_many(self, project_id: str, ids: Sequence[str]) -> list[Chunk]: ...
    async def by_path(self, project_id: str, path: str) -> list[Chunk]: ...
    async def for_symbols(self, project_id: str, symbol_ids: Sequence[str]) -> list[Chunk]: ...
    async def all_ids(self, project_id: str) -> list[str]: ...
    async def list_project(self, project_id: str) -> list[Chunk]: ...
    async def set_subsystems(self, project_id: str, mapping: dict[str, str]) -> int: ...
