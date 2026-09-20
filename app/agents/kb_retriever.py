"""KB retrieval agent – queries Pinecone for relevant enterprise knowledge."""

from __future__ import annotations

import logging
import time

from langchain_core.messages import AIMessage

from app.agents.state import AgentState
from app.services.embeddings import embed_query
from app.services.vector_store import get_vector_store

logger = logging.getLogger(__name__)


def kb_retriever_node(state: AgentState) -> dict:
    """Retrieve relevant chunks from the enterprise knowledge base."""
    start = time.time()
    user_request = state.get("user_request", "")
    project_id = state.get("project_id", 0)

    try:
        embedding = embed_query(user_request)
        vs = get_vector_store()
        results = vs.query(
            embedding,
            top_k=5,
            filter={"project_id": project_id} if project_id else None,
        )
        kb_results = [
            {
                "text": m["metadata"].get("text", ""),
                "source_filename": m["metadata"].get("source_filename", ""),
                "score": m["score"],
            }
            for m in results
        ]
    except Exception as exc:
        logger.error("KB retrieval failed: %s", exc)
        kb_results = []

    duration_ms = int((time.time() - start) * 1000)
    logger.info("KB retriever found %d chunks in %dms", len(kb_results), duration_ms)

    return {
        "kb_results": kb_results,
        "messages": [AIMessage(content=f"KB retrieval complete: found {len(kb_results)} relevant chunks.")],
    }
