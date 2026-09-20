"""High-level orchestrator: runs the agent pipeline, renders files, creates artifact versions."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph import compile_graph
from app.llm.schemas import DeckModel, DocumentModel, TemplateProfileSchema
from app.models.artifact import ArtifactVersion
from app.models.source import Source
from app.models.trace import AgentTrace
from app.services.renderers.docx_renderer import render_docx
from app.services.renderers.pptx_renderer import render_pptx

logger = logging.getLogger(__name__)

ARTIFACTS_DIR = Path("storage/artifacts")


async def run_pipeline(
    user_request: str,
    project_id: int,
    db: AsyncSession,
    *,
    output_type: str = "docx",
    template_profile: dict | None = None,
    template_path: str | None = None,
    edit_instructions: list[dict] | None = None,
    existing_model: dict | None = None,
    parent_version_id: int | None = None,
) -> dict:
    """Run the full agent pipeline and return the artifact version info."""
    trace_id = uuid.uuid4().hex[:12]
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Build initial state ──────────────────────────────
    initial_state = {
        "messages": [HumanMessage(content=user_request)],
        "user_request": user_request,
        "project_id": project_id,
        "output_type": output_type,
        "template_profile": template_profile,
        "template_path": template_path,
        "research_results": [],
        "kb_results": [],
        "document_model": existing_model,
        "edit_instructions": edit_instructions,
        "sources": [],
        "artifact_version_id": None,
        "next_step": "",
        "trace_id": trace_id,
    }

    # ── Run the graph ────────────────────────────────────
    graph = compile_graph()
    final_state = graph.invoke(initial_state)

    # ── Extract results ──────────────────────────────────
    model_dict = final_state.get("document_model")
    if not model_dict:
        return {"error": "No document model was generated", "trace_id": trace_id}

    # ── Create sources in DB ─────────────────────────────
    source_ids = []
    for r in final_state.get("research_results", []):
        src = Source(
            project_id=project_id,
            kind="web",
            url=r.get("source_url", ""),
            title=r.get("source_title", ""),
            chunk_text=r.get("text", "")[:500],
        )
        db.add(src)
        await db.flush()
        source_ids.append(src.id)

    for r in final_state.get("kb_results", []):
        src = Source(
            project_id=project_id,
            kind="kb",
            title=r.get("source_filename", ""),
            chunk_text=r.get("text", "")[:500],
        )
        db.add(src)
        await db.flush()
        source_ids.append(src.id)

    # ── Render file ──────────────────────────────────────
    profile = TemplateProfileSchema(**(template_profile or {}))

    if output_type == "pptx":
        deck = DeckModel(**model_dict)
        file_bytes = render_pptx(deck, profile, template_path)
        ext = "pptx"
    else:
        doc = DocumentModel(**model_dict)
        file_bytes = render_docx(doc, profile, template_path)
        ext = "docx"

    # ── Save file ────────────────────────────────────────
    file_name = f"{project_id}_{trace_id}.{ext}"
    file_path = ARTIFACTS_DIR / file_name
    file_path.write_bytes(file_bytes)

    # ── Compute version number ───────────────────────────
    from sqlalchemy import func, select
    result = await db.execute(
        select(func.max(ArtifactVersion.version_no)).where(
            ArtifactVersion.project_id == project_id
        )
    )
    max_ver = result.scalar() or 0
    version_no = max_ver + 1

    # ── Create artifact version ──────────────────────────
    av = ArtifactVersion(
        project_id=project_id,
        version_no=version_no,
        model_json=json.dumps(model_dict),
        file_path=str(file_path),
        file_type=ext,
        parent_version_id=parent_version_id,
        change_summary=f"{'Edited' if edit_instructions else 'Generated'}: {user_request[:200]}",
        source_ids_json=json.dumps(source_ids),
    )
    db.add(av)
    await db.flush()

    # ── Write agent traces ───────────────────────────────
    trace = AgentTrace(
        project_id=project_id,
        trace_id=trace_id,
        agent_name="pipeline",
        input_summary=user_request[:500],
        output_summary=f"v{version_no} {ext} generated, {len(source_ids)} sources",
        status="ok",
    )
    db.add(trace)

    logger.info("Pipeline complete: v%d %s (%s)", version_no, ext, file_path)

    return {
        "artifact_version_id": av.id,
        "version_no": version_no,
        "file_type": ext,
        "file_path": str(file_path),
        "download_url": f"/api/artifacts/{project_id}/versions/{av.id}/download",
        "sources_count": len(source_ids),
        "trace_id": trace_id,
    }
