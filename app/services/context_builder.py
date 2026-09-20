"""Context builder service aggregating web research and KB retrieval into citation sources and findings."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.rag_agent import retrieve
from app.agents.web_research import FindingItem, research
from app.services.source_registry import SourceRegistry

logger = logging.getLogger(__name__)


class ContextResult(BaseModel):
    """Aggregated context containing citable sources and structured findings."""

    brief: str
    sources_map: dict[int, dict[str, Any]] = Field(
        default_factory=dict, description="Map of int citation ID to source metadata dict."
    )
    sources_list: list[dict[str, Any]] = Field(
        default_factory=list, description="List of source dict objects."
    )
    findings: list[FindingItem] = Field(
        default_factory=list, description="List of research findings with source IDs."
    )
    kb_hits: list[dict[str, Any]] = Field(
        default_factory=list, description="List of retrieved KB chunks."
    )
    queries: list[str] = Field(
        default_factory=list, description="Search queries executed."
    )


def build_context(
    brief: str,
    use_web: bool = True,
    use_kb: bool = True,
    db_session: Session | None = None,
    trace_id: str | None = None,
) -> ContextResult:
    """Run research and retrieval, aggregating results into a ContextResult.

    Args:
        brief: Client prompt, topic, or document brief.
        use_web: Whether to execute web research.
        use_kb: Whether to execute enterprise KB retrieval.
        db_session: Optional DB session for storing sources/traces.
        trace_id: Optional trace grouping ID.

    Returns:
        ContextResult with sources_map, findings, kb_hits, and queries.
    """
    registry = SourceRegistry(db_session=db_session)
    findings: list[FindingItem] = []
    kb_hits: list[dict[str, Any]] = []
    queries: list[str] = []

    # 1. Enterprise KB Retrieval
    if use_kb:
        try:
            kb_hits = retrieve(
                query=brief,
                top_k=5,
                min_score=0.3,
                registry=registry,
                db_session=db_session,
                trace_id=trace_id,
            )
        except Exception as exc:
            logger.warning(f"KB retrieval failed in context_builder: {exc}")

    # 2. Web Research
    if use_web:
        try:
            res_result = research(
                topic=brief,
                registry=registry,
                db_session=db_session,
                trace_id=trace_id,
            )
            findings = res_result.findings
            queries = res_result.queries
        except Exception as exc:
            logger.warning(f"Web research failed in context_builder: {exc}")

    sources_map = registry.export_sources()
    sources_list = registry.export_sources_list()

    return ContextResult(
        brief=brief,
        sources_map=sources_map,
        sources_list=sources_list,
        findings=findings,
        kb_hits=kb_hits,
        queries=queries,
    )
