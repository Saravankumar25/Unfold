"""Dense store contract and selection (LLD §10). Backends: `indexing.dense_qdrant` (Qdrant, the local default and
parity oracle) and `indexing.dense_artifact` (one S3 object per project, ADR 0013). Importing this module loads no
store client, so the AWS images do not need the local-stack packages."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from unfold.indexing.models import Chunk

if TYPE_CHECKING:
    from unfold.runtime import Runtime

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
    from unfold.indexing.dense_qdrant import DenseIndex
    from unfold.indexing.embeddings import embedder_for

    return DenseIndex(rt.qdrant, embedder_for(rt), rt.redis)
