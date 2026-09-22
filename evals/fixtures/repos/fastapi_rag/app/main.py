"""FastAPI application entrypoint: creates the app, wires routers and lifecycle hooks."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import engine
from app.services.vector_store import VectorStore


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the Qdrant collection on startup and dispose the DB engine on shutdown."""
    settings = get_settings()
    configure_logging(settings.log_level)
    store = VectorStore(settings.qdrant_url, settings.collection_name)
    await store.ensure_collection(settings.embedding_dim)
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    settings = get_settings()
    application = FastAPI(title="DocQA", version="0.3.1", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()
