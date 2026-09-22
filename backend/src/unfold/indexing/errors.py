"""Typed indexing errors. Callers (retrieval) degrade on these; nothing here ever fabricates scores/vectors."""

from __future__ import annotations

from unfold.core.errors import ErrorCode, StoreUnavailable


class ModelUnavailable(StoreUnavailable):
    """A local model (embedder / cross-encoder) could not be loaded or failed at inference time."""

    code = ErrorCode.STORE_UNAVAILABLE
    retryable = True


class LexicalIndexError(StoreUnavailable):
    """A BM25 artifact could not be built, serialized or loaded."""

    code = ErrorCode.STORE_UNAVAILABLE
    retryable = True
