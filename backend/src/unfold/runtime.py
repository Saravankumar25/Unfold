"""Process-wide dependency container (HLD §3). Built once by a composition root (api.main / worker.run).

Library modules receive a `Runtime` (or the specific stores they need) as an argument; they never construct
clients themselves, so tests can inject real test stores and there is exactly one client per process.

Two profiles (`PERSISTENCE_BACKEND`): `postgres` is the local stack (Postgres, Redis, MinIO); `dynamodb` is the
AWS profile (DynamoDB, S3). The profile's modules are imported only when it is selected, so a Lambda never loads
SQLAlchemy, Redis or the MinIO SDK. Neo4j and Qdrant are built on first use, and only when their backend is chosen.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from unfold.core.config import Settings
from unfold.core.errors import StoreUnavailable
from unfold.ports import BlobStore, ChunkStore, ControlStore, CounterStore, ProgressStore, RateLimiter

if TYPE_CHECKING:
    from unfold.llm.schemas import LLMClient
    from unfold.persistence.neo4j_store import Neo4jStore
    from unfold.persistence.postgres.db import Database
    from unfold.persistence.qdrant_store import QdrantStore
    from unfold.persistence.redis_store import RedisStore


@dataclass
class Runtime:
    settings: Settings
    control: ControlStore
    chunks: ChunkStore
    objects: BlobStore
    limiter: RateLimiter
    progress: ProgressStore
    counters: CounterStore | None = None  # DynamoDB profile: LLM usage and provider windows
    llm: LLMClient | None = None  # set by the composition root once the gateway is built
    extras: dict[str, Any] = field(
        default_factory=dict
    )  # lazily created heavy singletons (embedder, reranker)
    _stores: dict[str, Any] = field(default_factory=dict)  # backend clients by name
    _closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    @classmethod
    def create(cls, settings: Settings, *, db_pool_size: int = 10) -> Runtime:
        if settings.persistence_backend == "dynamodb":
            return cls._aws(settings)
        return cls._local(settings, db_pool_size)

    @classmethod
    def _local(cls, settings: Settings, db_pool_size: int) -> Runtime:
        from unfold.indexing.store import ChunkRepository
        from unfold.persistence.object_store import MinioBlobStore
        from unfold.persistence.postgres.control import PostgresControlStore
        from unfold.persistence.postgres.db import Database
        from unfold.persistence.postgres.progress import PostgresProgressStore
        from unfold.persistence.redis_store import RedisRateLimiter, RedisStore

        db = Database(settings, pool_size=db_pool_size)
        redis = RedisStore(settings)
        rt = cls(
            settings=settings,
            control=PostgresControlStore(db),
            chunks=ChunkRepository(db),
            objects=MinioBlobStore(settings),
            limiter=RedisRateLimiter(redis),
            progress=PostgresProgressStore(db),
            _stores={"db": db, "redis": redis},
        )
        rt._closers += [db.dispose, redis.aclose]
        return rt

    @classmethod
    def _aws(cls, settings: Settings) -> Runtime:
        from unfold.indexing.object_chunks import ObjectChunkStore
        from unfold.persistence.dynamodb.control import DynamoControlStore
        from unfold.persistence.dynamodb.counters import DynamoCounters, DynamoRateLimiter
        from unfold.persistence.dynamodb.progress import DynamoProgressStore
        from unfold.persistence.dynamodb.table import DynamoTable
        from unfold.persistence.s3_store import S3BlobStore

        table = DynamoTable(settings)
        objects = S3BlobStore(settings)
        counters = DynamoCounters(table)
        return cls(
            settings=settings,
            control=DynamoControlStore(table),
            chunks=ObjectChunkStore(objects),
            objects=objects,
            limiter=DynamoRateLimiter(counters, table),
            progress=DynamoProgressStore(table),
            counters=counters,
        )

    # --- local-only and optional backends, built on first use -------------------------------------------

    def _need(self, name: str) -> Any:
        store = self._stores.get(name)
        if store is None:
            raise StoreUnavailable(
                f"The {name} store is not part of the {self.settings.persistence_backend} profile.",
                retryable=False,
            )
        return store

    @property
    def db(self) -> Database:
        db: Database = self._need("db")
        return db

    @property
    def redis(self) -> RedisStore:
        redis: RedisStore = self._need("redis")
        return redis

    @property
    def neo4j(self) -> Neo4jStore:
        if "neo4j" not in self._stores:
            from unfold.persistence.neo4j_store import Neo4jStore

            store = Neo4jStore(self.settings)
            self._stores["neo4j"] = store
            self._closers.append(store.aclose)
        neo4j: Neo4jStore = self._stores["neo4j"]
        return neo4j

    @property
    def qdrant(self) -> QdrantStore:
        if "qdrant" not in self._stores:
            from unfold.persistence.qdrant_store import QdrantStore

            store = QdrantStore(self.settings)
            self._stores["qdrant"] = store
            self._closers.append(store.aclose)
        qdrant: QdrantStore = self._stores["qdrant"]
        return qdrant

    async def aclose(self) -> None:
        for close in reversed(self._closers):
            await close()
        self._closers.clear()
