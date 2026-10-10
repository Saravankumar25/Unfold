"""Unified error model (ESD §25). Every failure that reaches a user carries an ErrorCode."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INVALID_GITHUB_URL = "INVALID_GITHUB_URL"
    REPOSITORY_NOT_PUBLIC = "REPOSITORY_NOT_PUBLIC"
    REPOSITORY_UNAVAILABLE = "REPOSITORY_UNAVAILABLE"
    ZIP_INVALID = "ZIP_INVALID"
    ZIP_BOMB_DETECTED = "ZIP_BOMB_DETECTED"
    UNSAFE_ARCHIVE = "UNSAFE_ARCHIVE"
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"
    UNSUPPORTED_ARCHIVE = "UNSUPPORTED_ARCHIVE"
    EMPTY_REPOSITORY = "EMPTY_REPOSITORY"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    LLM_BUDGET_EXHAUSTED = "LLM_BUDGET_EXHAUSTED"
    INVALID_MODEL_OUTPUT = "INVALID_MODEL_OUTPUT"
    ANALYSIS_STAGE_FAILED = "ANALYSIS_STAGE_FAILED"
    RETRIEVAL_FAILED = "RETRIEVAL_FAILED"
    GENERATION_FAILED = "GENERATION_FAILED"
    EXPORT_FAILED = "EXPORT_FAILED"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
    QUEUE_UNAVAILABLE = "QUEUE_UNAVAILABLE"
    SESSION_REQUIRED = "SESSION_REQUIRED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    ARTIFACT_EXPIRED = "ARTIFACT_EXPIRED"
    ARTIFACT_NOT_READY = "ARTIFACT_NOT_READY"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    PROJECT_NOT_READY = "PROJECT_NOT_READY"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class UnfoldError(Exception):
    """Base error. `http_status` is used by the API; `failure_stage` by the job runner."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status: int = 500
    retryable: bool = False
    failure_stage: str = "FAILED_ANALYSIS"

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        retryable: bool | None = None,
        detail: dict[str, Any] | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        if http_status is not None:
            self.http_status = http_status
        self.detail = detail or {}

    def envelope(self, request_id: str | None) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "retryable": self.retryable,
                "request_id": request_id,
            }
        }


class InputError(UnfoldError):
    code = ErrorCode.VALIDATION_ERROR
    http_status = 400
    failure_stage = "FAILED_INPUT"


class SecurityRejection(UnfoldError):
    code = ErrorCode.UNSAFE_ARCHIVE
    http_status = 400
    failure_stage = "FAILED_SECURITY"


class IngestionError(UnfoldError):
    code = ErrorCode.REPOSITORY_UNAVAILABLE
    http_status = 502
    retryable = True
    failure_stage = "FAILED_INGESTION"


class ProviderCapacityError(UnfoldError):
    code = ErrorCode.PROVIDER_RATE_LIMITED
    http_status = 503
    retryable = True
    failure_stage = "FAILED_PROVIDER_CAPACITY"


class ProviderError(UnfoldError):
    code = ErrorCode.PROVIDER_UNAVAILABLE
    http_status = 502
    retryable = True
    failure_stage = "FAILED_PROVIDER_CAPACITY"


class GenerationError(UnfoldError):
    code = ErrorCode.GENERATION_FAILED
    http_status = 500
    failure_stage = "FAILED_GENERATION"


class ExportError(UnfoldError):
    code = ErrorCode.EXPORT_FAILED
    http_status = 500
    retryable = True
    failure_stage = "FAILED_EXPORT"


class StoreUnavailable(UnfoldError):
    code = ErrorCode.STORE_UNAVAILABLE
    http_status = 503
    retryable = True


class NotFound(UnfoldError):
    code = ErrorCode.NOT_FOUND
    http_status = 404


class SessionError(UnfoldError):
    code = ErrorCode.SESSION_REQUIRED
    http_status = 401


class Conflict(UnfoldError):
    code = ErrorCode.CONFLICT
    http_status = 409


class ProjectNotReady(UnfoldError):
    code = ErrorCode.PROJECT_NOT_READY
    http_status = 409
    retryable = True


class RateLimited(UnfoldError):
    code = ErrorCode.RATE_LIMITED
    http_status = 429
    retryable = True
