"""Offline evaluation corpus: fixture repositories analysed in-process, no datastore and no network.

The same production code path as the pipeline (scan -> analyse -> IR -> chunks -> BM25), with the two storage
seams replaced by in-memory implementations of their contracts (`ChunkRepository`, `BlobStore`). The retriever that
results is a real `HybridRetriever` (lexical + structural + RRF + sufficiency); dense, graph and rerank are absent,
which retrieval already treats as ordinary degradation.

Evaluation tooling only: nothing under `api`, `worker` or the pipeline imports this package.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from unfold import ANALYSIS_VERSION
from unfold.core.errors import NotFound
from unfold.indexing.chunking import chunk_project
from unfold.indexing.lexical import BM25Index, LexicalArtifact, LexicalIndexCache
from unfold.indexing.models import Chunk
from unfold.indexing.store import ChunkRepository
from unfold.project_ir.builder import build_ir
from unfold.project_ir.schema import ProjectIdentity, ProjectIR
from unfold.repo_intel.service import analyze_repository
from unfold.retrieval.hybrid import HybridRetriever

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURES = REPO_ROOT / "evals" / "fixtures" / "repos"
GOLD = REPO_ROOT / "evals" / "gold"
BASELINE = REPO_ROOT / "evals" / "baseline.json"
MAX_FILE_BYTES = 1024 * 1024


class MemoryChunks(ChunkRepository):
    """`ChunkRepository` over a list (the read methods retrieval uses)."""

    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self._chunks = list(chunks)
        self._by_id = {c.id: c for c in self._chunks}

    async def get_many(self, project_id: str, ids: Sequence[str]) -> list[Chunk]:
        return [self._by_id[i] for i in dict.fromkeys(ids) if i in self._by_id]

    async def by_path(self, project_id: str, path: str) -> list[Chunk]:
        return sorted(
            (c for c in self._chunks if c.path == path), key=lambda c: (c.start_line, c.end_line, c.id)
        )

    async def for_symbols(self, project_id: str, symbol_ids: Sequence[str]) -> list[Chunk]:
        wanted = set(symbol_ids)
        return sorted(
            (c for c in self._chunks if c.symbol_id in wanted), key=lambda c: (c.path, c.start_line, c.id)
        )

    async def all_ids(self, project_id: str) -> list[str]:
        return [c.id for c in self._chunks]

    async def list_project(self, project_id: str) -> list[Chunk]:
        return list(self._chunks)


class MemoryBlobs:
    """`BlobStore` in a dict. Only what the lexical cache and the IR snapshot read paths can ask for."""

    bucket = "memory"

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    async def ensure_bucket(self) -> None:
        return None

    async def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._data[key] = data

    async def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> None:
        self._data[key] = await asyncio.to_thread(path.read_bytes)

    async def get_bytes(self, key: str) -> bytes:
        try:
            return self._data[key]
        except KeyError as exc:
            raise NotFound("Object not found.") from exc

    async def get_file(self, key: str, path: Path) -> Path:
        await asyncio.to_thread(path.write_bytes, await self.get_bytes(key))
        return path

    async def delete_prefix(self, prefix: str) -> int:
        doomed = [k for k in self._data if k.startswith(prefix)]
        for k in doomed:
            del self._data[k]
        return len(doomed)

    async def presigned_get_url(self, key: str, expires_s: int, *, filename: str) -> str:
        raise NotImplementedError("the in-memory store has no URLs")

    async def ping(self) -> None:
        return None


@dataclass
class FixtureProject:
    name: str
    project_id: str
    ir: ProjectIR
    chunks: list[Chunk]
    texts: dict[str, str]
    lexical: LexicalArtifact

    def retriever(self) -> HybridRetriever:
        cache = LexicalIndexCache()
        key = f"memory/{self.project_id}/lexical"
        cache.put(key, self.lexical)
        return HybridRetriever(
            project_id=self.project_id,
            chunks=MemoryChunks(self.chunks),
            lexical_cache=cache,
            objects=MemoryBlobs(),
            lexical_key=key,
            dense=None,
            reranker=None,
            graph=None,
            ir=self.ir,
        )

    def chunk_ids_for(self, path: str) -> list[str]:
        return [c.id for c in self.chunks if c.path == path]


def load_fixture(name: str, root: Path | None = None) -> FixtureProject:
    """Analyse `evals/fixtures/repos/<name>` (parse only, never executed)."""
    base = (root or FIXTURES) / name
    pid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"unfold-eval/{name}"))
    analyzed = analyze_repository(pid, base, max_single_file_bytes=MAX_FILE_BYTES)
    identity = ProjectIdentity(
        project_id=pid,
        name=name,
        input_type="zip",
        source_locator=f"{name}.zip",
        commit_sha=None,
        default_branch=None,
        description=analyzed.analysis.description,
    )
    ir = build_ir(identity, analyzed.inventory, analyzed.analysis, analysis_version=ANALYSIS_VERSION)
    chunks = chunk_project(ir, analyzed.texts)
    return FixtureProject(
        name=name,
        project_id=pid,
        ir=ir,
        chunks=chunks,
        texts=analyzed.texts,
        lexical=BM25Index.build(chunks),
    )
