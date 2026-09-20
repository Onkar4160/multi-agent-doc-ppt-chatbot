"""Pinecone vector store wrapper with auto-index creation."""

from __future__ import annotations

import logging
from typing import Any

from pinecone import Pinecone, ServerlessSpec

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_instance: "PineconeStore | None" = None


class PineconeStore:
    """Thin wrapper around the Pinecone SDK v5+ for upsert and query."""

    def __init__(self) -> None:
        settings = get_settings()
        self._pc = Pinecone(api_key=settings.pinecone_api_key)
        self._index_name = settings.pinecone_index
        self._cloud = settings.pinecone_cloud
        self._region = settings.pinecone_region
        self._ensure_index()
        self._index = self._pc.Index(self._index_name)

    def _ensure_index(self) -> None:
        """Create the index if it doesn't exist."""
        existing = [idx.name for idx in self._pc.list_indexes()]
        if self._index_name not in existing:
            logger.info("Creating Pinecone index '%s' (384 dim, cosine)…", self._index_name)
            self._pc.create_index(
                name=self._index_name,
                dimension=384,
                metric="cosine",
                spec=ServerlessSpec(cloud=self._cloud, region=self._region),
            )
            logger.info("Pinecone index created")

    def upsert_chunks(self, chunks: list[dict[str, Any]], namespace: str = "default") -> int:
        """Batch-upsert vectors. Each chunk: {id, values, metadata}."""
        batch_size = 100
        total = 0
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            self._index.upsert(vectors=batch, namespace=namespace)
            total += len(batch)
        logger.info("Upserted %d vectors to namespace '%s'", total, namespace)
        return total

    def query(
        self,
        embedding: list[float],
        top_k: int = 5,
        namespace: str = "default",
        filter: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Query the index and return matches with metadata."""
        kwargs: dict[str, Any] = {
            "vector": embedding,
            "top_k": top_k,
            "namespace": namespace,
            "include_metadata": True,
        }
        if filter:
            kwargs["filter"] = filter

        results = self._index.query(**kwargs)
        return [
            {
                "id": m["id"],
                "score": m["score"],
                "metadata": m.get("metadata", {}),
            }
            for m in results.get("matches", [])
        ]


def get_vector_store() -> PineconeStore:
    """Return a cached PineconeStore singleton."""
    global _instance
    if _instance is None:
        _instance = PineconeStore()
    return _instance
