"""Vector store abstraction supporting Pinecone serverless and in-memory test backend."""

from __future__ import annotations

import logging
import math
from typing import Any, Protocol, runtime_checkable

from app.core.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "enterprise-kb"
EMBEDDING_DIM = 384


@runtime_checkable
class VectorStore(Protocol):
    """Protocol for vector store backends (Pinecone and InMemory)."""

    def upsert(self, vectors: list[dict[str, Any]], namespace: str = DEFAULT_NAMESPACE) -> int:
        """Upsert vectors into the index. Each vector is {'id': str, 'values': list[float], 'metadata': dict}."""
        ...

    def query(
        self,
        vector: list[float],
        top_k: int = 5,
        namespace: str = DEFAULT_NAMESPACE,
        filter_dict: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query vector index. Returns list of dicts with keys: 'id', 'score', 'metadata'."""
        ...

    def stats(self, namespace: str = DEFAULT_NAMESPACE) -> dict[str, Any]:
        """Return index stats (total_vector_count, dimension, etc.)."""
        ...


class PineconeStore:
    """Pinecone serverless vector store implementation."""

    def __init__(self) -> None:
        settings = get_settings()
        api_key = settings.pinecone_api_key.strip()
        if not api_key:
            raise RuntimeError(
                "PINECONE_API_KEY is missing or empty in .env. "
                "Set a valid Pinecone key or use InMemoryStore for testing."
            )

        self._index_name = settings.pinecone_index
        self._cloud = settings.pinecone_cloud
        self._region = settings.pinecone_region

        try:
            from pinecone import Pinecone, ServerlessSpec
            self._pc = Pinecone(api_key=api_key)

            existing_indexes = [idx.name for idx in self._pc.list_indexes()]
            if self._index_name not in existing_indexes:
                logger.info(
                    f"Pinecone index '{self._index_name}' not found. "
                    f"Creating serverless index ({self._cloud}/{self._region})..."
                )
                self._pc.create_index(
                    name=self._index_name,
                    dimension=EMBEDDING_DIM,
                    metric="cosine",
                    spec=ServerlessSpec(cloud=self._cloud, region=self._region),
                )
                # Wait until index is ready
                import time
                while not self._pc.describe_index(self._index_name).status["ready"]:
                    time.sleep(1)

            self._index = self._pc.Index(self._index_name)
        except Exception as e:
            logger.error(f"Failed to initialize Pinecone store: {e}")
            raise RuntimeError(f"Pinecone initialization error: {e}") from e

    def upsert(self, vectors: list[dict[str, Any]], namespace: str = DEFAULT_NAMESPACE) -> int:
        """Upsert vectors to Pinecone namespace."""
        if not vectors:
            return 0
        
        # Batch in chunks of 100 for safety
        batch_size = 100
        count = 0
        for i in range(0, len(vectors), batch_size):
            batch = vectors[i : i + batch_size]
            self._index.upsert(vectors=batch, namespace=namespace)
            count += len(batch)
        return count

    def query(
        self,
        vector: list[float],
        top_k: int = 5,
        namespace: str = DEFAULT_NAMESPACE,
        filter_dict: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query Pinecone vector index."""
        kwargs: dict[str, Any] = {
            "vector": vector,
            "top_k": top_k,
            "include_metadata": True,
            "namespace": namespace,
        }
        if filter_dict:
            kwargs["filter"] = filter_dict

        res = self._index.query(**kwargs)
        results = []
        for match in res.get("matches", []):
            results.append({
                "id": match.get("id"),
                "score": float(match.get("score", 0.0)),
                "metadata": match.get("metadata", {}),
            })
        return results

    def stats(self, namespace: str = DEFAULT_NAMESPACE) -> dict[str, Any]:
        """Fetch Pinecone index statistics."""
        try:
            stat_res = self._index.describe_index_stats()
            ns_stats = stat_res.get("namespaces", {}).get(namespace, {})
            total_count = ns_stats.get("vector_count", stat_res.get("total_vector_count", 0))
            return {
                "index_name": self._index_name,
                "dimension": stat_res.get("dimension", EMBEDDING_DIM),
                "total_vector_count": stat_res.get("total_vector_count", 0),
                "namespace_vector_count": total_count,
                "namespace": namespace,
            }
        except Exception as e:
            logger.warning(f"Failed to fetch Pinecone stats: {e}")
            return {"index_name": self._index_name, "error": str(e)}


class InMemoryStore:
    """In-memory vector store implementation for unit tests and fallback."""

    def __init__(self) -> None:
        # storage: {namespace: {vector_id: {'values': list[float], 'metadata': dict}}}
        self._storage: dict[str, dict[str, dict[str, Any]]] = {}

    def upsert(self, vectors: list[dict[str, Any]], namespace: str = DEFAULT_NAMESPACE) -> int:
        """Upsert vectors into in-memory store."""
        if namespace not in self._storage:
            self._storage[namespace] = {}

        for vec in vectors:
            vec_id = str(vec["id"])
            self._storage[namespace][vec_id] = {
                "values": vec["values"],
                "metadata": vec.get("metadata", {}),
            }
        return len(vectors)

    @staticmethod
    def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
        """Compute cosine similarity between two float vectors."""
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))
        if norm1 == 0.0 or norm2 == 0.0:
            return 0.0
        return dot / (norm1 * norm2)

    def query(
        self,
        vector: list[float],
        top_k: int = 5,
        namespace: str = DEFAULT_NAMESPACE,
        filter_dict: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query in-memory vectors using cosine similarity."""
        ns_data = self._storage.get(namespace, {})
        scores = []

        for vec_id, data in ns_data.items():
            meta = data["metadata"]
            if filter_dict:
                match = all(meta.get(k) == v for k, v in filter_dict.items())
                if not match:
                    continue

            sim = self._cosine_similarity(vector, data["values"])
            scores.append({"id": vec_id, "score": sim, "metadata": meta})

        scores.sort(key=lambda x: x["score"], reverse=True)
        return scores[:top_k]

    def stats(self, namespace: str = DEFAULT_NAMESPACE) -> dict[str, Any]:
        """Get in-memory index stats."""
        ns_data = self._storage.get(namespace, {})
        total_vectors = sum(len(v) for v in self._storage.values())
        return {
            "index_name": "in-memory",
            "dimension": EMBEDDING_DIM,
            "total_vector_count": total_vectors,
            "namespace_vector_count": len(ns_data),
            "namespace": namespace,
        }


_vector_store_override: VectorStore | None = None


def get_vector_store() -> VectorStore:
    """Factory function returning the configured VectorStore implementation."""
    global _vector_store_override
    if _vector_store_override is not None:
        return _vector_store_override

    settings = get_settings()
    api_key = settings.pinecone_api_key.strip()
    
    if settings.mock_llm or not api_key:
        logger.info("Using InMemoryStore (MOCK_LLM=true or PINECONE_API_KEY missing).")
        return InMemoryStore()

    try:
        return PineconeStore()
    except Exception as e:
        logger.warning(f"Pinecone unavailable ({e}). Falling back to InMemoryStore.")
        return InMemoryStore()


def set_vector_store_override(store: VectorStore | None) -> None:
    """Override default vector store (useful for tests)."""
    global _vector_store_override
    _vector_store_override = store
