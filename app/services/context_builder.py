"""Context builder service aggregating web research and KB retrieval into citation sources and findings."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.rag_agent import retrieve
from app.agents.web_research import FindingItem, research
from app.services.evidence_pack import EvidenceItem, build_evidence_pack
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
    evidence_pack: list[EvidenceItem] = Field(
        default_factory=list, description="List of grounded evidence pack items."
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
    start_id: int = 1,
) -> ContextResult:
    """Run research and retrieval, aggregating results into a ContextResult.

    Args:
        brief: Client prompt, topic, or document brief.
        use_web: Whether to execute web research.
        use_kb: Whether to execute enterprise KB retrieval.
        db_session: Optional DB session for storing sources/traces.
        trace_id: Optional trace grouping ID.
        start_id: Citation ID offset for SourceRegistry.

    Returns:
        ContextResult with sources_map, findings, kb_hits, and queries.
    """
    registry = SourceRegistry(db_session=db_session, start_id=start_id)
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

    # 3. Build grounded Evidence Pack from findings and KB chunks
    evidence_pack = build_evidence_pack(findings=findings, kb_hits=kb_hits, start_id=start_id)
    import datetime
    today_str = datetime.date.today().strftime("%B %Y")
    for item in evidence_pack:
        if item.id not in sources_map:
            base_info = sources_map.get(item.source_id, {})
            u_or_f = base_info.get("url_or_filename") or base_info.get("url", "")
            sources_map[item.id] = {
                "id": item.id,
                "title": base_info.get("title", f"Fact [{item.id}]"),
                "url": base_info.get("url", ""),
                "url_or_filename": u_or_f,
                "kind": base_info.get("kind", "web"),
                "accessed_at": base_info.get("accessed_at", today_str),
                "snippet": item.text,
            }

    return ContextResult(
        brief=brief,
        sources_map=sources_map,
        sources_list=sources_list,
        findings=findings,
        kb_hits=kb_hits,
        evidence_pack=evidence_pack,
        queries=queries,
    )

