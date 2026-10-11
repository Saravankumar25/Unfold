"""Real local models: the production rerank path keeps the scores and is not slower than the naive one.

Needs only the fastembed model cache (`.model-cache`), no data store. The bound is a ratio measured inside one
process against the same ONNX session, best of interleaved repeats, so machine speed and neighbours cancel out.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from unfold.core.config import Settings
from unfold.evaluation.corpus import load_fixture
from unfold.indexing.reranker import CrossEncoderReranker

pytestmark = pytest.mark.integration

MAX_RATIO = 1.25  # expected ~0.5-0.75; the bound only has to catch a regression back to unordered batches
REPEATS = 7  # min of more interleaved repeats: a loaded machine only ever adds time


@pytest.fixture(scope="module")
def reranker() -> CrossEncoderReranker:
    s = Settings()
    if not (Path(s.model_cache_dir) / "models--Xenova--ms-marco-MiniLM-L-6-v2").exists():
        pytest.skip("reranker model is not in the local cache")
    rr = CrossEncoderReranker.from_settings(s)
    rr._load()
    return rr


def _naive(rr: CrossEncoderReranker, query: str, texts: list[str]) -> list[float]:
    return [float(x) for x in rr._load().rerank(query, texts, batch_size=rr.batch_size)]


def test_retrieval_rerank_keeps_scores_and_is_not_slower(reranker: CrossEncoderReranker) -> None:
    chunks = load_fixture("fastapi_rag").chunks
    texts = [c.text[:2000] for c in sorted(chunks, key=lambda c: c.id)][:30]
    assert len({len(t) for t in texts}) > 5  # mixed lengths, the case ordering helps
    query = "how are documents chunked and embedded into the vector store"
    expected = _naive(reranker, query, texts)
    assert reranker._rerank_sync(query, texts) == pytest.approx(expected, abs=1e-3)
    assert reranker._load() is reranker._load()  # one session for the process

    naive: list[float] = []
    ordered: list[float] = []
    for _ in range(REPEATS):
        t = time.perf_counter()
        _naive(reranker, query, texts)
        naive.append(time.perf_counter() - t)
        t = time.perf_counter()
        reranker._rerank_sync(query, texts)
        ordered.append(time.perf_counter() - t)
    assert min(ordered) <= MAX_RATIO * min(naive), (min(ordered), min(naive))


def test_retrieval_document_embeddings_do_not_depend_on_batch_order() -> None:
    import numpy as np

    from unfold.indexing.embeddings import Embedder

    s = Settings()
    if not (Path(s.model_cache_dir) / "models--qdrant--bge-small-en-v1.5-onnx-q").exists():
        pytest.skip("embedding model is not in the local cache")
    texts = [c.text for c in load_fixture("fastapi_rag").chunks][:24]
    emb = Embedder.from_settings(s)
    got = emb._embed_sync(texts, False)
    expected = np.asarray(list(emb._load().passage_embed(texts, batch_size=emb.batch_size)), dtype=np.float32)
    cos = (got * expected).sum(1) / (np.linalg.norm(got, axis=1) * np.linalg.norm(expected, axis=1))
    assert float(cos.min()) > 0.9999
