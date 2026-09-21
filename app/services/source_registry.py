"""Source registry service for deduplicating, tracking, and assigning citation IDs."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session
from app.models.source import Source

logger = logging.getLogger(__name__)


class SourceRegistry:
    """Tracks web research and KB retrieval sources for citations within a run."""

    def __init__(
        self,
        db_session: Session | None = None,
        project_id: int | None = None,
        start_id: int = 1,
    ) -> None:
        self._db_session = db_session
        self._project_id = project_id
        # {id: {"id": int, "kind": str, "title": str, "url": str, "snippet": str, "score": float, "published_date": str}}
        self._sources: dict[int, dict[str, Any]] = {}
        # deduplication index: key -> source_id
        self._dedupe_index: dict[str, int] = {}
        self._next_id = start_id

    def add(
        self,
        kind: str,  # "kb" | "web"
        title: str,
        url_or_path: str,
        snippet: str,
        score: float = 0.0,
        published_date: str | None = None,
    ) -> int:
        """Register a source, return citation integer ID starting at 1.

        Dedupes identical URLs (web) or identical title+path (KB).
        """
        title = (title or "Untitled Source").strip()
        url_or_path = (url_or_path or "").strip()
        snippet = (snippet or "").strip()

        # Deduplication key
        if kind == "web" and url_or_path:
            dedupe_key = f"web:{url_or_path.lower()}"
        else:
            dedupe_key = f"kb:{title.lower()}:{url_or_path.lower()}:{snippet[:100]}"

        if dedupe_key in self._dedupe_index:
            existing_id = self._dedupe_index[dedupe_key]
            # Update score if higher
            if score > self._sources[existing_id]["score"]:
                self._sources[existing_id]["score"] = score
            return existing_id

        source_id = self._next_id
        self._next_id += 1

        record = {
            "id": source_id,
            "kind": kind,
            "title": title,
            "url": url_or_path,
            "snippet": snippet,
            "score": score,
            "published_date": published_date,
        }
        self._sources[source_id] = record
        self._dedupe_index[dedupe_key] = source_id

        # Persist to database if session is provided
        if self._db_session:
            try:
                db_source = Source(
                    project_id=self._project_id,
                    kind=kind,
                    url=url_or_path,
                    title=title,
                    chunk_text=snippet,
                    metadata_json=json.dumps({
                        "score": score,
                        "published_date": published_date,
                        "citation_id": source_id,
                    }),
                )
                self._db_session.add(db_source)
                self._db_session.flush()
            except Exception as e:
                logger.warning(f"Failed to persist Source row to database: {e}")

        return source_id

    def get(self, source_id: int) -> dict[str, Any] | None:
        """Get source record by integer citation ID."""
        return self._sources.get(source_id)

    def export_sources(self) -> dict[int, dict[str, Any]]:
        """Export sources map format {id: {"title": ..., "url": ..., "snippet": ...}} for renderers."""
        return {
            sid: {
                "id": s["id"],
                "kind": s["kind"],
                "title": s["title"],
                "url": s["url"],
                "snippet": s["snippet"],
                "score": s["score"],
                "published_date": s["published_date"],
            }
            for sid, s in self._sources.items()
        }

    def export_sources_list(self) -> list[dict[str, Any]]:
        """Export sources as a list of dict objects."""
        return list(self._sources.values())

    def __len__(self) -> int:
        return len(self._sources)
