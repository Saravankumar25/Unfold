"""Error envelope + request-id middleware (ESD §25). Every error response has the same JSON shape."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from unfold.core.errors import ErrorCode, UnfoldError
from unfold.core.logging import bind_context, clear_context, get_logger

log = get_logger(__name__)
REQUEST_ID_HEADER = "X-Request-ID"


def _envelope(code: str, message: str, retryable: bool, request_id: str | None) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "retryable": retryable, "request_id": request_id}}


def _rid(request: Request) -> str | None:
    rid: str | None = getattr(request.state, "request_id", None)
    return rid


def install_error_handling(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        rid = (
            incoming if 8 <= len(incoming) <= 64 and incoming.replace("-", "").isalnum() else uuid.uuid4().hex
        )
        request.state.request_id = rid
        clear_context()
        bind_context(request_id=rid)
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = rid
        return response

    @app.exception_handler(UnfoldError)
    async def unfold_error(request: Request, exc: UnfoldError) -> JSONResponse:
        if exc.http_status >= 500:
            log.error("request.failed", code=exc.code.value, error=exc.message, path=request.url.path)
        headers = {"Retry-After": "30"} if exc.http_status in (429, 503) and exc.retryable else None
        return JSONResponse(exc.envelope(_rid(request)), status_code=exc.http_status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        msg = (
            f"{where}: {first.get('msg', 'invalid input')}"
            if where
            else str(first.get("msg", "invalid input"))
        )
        return JSONResponse(
            _envelope(ErrorCode.VALIDATION_ERROR.value, msg, False, _rid(request)), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.VALIDATION_ERROR
        if exc.status_code == 405:
            code = ErrorCode.VALIDATION_ERROR
        return JSONResponse(
            _envelope(code.value, str(exc.detail), False, _rid(request)), status_code=exc.status_code
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("request.unhandled", path=request.url.path, error_type=type(exc).__name__)
        return JSONResponse(
            _envelope(ErrorCode.INTERNAL_ERROR.value, "Internal error.", True, _rid(request)), status_code=500
        )
