"""Sentence-Transformers embedding service.

Lazy-loads sentence-transformers 'all-MiniLM-L6-v2' model (384-dimensional)
to generate dense vector representations for documents and search queries.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

_model_instance: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    """Lazy-load the SentenceTransformer model on first invocation."""
    global _model_instance
    if _model_instance is None:
        logger.info(f"Loading embedding model '{MODEL_NAME}'...")
        from sentence_transformers import SentenceTransformer
        _model_instance = SentenceTransformer(MODEL_NAME)
    return _model_instance


def embed_texts(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Generate dense embeddings for a batch of text strings.

    Args:
        texts: List of text strings to embed.
        batch_size: Number of items processed per batch.

    Returns:
        List of 384-dimensional vector embeddings as floating point lists.
    """
    if not texts:
        return []
    
    model = _get_model()
    embeddings = model.encode(texts, batch_size=batch_size, show_progress_bar=False, convert_to_numpy=True)
    return embeddings.tolist()


def embed_query(query: str) -> list[float]:
    """Generate dense embedding for a single search query string.

    Args:
        query: Query string to embed.

    Returns:
        384-dimensional vector embedding.
    """
    if not query.strip():
        return [0.0] * EMBEDDING_DIM
    
    res = embed_texts([query])
    return res[0]
