"""RAG agent for retrieving relevant enterprise knowledge base chunks."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.trace import AgentTrace
from app.services.embeddings import embed_query
from app.services.source_registry import SourceRegistry
from app.services.vector_store import DEFAULT_NAMESPACE, get_vector_store

logger = logging.getLogger(__name__)


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
        List of hit dicts: [{'source_id': int, 'score': float, 'title': str, 'text': str, 'metadata': dict}]
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

        title = f"{source_name} - {heading}" if heading else source_name

        source_id = registry.add(
            kind="kb",
            title=title,
            url_or_path=source_name,
            snippet=chunk_text,
            score=score,
        )

        hits.append({
            "source_id": source_id,
            "score": score,
            "title": title,
            "source_name": source_name,
            "heading": heading,
            "text": chunk_text,
            "metadata": meta,
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
