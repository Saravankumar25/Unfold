"""Document ingestion: persist uploads, extract text, chunk, embed and index."""

import io

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.models import Chunk, Document
from app.services.chunking import split_text
from app.services.embeddings import embed_texts
from app.services.vector_store import VectorStore


def extract_text(filename: str, data: bytes) -> str:
    """Extract plain text from a PDF or Markdown/plain-text upload."""
    if filename.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    return data.decode("utf-8", errors="replace")


class IngestionService:
    """Owns the document lifecycle in PostgreSQL and Qdrant."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        settings = get_settings()
        self.settings = settings
        self.store = VectorStore(settings.qdrant_url, settings.collection_name)

    async def register_upload(self, filename: str, data: bytes) -> Document:
        """Create a pending document row holding the extracted text."""
        document = Document(
            filename=filename,
            content_type="application/octet-stream",
            status="pending",
            raw_text=extract_text(filename, data),
        )
        self.db.add(document)
        await self.db.commit()
        await self.db.refresh(document)
        return document

    async def list_documents(self) -> list[Document]:
        result = await self.db.execute(select(Document).order_by(Document.created_at.desc()))
        return list(result.scalars())

    async def get_document(self, document_id: int) -> Document | None:
        result = await self.db.execute(
            select(Document).options(selectinload(Document.chunks)).where(Document.id == document_id)
        )
        return result.scalar_one_or_none()

    async def ingest(self, document_id: int) -> int:
        """Chunk, embed and index a pending document. Returns the number of chunks."""
        document = await self.get_document(document_id)
        if document is None or not document.raw_text:
            return 0
        pieces = split_text(document.raw_text, self.settings.chunk_size, self.settings.chunk_overlap)
        rows = [
            Chunk(document_id=document.id, position=p.position, text=p.text, token_count=p.token_count)
            for p in pieces
        ]
        self.db.add_all(rows)
        await self.db.flush()
        vectors = embed_texts([r.text for r in rows])
        payloads = [{"document_id": document.id, "position": r.position} for r in rows]
        await self.store.upsert_chunks([r.id for r in rows], vectors, payloads)
        document.status = "ready"
        await self.db.commit()
        return len(rows)

    async def delete_document(self, document_id: int) -> None:
        document = await self.get_document(document_id)
        if document is None:
            return
        await self.store.delete_document(document_id)
        await self.db.delete(document)
        await self.db.commit()
