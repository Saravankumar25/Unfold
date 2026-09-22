"""Typed retrieval errors (ESD §25)."""

from __future__ import annotations

from unfold.core.errors import ErrorCode, UnfoldError


class RetrievalFailed(UnfoldError):
    """Every first-stage retriever failed — there is no honest evidence to return."""

    code = ErrorCode.RETRIEVAL_FAILED
    http_status = 503
    retryable = True
