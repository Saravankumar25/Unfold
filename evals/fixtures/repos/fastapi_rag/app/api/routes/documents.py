"""Document upload and listing endpoints."""

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_queue
from app.schemas import DocumentOut, DocumentStatus
from app.services.ingestion import IngestionService

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=DocumentOut, status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    queue: ArqRedis = Depends(get_queue),
) -> DocumentOut:
    """Store the uploaded file and enqueue background ingestion."""
    if file.content_type not in ("application/pdf", "text/markdown", "text/plain"):
        raise HTTPException(status_code=415, detail="Unsupported document type")
    service = IngestionService(db)
    document = await service.register_upload(file.filename or "upload", await file.read())
    await queue.enqueue_job("ingest_document", document.id)
    return DocumentOut.model_validate(document)


@router.get("", response_model=list[DocumentOut])
async def list_documents(db: AsyncSession = Depends(get_db)) -> list[DocumentOut]:
    """List uploaded documents with their ingestion status."""
    service = IngestionService(db)
    return [DocumentOut.model_validate(d) for d in await service.list_documents()]


@router.get("/{document_id}", response_model=DocumentStatus)
async def get_document(document_id: int, db: AsyncSession = Depends(get_db)) -> DocumentStatus:
    """Return one document and its chunk count."""
    service = IngestionService(db)
    document = await service.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentStatus(id=document.id, status=document.status, chunk_count=len(document.chunks))


@router.delete("/{document_id}", status_code=204)
async def delete_document(document_id: int, db: AsyncSession = Depends(get_db)) -> None:
    """Delete a document, its chunks and its vectors."""
    service = IngestionService(db)
    await service.delete_document(document_id)
