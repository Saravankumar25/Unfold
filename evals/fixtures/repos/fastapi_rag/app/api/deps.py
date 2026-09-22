"""Shared FastAPI dependencies."""

from collections.abc import AsyncIterator

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import SessionLocal


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield a database session per request."""
    async with SessionLocal() as session:
        yield session


async def get_queue() -> ArqRedis:
    """Return an arq Redis pool used to enqueue background jobs."""
    settings = get_settings()
    return await create_pool(RedisSettings.from_dsn(settings.redis_url))
