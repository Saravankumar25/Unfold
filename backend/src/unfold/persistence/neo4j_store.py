"""Neo4j driver lifecycle (ESD §4.2). Cypher lives in `unfold.graph`; this module only owns the connection."""

from __future__ import annotations

from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

from unfold.core.config import Settings


class Neo4jStore:
    def __init__(self, settings: Settings) -> None:
        self.driver: AsyncDriver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password.get_secret_value()),
            max_connection_pool_size=20,
        )

    async def ping(self) -> None:
        await self.driver.verify_connectivity()

    async def aclose(self) -> None:
        await self.driver.close()

    async def run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Run a parameterised query and return records as dicts (reads and small writes)."""
        records, _summary, _keys = await self.driver.execute_query(cypher, params, database_="neo4j")
        return [r.data() for r in records]
