"""FastAPI composition root (HLD §2 `api`). Builds one Runtime per process; never runs analysis inline."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from unfold import __version__
from unfold.api.errors import install_error_handling
from unfold.api.routes import guide, projects, sessions
from unfold.core.config import Settings, get_settings
from unfold.core.logging import configure_logging, get_logger
from unfold.observability.tracing import configure_tracing
from unfold.ports import JobQueue
from unfold.runtime import Runtime
from unfold.worker.factory import build_job_queue

log = get_logger(__name__)
API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None, *, queue: JobQueue | None = None) -> FastAPI:
    """Build the API. `queue` lets a caller supply the job queue (tests, another launcher); default: subprocess."""
    settings = settings or get_settings()
    if (
        settings.is_production
        and settings.persistence_backend == "dynamodb"
        and settings.proxy_hmac_secret is None
    ):
        # no address-based proxy trust behind a Function URL: the signed header is the client identity (ADR 0016)
        raise ValueError(
            "Unsafe production configuration: PROXY_HMAC_SECRET is required with PERSISTENCE_BACKEND=dynamodb"
        )
    configure_logging(settings)
    configure_tracing(settings, "api")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = Runtime.create(settings)
        app.state.runtime = rt
        app.state.queue = queue if queue is not None else await build_job_queue(settings)
        if settings.persistence_backend == "postgres":
            # Local stack only: nothing else schedules recovery. On AWS the scheduled sweeper does, and a
            # cold-starting Lambda must not scan the table (ADR 0011).
            from unfold.worker.recovery import requeue_incomplete_jobs

            await rt.objects.ensure_bucket()
            await requeue_incomplete_jobs(rt, app.state.queue)
        log.info("api.started", version=__version__, env=settings.app_env)
        try:
            yield
        finally:
            await app.state.queue.aclose()
            await rt.aclose()

    app = FastAPI(
        title="Unfold API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/api/docs",
        openapi_url=None if settings.is_production else "/api/openapi.json",
    )
    install_error_handling(app)
    for module in (sessions, projects, guide):
        app.include_router(module.router, prefix=API_PREFIX)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["ops"])
    async def ready() -> JSONResponse:
        rt: Runtime = app.state.runtime
        checks: dict[str, Any] = {
            "control": rt.control.ping(),
            "limiter": rt.limiter.ping(),
            "object_store": rt.objects.ping(),
        }
        if settings.graph_backend == "neo4j":
            checks["neo4j"] = rt.neo4j.ping()
        if settings.dense_backend == "qdrant":
            checks["qdrant"] = rt.qdrant.ping()
        results = await asyncio.gather(
            *(asyncio.wait_for(c, 5) for c in checks.values()), return_exceptions=True
        )
        status = {
            name: ("ok" if not isinstance(r, BaseException) else type(r).__name__)
            for name, r in zip(checks, results, strict=True)
        }
        ok = all(v == "ok" for v in status.values())
        return JSONResponse(
            {"status": "ok" if ok else "degraded", "checks": status}, status_code=200 if ok else 503
        )

    if not settings.is_production:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.public_web_origin],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    install_proxy_trust(app, settings)  # last, so it runs first
    return app


def install_proxy_trust(app: FastAPI, settings: Settings) -> None:
    """Only trusted proxy peers may set the client address via X-Forwarded-For (audit S-2)."""
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.trusted_proxies)  # type: ignore[arg-type]


def run() -> None:
    import uvicorn

    uvicorn.run(
        "unfold.api.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container entrypoint binds all interfaces
        port=8000,
        proxy_headers=False,  # create_app installs ProxyHeadersMiddleware with TRUSTED_PROXY_IPS
    )
