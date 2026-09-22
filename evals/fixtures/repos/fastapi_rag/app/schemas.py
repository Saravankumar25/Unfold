"""Pydantic request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentOut(BaseModel):
    """Public view of an uploaded document."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    status: str
    created_at: datetime


class DocumentStatus(BaseModel):
    id: int
    status: str
    chunk_count: int


class QueryRequest(BaseModel):
    """A user question."""

    question: str = Field(min_length=3, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=50)


class Citation(BaseModel):
    chunk_id: int
    document_id: int
    score: float


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
