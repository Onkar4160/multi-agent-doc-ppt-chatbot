"""Context builder service aggregating web research and KB retrieval into citation sources and findings."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.rag_agent import retrieve
from app.agents.web_research import FindingItem, research
from app.services.evidence_pack import EvidenceItem, build_evidence_pack
from app.models.evidence import ResearchDossier
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
    file_hits: list[dict[str, Any]] = Field(
        default_factory=list, description="List of chunks extracted from attached files."
    )
    evidence_pack: list[EvidenceItem] = Field(
        default_factory=list, description="List of grounded evidence pack items."
    )
    queries: list[str] = Field(
        default_factory=list, description="Search queries executed."
    )
    dossier: ResearchDossier | None = Field(default=None, description="Research dossier (Stage 5).")



def build_context(
    brief: str,
    use_web: bool = True,
    use_kb: bool = True,
    file_ids: list[int] | None = None,
    db_session: Session | None = None,
    trace_id: str | None = None,
    start_id: int = 1,
    depth: str = "standard",
    reuse_dossier: ResearchDossier | None = None,
) -> ContextResult:
    """Retrieval, attached files and web/scholarly research turned into a verified evidence dossier.

    ``depth`` (none/light/standard/deep) comes from the interpretation; ``reuse_dossier`` skips new research for
    follow-ups on the same task (FR-RES-11). Citation numbers are source IDs.
    """
    if reuse_dossier is not None:
        return _from_dossier(brief, reuse_dossier)
    registry = SourceRegistry(db_session=db_session, start_id=start_id)
    findings: list[FindingItem] = []
    kb_hits: list[dict[str, Any]] = []
    file_hits: list[dict[str, Any]] = []
    queries: list[str] = []

    # 1. Attached Files Ingestion (Source / Reference documents)
    if file_ids and db_session:
        try:
            from pathlib import Path
            from app.models.file import UploadedFile
            from app.services.parsers import parse_file

            up_files = db_session.query(UploadedFile).filter(UploadedFile.id.in_(file_ids)).all()
            for uf in up_files:
                p = Path(uf.stored_path)
                if p.exists():
                    try:
                        parsed = parse_file(p)
                        for idx, blk in enumerate(parsed.blocks):
                            txt = (blk.text or "").strip()
                            if len(txt) > 20:
                                sid = registry.add(
                                    kind="uploaded_file",
                                    title=uf.filename,
                                    url_or_path=str(p),
                                    snippet=txt[:300],
                                    score=0.98,
                                )
                                file_hits.append({
                                    "text": txt,
                                    "source_id": sid,
                                    "filename": uf.filename,
                                    "file_id": uf.id,
                                    "path": str(p),
                                    "location": f"{uf.filename}, block {idx + 1}",
                                })
                    except Exception as parse_exc:
                        logger.warning(f"Failed to parse attached source file {uf.filename}: {parse_exc}")
        except Exception as f_exc:
            logger.warning(f"Attached file retrieval failed in context_builder: {f_exc}")

    # 2. Enterprise KB Retrieval
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

    # 3. Research: plan, web + scholarly search, page reading, evidence extraction and verification
    dossier: ResearchDossier | None = None
    if use_web or kb_hits or file_hits:
        from app.agents.research_pipeline import run_research
        from app.llm.client import get_llm_client

        try:
            dossier = run_research(
                brief, depth if use_web else "none", registry,
                llm=get_llm_client(), file_hits=file_hits, kb_hits=kb_hits, use_web=use_web and depth != "none",
            )
            queries = [q.question for q in dossier.plan.questions]
        except Exception as exc:
            if exc.__class__.__name__ in ("BudgetExceeded", "LLMAuthError"):
                raise
            logger.warning(f"Research pipeline failed in context_builder: {exc}")
    if dossier is not None:
        return _from_dossier(brief, dossier, registry.export_sources(), kb_hits=kb_hits, file_hits=file_hits)

    sources_map = registry.export_sources()
    sources_list = registry.export_sources_list()

    # 4. Build grounded Evidence Pack from attached files, findings, and KB chunks
    evidence_pack = build_evidence_pack(
        findings=findings,
        kb_hits=kb_hits,
        file_hits=file_hits,
        start_id=start_id,
    )
    # Citation numbers are source IDs: the pack cites each piece by the source it came from.
    evidence_pack = [EvidenceItem(id=item.source_id, text=item.text, source_id=item.source_id)
                     for item in evidence_pack if item.source_id in sources_map]

    return ContextResult(
        brief=brief,
        sources_map=sources_map,
        sources_list=sources_list,
        findings=findings,
        kb_hits=kb_hits,
        file_hits=file_hits,
        evidence_pack=evidence_pack,
        queries=queries,
    )


def _from_dossier(
    brief: str,
    dossier: ResearchDossier,
    registry_sources: dict[int, dict[str, Any]] | None = None,
    kb_hits: list[dict[str, Any]] | None = None,
    file_hits: list[dict[str, Any]] | None = None,
) -> ContextResult:
    """Writer-facing context from a dossier: evidence lines cited by source ID, then limitations."""
    import datetime

    from app.agents.research_pipeline import evidence_lines, limitations

    accessed = datetime.date.today().strftime("%B %Y")
    sources_map: dict[int, dict[str, Any]] = {}
    for s in dossier.sources:
        base = (registry_sources or {}).get(s.id, {})
        sources_map[s.id] = {
            **base,
            "id": s.id,
            "kind": {"user_file": "kb", "kb": "kb"}.get(s.kind, "web"),
            "title": s.title,
            "url": s.url or base.get("url", ""),
            "url_or_filename": s.url or base.get("url_or_filename") or s.title,
            "accessed_at": base.get("accessed_at", accessed),
            "published_date": s.published_at,
            "authority": s.authority,
        }
    pack = [EvidenceItem(id=sid, text=line, source_id=sid) for sid, line in evidence_lines(dossier)]
    pack += [EvidenceItem(id=0, text=f"LIMITATION: {note}", source_id=0) for note in limitations(dossier)]
    findings = [
        FindingItem(text=c.statement, source_ids=sorted({e.source_id for e in dossier.evidence if e.id in c.evidence_ids}))
        for c in dossier.claims if c.status != "unsupported"
    ]
    return ContextResult(
        brief=brief,
        sources_map=sources_map,
        sources_list=list(sources_map.values()),
        findings=findings,
        kb_hits=kb_hits or [],
        file_hits=file_hits or [],
        evidence_pack=pack,
        queries=[q.question for q in dossier.plan.questions],
        dossier=dossier,
    )

