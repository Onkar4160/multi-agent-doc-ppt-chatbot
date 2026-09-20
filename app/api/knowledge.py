"""Knowledge Base and Web Research API Router."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.rag_agent import retrieve
from app.agents.web_research import research
from app.api.deps import get_current_user, get_db
from app.services.file_utils import save_upload, validate_upload
from app.services.ingestion import ingest_directory, ingest_file
from app.services.source_registry import SourceRegistry
from app.services.vector_store import get_vector_store

router = APIRouter(tags=["knowledge_and_research"])


class ResearchRequest(BaseModel):
    """Payload for web research endpoint."""
    topic: str = Field(description="Topic or problem statement for web research.")


@router.post("/kb/ingest", status_code=status.HTTP_201_CREATED)
async def kb_ingest_file(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """Upload and ingest a single document file into the vector store."""
    validate_upload(file)
    saved_path = await save_upload(file)
    result = ingest_file(saved_path)
    return {"status": "ok", "result": result}


@router.post("/kb/ingest-samples")
async def kb_ingest_samples(
    user=Depends(get_current_user),
):
    """Ingest all sample knowledge base documents in data/sample_kb."""
    sample_dir = Path("data/sample_kb")
    if not sample_dir.exists():
        return {"status": "error", "message": "data/sample_kb directory does not exist."}

    results = ingest_directory(sample_dir)
    total_chunks = sum(r.get("chunks_count", 0) for r in results)
    return {
        "status": "ok",
        "ingested_files": len(results),
        "total_chunks": total_chunks,
        "details": results,
    }


@router.get("/kb/search")
async def kb_search(
    q: str = Query(..., min_length=1, description="Search query string"),
    top_k: int = Query(5, ge=1, le=20),
    min_score: float = Query(0.3, ge=0.0, le=1.0),
    user=Depends(get_current_user),
):
    """Semantic search over the knowledge base."""
    registry = SourceRegistry()
    hits = retrieve(query=q, top_k=top_k, min_score=min_score, registry=registry)
    return {
        "query": q,
        "hits_count": len(hits),
        "hits": hits,
        "sources": registry.export_sources_list(),
    }


@router.get("/kb/stats")
async def kb_stats(
    user=Depends(get_current_user),
):
    """Get vector store index statistics."""
    vs = get_vector_store()
    return vs.stats()


@router.post("/research")
async def execute_web_research(
    body: ResearchRequest,
    user=Depends(get_current_user),
):
    """Execute focused web research on a topic."""
    registry = SourceRegistry()
    res = research(topic=body.topic, registry=registry)
    return {
        "topic": body.topic,
        "queries": res.queries,
        "findings": [f.model_dump() for f in res.findings],
        "sources": registry.export_sources_list(),
    }
