"""Local dense embeddings via fastembed (ONNX, CPU) — LLD §10, ESD §19, HLD D4.

The model is loaded lazily and exactly once per process (thread-safe); inference runs in a worker thread so the
event loop is never blocked. Load/inference failures raise `ModelUnavailable` — never fake vectors.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from unfold.core.config import Settings
from unfold.core.logging import get_logger
from unfold.indexing.errors import ModelUnavailable

if TYPE_CHECKING:
    from unfold.runtime import Runtime

log = get_logger(__name__)

EMBED_BATCH = 64


def length_order(texts: Sequence[str]) -> list[int]:
    """Indices that put texts of similar length in the same batch: ONNX pads each batch to its longest text, so
    one long text among short ones multiplies the work (scores are unaffected, padding is masked)."""
    return sorted(range(len(texts)), key=lambda i: len(texts[i]))


class Embedder:
    def __init__(
        self,
        model_name: str,
        cache_dir: str | None = None,
        *,
        batch_size: int = EMBED_BATCH,
        threads: int | None = None,
    ) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.batch_size = batch_size
        self.threads = threads
        self._model: Any = None
        self._dimension: int | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> Embedder:
        return cls(settings.embedding_model, settings.model_cache_dir, threads=settings.model_threads or None)

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                try:
                    from fastembed import TextEmbedding

                    self._model = TextEmbedding(
                        model_name=self.model_name, cache_dir=self.cache_dir, threads=self.threads
                    )
                    self._dimension = int(self._model.embedding_size)
                except Exception as exc:
                    raise ModelUnavailable(
                        f"Embedding model {self.model_name!r} could not be loaded: {type(exc).__name__}",
                        detail={"model": self.model_name},
                    ) from exc
                log.info("embedder.loaded", model=self.model_name, dimension=self._dimension)
        return self._model

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            try:
                from fastembed import TextEmbedding

                self._dimension = int(TextEmbedding.get_embedding_size(self.model_name))
            except Exception:
                self._load()
        assert self._dimension is not None
        return self._dimension

    def _embed_sync(self, texts: Sequence[str], query: bool) -> npt.NDArray[np.float32]:
        model = self._load()
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        try:
            if query:
                vectors = list(model.query_embed(list(texts)))
            else:
                order = length_order(texts)
                got = list(model.passage_embed([texts[i] for i in order], batch_size=self.batch_size))
                vectors = [v for _, v in sorted(zip(order, got, strict=True), key=lambda p: p[0])]
        except Exception as exc:
            raise ModelUnavailable(
                f"Embedding inference failed: {type(exc).__name__}", detail={"model": self.model_name}
            ) from exc
        return np.asarray(vectors, dtype=np.float32).reshape(len(texts), -1)

    async def embed_documents_array(self, texts: Sequence[str]) -> npt.NDArray[np.float32]:
        return await asyncio.to_thread(self._embed_sync, list(texts), False)

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        arr = await self.embed_documents_array(texts)
        return [row.tolist() for row in arr]

    async def embed_query(self, text: str) -> list[float]:
        arr = await asyncio.to_thread(self._embed_sync, [text], True)
        return [float(x) for x in arr[0]]


def embedder_for(runtime: Runtime) -> Embedder:
    """Process-wide embedder singleton kept in `Runtime.extras`."""
    emb = runtime.extras.get("embedder")
    if not isinstance(emb, Embedder):
        emb = Embedder.from_settings(runtime.settings)
        runtime.extras["embedder"] = emb
    return emb
