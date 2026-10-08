"""Requirement-driven Enterprise RAG agent for retrieving grounded knowledge base chunks with provenance."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models.trace import AgentTrace
from app.services.embeddings import embed_query
from app.services.source_registry import SourceRegistry
from app.services.vector_store import DEFAULT_NAMESPACE, get_vector_store

logger = logging.getLogger(__name__)


class ProvenanceRecord(BaseModel):
    """Full provenance tracking for an evidence chunk."""

    chunk_id: str = ""
    document_id: str | int | None = None
    artifact_id: int | None = None
    version_id: int | None = None
    node_id: str | None = None
    file_name: str = ""
    page: int | None = None
    section: str = ""
    source_type: str = "kb"
    text: str = ""
    timestamp: float = Field(default_factory=time.time)


def retrieve(
    query: str,
    top_k: int = 5,
    min_score: float = 0.3,
    registry: SourceRegistry | None = None,
    db_session: Session | None = None,
    trace_id: str | None = None,
    namespace: str = DEFAULT_NAMESPACE,
) -> list[dict[str, Any]]:
    """Retrieve top-k enterprise KB chunks for a search query.

    Args:
        query: Search query string.
        top_k: Max number of results to retrieve.
        min_score: Cosine similarity score threshold (default 0.3).
        registry: SourceRegistry instance to add sources to.
        db_session: Optional SQLAlchemy DB session for AgentTrace recording.
        trace_id: Optional trace grouping ID.
        namespace: Vector store namespace.

    Returns:
        List of hit dicts: [{'source_id': int, 'score': float, 'title': str, 'text': str, 'metadata': dict, 'provenance': dict}]
    """
    start_time = time.time()
    trace_uuid = trace_id or str(uuid.uuid4())
    store = get_vector_store()

    # 1. Embed query
    query_vector = embed_query(query)

    # 2. Query VectorStore
    raw_hits = store.query(vector=query_vector, top_k=top_k, namespace=namespace)

    # 3. Filter hits by min_score and register in SourceRegistry
    hits: list[dict[str, Any]] = []
    if registry is None:
        registry = SourceRegistry(db_session=db_session)

    for match in raw_hits:
        score = float(match.get("score", 0.0))
        if score < min_score:
            continue

        meta = match.get("metadata", {})
        source_name = meta.get("source_name", "KB Document")
        heading = meta.get("heading", "")
        chunk_text = meta.get("text", "")
        chunk_id = match.get("id", str(uuid.uuid4()))

        title = f"{source_name} - {heading}" if heading else source_name

        source_id = registry.add(
            kind="kb",
            title=title,
            url_or_path=source_name,
            snippet=chunk_text,
            score=score,
        )

        prov = ProvenanceRecord(
            chunk_id=chunk_id,
            file_name=source_name,
            section=heading,
            source_type="kb",
            text=chunk_text,
        )

        hits.append({
            "source_id": source_id,
            "score": score,
            "title": title,
            "source_name": source_name,
            "heading": heading,
            "text": chunk_text,
            "metadata": meta,
            "provenance": prov.model_dump(),
        })

    duration_ms = int((time.time() - start_time) * 1000)

    # 4. Log AgentTrace row
    if db_session:
        try:
            trace_row = AgentTrace(
                trace_id=trace_uuid,
                agent_name="rag_agent",
                input_summary=f"query='{query[:100]}', top_k={top_k}, min_score={min_score}",
                output_summary=f"Retrieved {len(hits)} KB chunks above score {min_score}",
                duration_ms=duration_ms,
                status="ok",
            )
            db_session.add(trace_row)
            db_session.flush()
        except Exception as exc:
            logger.warning(f"Failed to record AgentTrace row for rag_agent: {exc}")

    logger.info(f"RAG agent retrieved {len(hits)} hits for '{query}' in {duration_ms}ms.")
    return hits


def retrieve_for_requirements(
    requirements: list[str],
    top_k_per_req: int = 3,
    min_score: float = 0.3,
    registry: SourceRegistry | None = None,
    db_session: Session | None = None,
    namespace: str = DEFAULT_NAMESPACE,
) -> list[dict[str, Any]]:
    """Retrieve KB evidence targeted to specific planner requirements."""
    aggregated_hits: list[dict[str, Any]] = []
    seen_texts: set[str] = set()

    for req in requirements:
        req_clean = req.strip()
        if not req_clean:
            continue
        hits = retrieve(
            query=req_clean,
            top_k=top_k_per_req,
            min_score=min_score,
            registry=registry,
            db_session=db_session,
            namespace=namespace,
        )
        for h in hits:
            t = h.get("text", "")
            if t not in seen_texts:
                seen_texts.add(t)
                aggregated_hits.append(h)

    return aggregated_hits


def evaluate_evidence_sufficiency(
    evidence: list[dict[str, Any]],
    requirements: list[str],
) -> tuple[bool, list[str]]:
    """Evaluate whether retrieved evidence is sufficient to satisfy planner requirements.

    Returns:
        (is_sufficient, list_of_missing_requirements)
    """
    if not requirements:
        return True, []

    if not evidence:
        return False, requirements

    all_evidence_text = " ".join(e.get("text", "").lower() for e in evidence)
    missing: list[str] = []

    for req in requirements:
        words = [w for w in req.lower().split() if len(w) > 3]
        if words:
            matched_words = [w for w in words if w in all_evidence_text]
            if len(matched_words) / len(words) < 0.2:
                missing.append(req)

    return len(missing) == 0, missing
