"""Async engine + session factory (one per process)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from unfold.core.config import Settings
from unfold.core.errors import StoreUnavailable


@contextmanager
def _store_outage() -> Iterator[None]:
    """A paused or restarting database (connection-level failure, not a bad statement) is retryable: every
    pipeline stage is checkpointed, so the job resumes."""
    try:
        yield
    except DBAPIError as exc:
        if exc.connection_invalidated or isinstance(exc, OperationalError | InterfaceError):
            raise StoreUnavailable("The database was unreachable; try again.") from exc
        raise
    except ConnectionError as exc:
        raise StoreUnavailable("The database was unreachable; try again.") from exc


class Database:
    def __init__(self, settings: Settings, *, pool_size: int = 10) -> None:
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_size=pool_size,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        with _store_outage():
            async with self.sessions() as s:
                yield s

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        with _store_outage():
            async with self.sessions() as s, s.begin():
                yield s

    async def ping(self) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()
