"""Process-wide dependency container (HLD §3). Built once by a composition root (api.main / worker.settings).

Library modules receive a `Runtime` (or the specific stores they need) as an argument; they never construct
clients themselves, so tests can inject real test stores and there is exactly one client per process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from unfold.core.config import Settings
from unfold.persistence.neo4j_store import Neo4jStore
from unfold.persistence.object_store import MinioBlobStore
from unfold.persistence.postgres.db import Database
from unfold.persistence.postgres.progress import PostgresProgressStore
from unfold.persistence.qdrant_store import QdrantStore
from unfold.persistence.redis_store import RedisRateLimiter, RedisStore
from unfold.ports import BlobStore, ProgressStore, RateLimiter

if TYPE_CHECKING:
    from unfold.llm.schemas import LLMClient


@dataclass
class Runtime:
    settings: Settings
    db: Database
    redis: RedisStore  # cache + LLM coordination only; app code uses the ports below
    neo4j: Neo4jStore
    qdrant: QdrantStore
    objects: BlobStore
    limiter: RateLimiter
    progress: ProgressStore
    llm: LLMClient | None = None  # set by the composition root once the gateway is built
    extras: dict[str, Any] = field(
        default_factory=dict
    )  # lazily created heavy singletons (embedder, reranker)

    @classmethod
    def create(cls, settings: Settings, *, db_pool_size: int = 10) -> Runtime:
        db = Database(settings, pool_size=db_pool_size)
        redis = RedisStore(settings)
        return cls(
            settings=settings,
            db=db,
            redis=redis,
            neo4j=Neo4jStore(settings),
            qdrant=QdrantStore(settings),
            objects=MinioBlobStore(settings),
            limiter=RedisRateLimiter(redis),
            progress=PostgresProgressStore(db),
        )

    async def aclose(self) -> None:
        await self.db.dispose()
        await self.redis.aclose()
        await self.neo4j.aclose()
        await self.qdrant.aclose()
