"""Local cross-encoder reranker via fastembed `TextCrossEncoder` (LLD §11, AI infra §16).

Lazy, thread-safe load; batched inference in a worker thread. Any failure raises `ModelUnavailable` so retrieval
can fall back to fused order and record `degraded=['reranker']` — scores are never fabricated.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from unfold.core.config import Settings
from unfold.core.logging import get_logger
from unfold.indexing.errors import ModelUnavailable

if TYPE_CHECKING:
    from unfold.runtime import Runtime

log = get_logger(__name__)

RERANK_BATCH = 16


class CrossEncoderReranker:
    def __init__(
        self, model_name: str, cache_dir: str | None = None, *, batch_size: int = RERANK_BATCH
    ) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.batch_size = batch_size
        self._model: Any = None
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> CrossEncoderReranker:
        return cls(settings.reranker_model, settings.model_cache_dir)

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                try:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder

                    self._model = TextCrossEncoder(model_name=self.model_name, cache_dir=self.cache_dir)
                except Exception as exc:
                    raise ModelUnavailable(
                        f"Reranker model {self.model_name!r} could not be loaded: {type(exc).__name__}",
                        detail={"model": self.model_name},
                    ) from exc
                log.info("reranker.loaded", model=self.model_name)
        return self._model

    def _rerank_sync(self, query: str, texts: list[str]) -> list[float]:
        model = self._load()
        try:
            scores = [float(s) for s in model.rerank(query, texts, batch_size=self.batch_size)]
        except Exception as exc:
            raise ModelUnavailable(
                f"Reranker inference failed: {type(exc).__name__}", detail={"model": self.model_name}
            ) from exc
        if len(scores) != len(texts):
            raise ModelUnavailable(
                "Reranker returned a score count that does not match the input.",
                detail={"model": self.model_name},
            )
        return scores

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        """Relevance logits aligned with `texts` (higher = more relevant)."""
        if not texts:
            return []
        return await asyncio.to_thread(self._rerank_sync, query, list(texts))


def reranker_for(runtime: Runtime) -> CrossEncoderReranker:
    """Process-wide reranker singleton kept in `Runtime.extras`."""
    rr = runtime.extras.get("reranker")
    if not isinstance(rr, CrossEncoderReranker):
        rr = CrossEncoderReranker.from_settings(runtime.settings)
        runtime.extras["reranker"] = rr
    return rr
