"""arq background worker: document ingestion jobs."""

import logging

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.ingestion import IngestionService

logger = logging.getLogger(__name__)


async def ingest_document(ctx: dict, document_id: int) -> int:
    """Chunk, embed and index one uploaded document."""
    async with SessionLocal() as session:
        service = IngestionService(session)
        count = await service.ingest(document_id)
    logger.info("ingested document %s into %s chunks", document_id, count)
    return count


async def startup(ctx: dict) -> None:
    logger.info("worker starting")


class WorkerSettings:
    """arq worker configuration."""

    functions = [ingest_document]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
