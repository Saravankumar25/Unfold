"""Async engine + session factory (one per process)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from unfold.core.config import Settings


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
        async with self.sessions() as s:
            yield s

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as s, s.begin():
            yield s

    async def ping(self) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()
