"""Knowledge base endpoints – ingest and query."""

from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.services.embeddings import embed_query
from app.services.file_utils import validate_upload, save_upload
from app.services.kb_ingest import ingest_document
from app.services.vector_store import get_vector_store

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class QueryRequest(BaseModel):
    """Semantic search request."""
    query: str
    project_id: int
    top_k: int = 5


@router.post("/ingest")
async def ingest_kb_document(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Upload and ingest a knowledge base document into the vector store."""
    validate_upload(file)
    saved_path = await save_upload(file)
    count = ingest_document(str(saved_path), project_id)
    return {"status": "ok", "chunks_ingested": count, "filename": file.filename}


@router.post("/query")
async def query_kb(
    body: QueryRequest,
    user=Depends(get_current_user),
):
    """Semantic search over the knowledge base."""
    embedding = embed_query(body.query)
    vs = get_vector_store()
    results = vs.query(
        embedding,
        top_k=body.top_k,
        filter={"project_id": body.project_id},
    )
    return {"query": body.query, "results": results}
