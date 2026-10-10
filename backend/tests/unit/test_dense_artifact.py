"""`DenseArtifact` / `ArtifactDenseStore` behaviour with synthetic vectors (parity with Qdrant is an integration test)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from unfold.core.errors import NotFound
from unfold.indexing.dense_artifact import (
    ArtifactDenseStore,
    DenseArtifact,
    DenseArtifactCache,
    dense_key,
)
from unfold.indexing.errors import ModelUnavailable
from unfold.indexing.models import Chunk, ContentType

PID = "11111111-1111-1111-1111-111111111111"


def _vec(text: str, dim: int = 16) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")
    return np.random.default_rng(seed).normal(size=dim).astype(np.float32)


def _chunk(i: int, *, ct: ContentType = ContentType.SOURCE, sub: str | None = None) -> Chunk:
    return Chunk(
        id=f"c{i}",
        project_id=PID,
        path=f"f{i}.py",
        start_line=1,
        end_line=2,
        kind="function",
        content_type=ct,
        subsystem_id=sub,
        text=f"chunk {i}",
        content_hash=f"h{i}",
    )


class _Blobs:
    bucket = "t"

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def put_bytes(self, key: str, data: bytes, content_type: str = "") -> None:
        self.data[key] = data

    async def get_bytes(self, key: str) -> bytes:
        if key not in self.data:
            raise NotFound("missing")
        return self.data[key]

    async def delete_prefix(self, prefix: str) -> int:
        gone = [k for k in self.data if k.startswith(prefix)]
        for k in gone:
            del self.data[k]
        return len(gone)

    async def ensure_bucket(self) -> None: ...
    async def put_file(self, key: str, path: Path, content_type: str = "") -> None: ...
    async def get_file(self, key: str, path: Path) -> Path:
        return path

    async def ping(self) -> None: ...


class _Embedder:
    model_name = "BAAI/bge-small-en-v1.5"
    dimension = 16

    async def embed_documents_array(self, texts: Sequence[str]) -> np.ndarray:
        return np.stack([_vec(t) for t in texts])

    async def embed_query(self, text: str) -> list[float]:
        return [float(x) for x in _vec(text)]


def _store(blobs: _Blobs, cache: DenseArtifactCache | None = None) -> ArtifactDenseStore:
    return ArtifactDenseStore(blobs, _Embedder(), cache or DenseArtifactCache())  # type: ignore[arg-type]


def test_key_is_namespaced_by_project_model_and_version() -> None:
    assert (
        dense_key(PID, "BAAI/bge-small-en-v1.5", "v1")
        == f"projects/{PID}/dense/baai-bge-small-en-v1-5-v1.npz"
    )


def test_roundtrip_and_exact_cosine_order_with_filters() -> None:
    chunks = [
        _chunk(0, sub="s1"),
        _chunk(1, ct=ContentType.DOCS, sub="s1"),
        _chunk(2, sub="s2"),
        _chunk(3),
    ]
    vectors = np.stack([_vec(c.text) for c in chunks])
    art = DenseArtifact.from_bytes(DenseArtifact.from_chunks(chunks, vectors).to_bytes())
    q = _vec("chunk 2")
    ranked = art.search(q, k=10)
    assert ranked[0][0] == "c2" and ranked[0][1] == pytest.approx(1.0, abs=1e-5)
    expected = sorted(
        range(4),
        key=lambda i: -float(vectors[i] @ q / (np.linalg.norm(vectors[i]) * np.linalg.norm(q))),
    )
    assert [cid for cid, _ in ranked] == [f"c{i}" for i in expected]
    assert [c for c, _ in art.search(q, 10, content_types=["docs"])] == ["c1"]
    assert {c for c, _ in art.search(q, 10, subsystem_ids=["s1", "s2"])} == {"c0", "c1", "c2"}
    assert art.search(q, 10, subsystem_ids=[""]) == []  # chunks without a subsystem never match a filter
    assert len(art.search(q, 2)) == 2
    assert art.search(np.zeros(16), 5) == []


def test_corrupt_artifact_is_a_typed_error() -> None:
    with pytest.raises(ModelUnavailable):
        DenseArtifact.from_bytes(b"not an npz")


async def test_store_upsert_search_set_subsystems_delete() -> None:
    blobs, cache = _Blobs(), DenseArtifactCache()
    store = _store(blobs, cache)
    assert await store.search(PID, "chunk 1") == []  # nothing indexed yet
    chunks = [_chunk(i) for i in range(5)]
    assert await store.upsert(chunks) == 5
    assert list(blobs.data) == [dense_key(PID, _Embedder.model_name)]
    assert (await store.search(PID, "chunk 3", k=3))[0][0] == "c3"

    assert await store.set_subsystems(PID, {"c1": "sx", "c4": "sx"}) == 2
    assert {c for c, _ in await store.search(PID, "chunk 0", k=10, subsystem_ids=["sx"])} == {"c1", "c4"}
    # a cold process (empty cache) reads the persisted artifact, including the subsystem update
    cold = _store(blobs)
    assert {c for c, _ in await cold.search(PID, "chunk 0", k=10, subsystem_ids=["sx"])} == {"c1", "c4"}

    await store.delete_project(PID)
    assert blobs.data == {} and await store.search(PID, "chunk 3") == []


def test_truncated_and_empty_artifacts_are_typed_errors() -> None:
    good = DenseArtifact.from_chunks([_chunk(0), _chunk(1)], np.stack([_vec("a"), _vec("b")])).to_bytes()
    for bad in (b"", good[: len(good) // 2], good[:10]):
        with pytest.raises(ModelUnavailable):
            DenseArtifact.from_bytes(bad)


def test_ties_are_ordered_by_insertion_not_by_the_selection_algorithm() -> None:
    same = np.tile(_vec("dup"), (6, 1))
    art = DenseArtifact.from_chunks([_chunk(i) for i in range(6)], same)
    assert [c for c, _ in art.search(_vec("dup"), k=3)] == ["c0", "c1", "c2"]


async def test_cache_entries_expire_so_a_rewrite_is_seen() -> None:
    blobs = _Blobs()
    store = _store(blobs, DenseArtifactCache(ttl_seconds=0.0))
    await store.upsert([_chunk(0)])
    assert [c for c, _ in await store.search(PID, "chunk 0")] == ["c0"]
    blobs.data[dense_key(PID, _Embedder.model_name)] = DenseArtifact.from_chunks(
        [_chunk(9)], np.stack([_vec("chunk 9")])
    ).to_bytes()  # rewritten by another process
    assert [c for c, _ in await store.search(PID, "chunk 9")] == ["c9"]


def test_backend_setting_selects_the_store() -> None:
    from types import SimpleNamespace

    from unfold.indexing.dense import dense_store_for
    from unfold.indexing.dense_qdrant import DenseIndex

    qdrant = SimpleNamespace(collection="c")
    cfg = SimpleNamespace(
        settings=SimpleNamespace(dense_backend="artifact", embedding_model="m", model_cache_dir="."),
        extras={},
        objects=_Blobs(),
        qdrant=qdrant,
        redis=SimpleNamespace(),
    )
    assert isinstance(dense_store_for(cfg), ArtifactDenseStore)  # type: ignore[arg-type]
    cfg.settings.dense_backend = "qdrant"
    assert isinstance(dense_store_for(cfg), DenseIndex)  # type: ignore[arg-type]
