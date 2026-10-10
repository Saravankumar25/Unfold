"""Local model runtime: one load per process, length-grouped batches, bounded ONNX threads (retrieval latency).

The fastembed classes are replaced by recording doubles; the code under test (`Embedder`, `CrossEncoderReranker`,
`embedder_for` / `reranker_for`) is the real production code.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

import pytest

from unfold.core.config import Settings
from unfold.indexing.embeddings import Embedder, embedder_for, length_order
from unfold.indexing.reranker import CrossEncoderReranker, reranker_for

LOADS: list[tuple[str, int | None]] = []
SEEN: dict[str, list[list[int]]] = {"embed": [], "rerank": []}


class RecordingEmbedding:
    embedding_size = 3

    def __init__(self, model_name: str, cache_dir: str | None = None, threads: int | None = None) -> None:
        LOADS.append(("embed", threads))

    def passage_embed(self, texts: list[str], batch_size: int = 64) -> Iterable[list[float]]:
        SEEN["embed"].append([len(t) for t in texts])
        for t in texts:
            yield [float(len(t)), 0.0, 1.0]

    def query_embed(self, texts: list[str]) -> Iterable[list[float]]:
        for t in texts:
            yield [float(len(t)), 1.0, 0.0]


class RecordingCrossEncoder:
    def __init__(self, model_name: str, cache_dir: str | None = None, threads: int | None = None) -> None:
        LOADS.append(("rerank", threads))

    def rerank(self, query: str, docs: list[str], batch_size: int = 16) -> Iterable[float]:
        SEEN["rerank"].append([len(d) for d in docs])
        for d in docs:
            yield float(len(d))


@pytest.fixture(autouse=True)
def _doubles(monkeypatch: pytest.MonkeyPatch) -> None:
    LOADS.clear()
    for v in SEEN.values():
        v.clear()
    monkeypatch.setattr("fastembed.TextEmbedding", RecordingEmbedding)
    monkeypatch.setattr("fastembed.rerank.cross_encoder.TextCrossEncoder", RecordingCrossEncoder)


TEXTS = ["x" * 900, "a", "y" * 300, "bb", "z" * 2000, "c" * 40]


def test_length_order_groups_similar_lengths() -> None:
    order = length_order(TEXTS)
    assert [len(TEXTS[i]) for i in order] == sorted(len(t) for t in TEXTS)
    assert sorted(order) == list(range(len(TEXTS)))


async def test_embedder_batches_by_length_and_returns_input_order() -> None:
    emb = Embedder("m", None, threads=6)
    arr = await emb.embed_documents_array(TEXTS)
    assert [int(v) for v in arr[:, 0]] == [len(t) for t in TEXTS]  # row i belongs to text i
    assert SEEN["embed"] == [sorted(len(t) for t in TEXTS)]  # the model saw short texts together


async def test_reranker_batches_by_length_and_returns_input_order() -> None:
    rr = CrossEncoderReranker("m", None, threads=6)
    scores = await rr.rerank("q", TEXTS)
    assert scores == [float(len(t)) for t in TEXTS]
    assert SEEN["rerank"] == [sorted(len(t) for t in TEXTS)]


async def test_each_model_loads_once_under_concurrent_use_and_gets_the_thread_cap() -> None:
    emb, rr = Embedder("m", None, threads=6), CrossEncoderReranker("m", None, threads=6)
    await asyncio.gather(
        *(emb.embed_query("q") for _ in range(8)),
        *(emb.embed_documents(["a", "bb"]) for _ in range(4)),
        *(rr.rerank("q", ["a", "bb", "ccc"]) for _ in range(8)),
    )
    assert sorted(LOADS) == [("embed", 6), ("rerank", 6)]


def test_runtime_singletons_are_shared_across_retrievers_and_use_settings_threads() -> None:
    rt: Any = SimpleNamespace(extras={}, settings=Settings(model_threads=3))
    first = (embedder_for(rt), reranker_for(rt))
    second = (embedder_for(rt), reranker_for(rt))
    assert first[0] is second[0] and first[1] is second[1]
    assert first[0].threads == 3 and first[1].threads == 3
    assert Settings(model_threads=0).model_threads == 0  # 0 keeps the onnxruntime default (threads=None)
    assert Embedder.from_settings(Settings(model_threads=0)).threads is None


async def test_empty_inputs_do_not_load_a_model() -> None:
    assert await CrossEncoderReranker("m").rerank("q", []) == []
    assert LOADS == []
