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
from unfold.api.routes import projects, sessions
from unfold.api.streams import StreamCap
from unfold.core.config import Settings, get_settings
from unfold.core.logging import configure_logging, get_logger
from unfold.observability.tracing import configure_tracing
from unfold.runtime import Runtime
from unfold.worker.queue import ArqJobQueue

log = get_logger(__name__)
API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    configure_tracing(settings, "api")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = Runtime.create(settings)
        app.state.runtime = rt
        app.state.queue = await ArqJobQueue.connect(settings)
        app.state.streams = StreamCap(settings.max_streams_per_session, settings.max_streams_total)
        await rt.objects.ensure_bucket()
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
    for module in (sessions, projects):
        app.include_router(module.router, prefix=API_PREFIX)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["ops"])
    async def ready() -> JSONResponse:
        rt: Runtime = app.state.runtime
        checks: dict[str, Any] = {
            "postgres": rt.db.ping(),
            "redis": rt.limiter.ping(),
            "neo4j": rt.neo4j.ping(),
            "qdrant": rt.qdrant.ping(),
            "object_store": rt.objects.ping(),
        }
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
