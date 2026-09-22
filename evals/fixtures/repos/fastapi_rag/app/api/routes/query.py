"""Question answering endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas import QueryRequest, QueryResponse
from app.services.llm import LLMClient
from app.services.retriever import HybridRetriever

router = APIRouter(prefix="/query", tags=["query"])


@router.post("", response_model=QueryResponse)
async def answer_query(request: QueryRequest, db: AsyncSession = Depends(get_db)) -> QueryResponse:
    """Retrieve relevant chunks with hybrid search and ask the LLM for a cited answer."""
    retriever = HybridRetriever(db)
    hits = await retriever.search(request.question, top_k=request.top_k)
    llm = LLMClient()
    answer = await llm.answer(request.question, hits)
    return QueryResponse(answer=answer.text, citations=answer.citations)
