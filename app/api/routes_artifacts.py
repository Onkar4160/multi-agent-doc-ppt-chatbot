"""Artifact generation, versioning, and download API router."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.agents.doc_analyzer import analyze_document
from app.agents.doc_generator import generate_document_model
from app.agents.ppt_analyzer import analyze_presentation
from app.agents.ppt_generator import generate_deck_model
from app.models.file import UploadedFile
from app.models.template_profile import TemplateProfileRecord
from app.models.trace import AgentTrace
from app.services.context_builder import build_context
from app.services.docx_renderer import render_docx
from app.services.pptx_renderer import render_pptx
from app.services.versioning import (
    add_version,
    create_artifact,
    get_version,
    list_artifacts,
    list_versions,
)

router = APIRouter(prefix="/artifacts", tags=["artifacts"])

DEFAULT_DOCX_TEMPLATE = Path("data/sample_templates/Company_Proposal.docx")
DEFAULT_PPTX_TEMPLATE = Path("data/sample_templates/Company_Template.pptx")


class GenerateArtifactRequest(BaseModel):
    """Payload for generating document and presentation artifacts."""
    brief: str
    kind: Literal["docx", "pptx", "both"] = "docx"
    doc_template_file_id: int | None = None
    ppt_template_file_id: int | None = None
    slide_count: int = 12
    sources: list[dict[str, Any]] = Field(default_factory=list)
    use_web: bool = True
    use_kb: bool = True


@router.post("/generate", status_code=status.HTTP_201_CREATED)
async def generate_artifact(
    body: GenerateArtifactRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Generate structured DOCX document and/or PPTX presentation from a brief."""
    results: list[dict[str, Any]] = []
    sources_list = list(body.sources)
    sources_map = {s.get("id", idx + 1): s for idx, s in enumerate(sources_list)}

    if (body.use_web or body.use_kb) and not sources_list:
        ctx = build_context(body.brief, use_web=body.use_web, use_kb=body.use_kb)
        if ctx.sources_list:
            sources_list = ctx.sources_list
            sources_map = ctx.sources_map

    # Handle DOCX generation
    if body.kind in ("docx", "both"):
        doc_path, doc_profile = await _resolve_template_path_and_profile(
            db, body.doc_template_file_id, default_path=DEFAULT_DOCX_TEMPLATE, is_pptx=False
        )

        start_t = time.perf_counter()
        doc_model = generate_document_model(body.brief, doc_profile, sources=sources_list)
        temp_out = Path("storage/outputs") / f"temp_{uuid.uuid4()}.docx"
        render_docx(doc_model, doc_path, doc_profile, temp_out, sources_map=sources_map)
        duration_ms = int((time.perf_counter() - start_t) * 1000)

        # Collect used source IDs
        used_sids = [sid for sec in doc_model.sections for b in sec.blocks for sid in getattr(b, "source_ids", [])]

        artifact = await create_artifact(db, "docx", doc_model.title)
        ver_rec = await add_version(
            db,
            artifact_id=artifact.id,
            model_json=doc_model.model_dump_json(),
            source_file_path=temp_out,
            change_summary="Initial DOCX generation",
            source_ids=used_sids,
        )

        # Cleanup temp file
        if temp_out.exists():
            temp_out.unlink()

        # Trace
        db.add(AgentTrace(
            trace_id=str(uuid.uuid4()),
            agent_name="doc_generator",
            input_summary=f"Generate DOCX for brief: '{body.brief[:100]}'",
            output_summary=f"Generated Artifact {artifact.id} v{ver_rec.version_no}: '{doc_model.title}'",
            duration_ms=duration_ms,
            status="ok",
        ))
        await db.flush()

        results.append({
            "artifact_id": artifact.id,
            "kind": "docx",
            "title": artifact.title,
            "version_no": ver_rec.version_no,
            "download_url": f"/artifacts/{artifact.id}/download?version={ver_rec.version_no}",
        })

    # Handle PPTX generation
    if body.kind in ("pptx", "both"):
        ppt_path, ppt_profile = await _resolve_template_path_and_profile(
            db, body.ppt_template_file_id, default_path=DEFAULT_PPTX_TEMPLATE, is_pptx=True
        )

        start_t = time.perf_counter()
        deck_model = generate_deck_model(body.brief, ppt_profile, sources=sources_list, slide_count=body.slide_count)
        temp_out = Path("storage/outputs") / f"temp_{uuid.uuid4()}.pptx"
        render_pptx(deck_model, ppt_path, ppt_profile, temp_out, sources_map=sources_map)
        duration_ms = int((time.perf_counter() - start_t) * 1000)

        used_sids = [sid for sl in deck_model.slides for sid in sl.source_ids]

        artifact = await create_artifact(db, "pptx", deck_model.title)
        ver_rec = await add_version(
            db,
            artifact_id=artifact.id,
            model_json=deck_model.model_dump_json(),
            source_file_path=temp_out,
            change_summary="Initial PPTX generation",
            source_ids=used_sids,
        )

        if temp_out.exists():
            temp_out.unlink()

        db.add(AgentTrace(
            trace_id=str(uuid.uuid4()),
            agent_name="ppt_generator",
            input_summary=f"Generate PPTX ({body.slide_count} slides) for brief: '{body.brief[:100]}'",
            output_summary=f"Generated Artifact {artifact.id} v{ver_rec.version_no}: '{deck_model.title}'",
            duration_ms=duration_ms,
            status="ok",
        ))
        await db.flush()

        results.append({
            "artifact_id": artifact.id,
            "kind": "pptx",
            "title": artifact.title,
            "version_no": ver_rec.version_no,
            "download_url": f"/artifacts/{artifact.id}/download?version={ver_rec.version_no}",
        })

    return {"artifacts": results}


@router.get("", response_model=list[dict])
async def get_artifacts(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """List all generated artifacts."""
    artifacts = await list_artifacts(db)
    output = []
    for a in artifacts:
        latest = await get_version(db, a.id)
        output.append({
            "id": a.id,
            "title": a.title,
            "artifact_type": a.artifact_type,
            "latest_version": latest.version_no if latest else 1,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })
    return output


@router.get("/{id}/versions", response_model=list[dict])
async def get_artifact_versions(
    id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """List version history for a specific artifact."""
    versions = await list_versions(db, id)
    output = []
    for v in versions:
        diff_val = json.loads(v.diff_json) if getattr(v, "diff_json", None) else []
        output.append({
            "version_no": v.version_no,
            "file_type": v.file_type,
            "change_summary": v.change_summary,
            "diff": diff_val,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "download_url": f"/artifacts/{id}/download?version={v.version_no}",
        })
    return output


@router.get("/{id}/versions/{n}")
async def get_artifact_version_detail(
    id: int,
    n: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get detailed metadata and model snapshot for a specific version."""
    ver = await get_version(db, id, version_no=n)
    if not ver:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version {n} for artifact {id} not found",
        )

    diff_val = json.loads(ver.diff_json) if ver.diff_json else []
    sources_val = json.loads(ver.source_ids_json) if ver.source_ids_json else []
    model_obj = json.loads(ver.model_json) if ver.model_json else {}

    return {
        "artifact_id": id,
        "version_no": ver.version_no,
        "file_type": ver.file_type,
        "change_summary": ver.change_summary,
        "diff": diff_val,
        "source_ids": sources_val,
        "created_at": ver.created_at.isoformat() if ver.created_at else None,
        "download_url": f"/artifacts/{id}/download?version={ver.version_no}",
        "model_json": model_obj,
    }


class EditArtifactRequest(BaseModel):
    instruction: str
    base_version: int | None = None


@router.post("/{id}/edit")
async def edit_artifact_endpoint(
    id: int,
    body: EditArtifactRequest,
    user=Depends(get_current_user),
):
    """Conversational edit on an existing artifact."""
    from app.agents.editor import edit_artifact
    from app.core.database import get_sync_session

    sync_db = get_sync_session()
    try:
        res = edit_artifact(
            artifact_id=id,
            instruction=body.instruction,
            base_version=body.base_version,
            db_session=sync_db,
        )
        return res.model_dump()
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        sync_db.close()


class RevertArtifactRequest(BaseModel):
    version: int


@router.post("/{id}/revert")
async def revert_artifact_endpoint(
    id: int,
    body: RevertArtifactRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Revert artifact to an older version by creating a NEW version snapshot."""
    target_ver = await get_version(db, id, version_no=body.version)
    if not target_ver:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version {body.version} for artifact {id} not found",
        )

    latest_ver = await get_version(db, id)
    parent_id = latest_ver.id if latest_ver else None

    # Save as a NEW version
    new_ver = await add_version(
        db,
        artifact_id=id,
        model_json=target_ver.model_json,
        source_file_path=target_ver.file_path,
        change_summary=f"Reverted to version {body.version}",
        source_ids=json.loads(target_ver.source_ids_json) if target_ver.source_ids_json else [],
        parent_version_id=parent_id,
        diff_json={"action": "revert", "target_version": body.version},
    )

    return {
        "artifact_id": id,
        "new_version_no": new_ver.version_no,
        "reverted_from_version": body.version,
        "change_summary": new_ver.change_summary,
        "download_url": f"/artifacts/{id}/download?version={new_ver.version_no}",
    }


class ConvertArtifactRequest(BaseModel):
    target_kind: Literal["docx", "pptx"]
    slide_count: int = 10


@router.post("/{id}/convert")
async def convert_artifact_endpoint(
    id: int,
    body: ConvertArtifactRequest,
    user=Depends(get_current_user),
):
    """Convert artifact (docx -> pptx or pptx -> docx) into a new artifact."""
    from app.agents.converter import convert_artifact
    from app.core.database import get_sync_session

    sync_db = get_sync_session()
    try:
        res = convert_artifact(
            artifact_id=id,
            target_kind=body.target_kind,
            slide_count=body.slide_count,
            db_session=sync_db,
        )
        return res.model_dump()
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        sync_db.close()


@router.get("/{id}/download")
async def download_artifact_version(
    id: int,
    version: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Download the rendered DOCX or PPTX file for an artifact version."""
    ver_rec = await get_version(db, id, version_no=version)
    if not ver_rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version {version or 'latest'} for artifact_id {id} not found",
        )

    file_path = Path(ver_rec.file_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact file missing on disk at '{ver_rec.file_path}'",
        )

    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if ver_rec.file_type == "docx"
        else "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )

    filename = f"artifact_{id}_v{ver_rec.version_no}.{ver_rec.file_type}"
    return FileResponse(path=file_path, filename=filename, media_type=media_type)



# ── Helper for Resolving Template & Profile ────────────────────────────────

async def _resolve_template_path_and_profile(
    db: AsyncSession,
    file_id: int | None,
    default_path: Path,
    is_pptx: bool,
) -> tuple[Path, Any]:
    """Resolve template disk path and TemplateProfile."""
    if file_id is not None:
        res = await db.execute(select(UploadedFile).where(UploadedFile.id == file_id))
        f_row = res.scalar_one_or_none()
        if f_row and Path(f_row.stored_path).exists():
            tmpl_path = Path(f_row.stored_path)
            # Try loading profile from DB
            prof_res = await db.execute(
                select(TemplateProfileRecord)
                .where(TemplateProfileRecord.file_id == file_id)
                .order_by(TemplateProfileRecord.id.desc())
            )
            p_rec = prof_res.scalar_one_or_none()
            if p_rec:
                from app.models.template_profile import TemplateProfile
                profile = TemplateProfile.model_validate_json(p_rec.profile_json)
                return tmpl_path, profile
            else:
                # Analyze on the fly
                profile = analyze_presentation(tmpl_path, file_id) if is_pptx else analyze_document(tmpl_path, file_id)
                return tmpl_path, profile

    # Fallback to default template
    tmpl_path = default_path
    if not tmpl_path.exists():
        raise FileNotFoundError(f"Default template missing at '{default_path}'")

    profile = analyze_presentation(tmpl_path) if is_pptx else analyze_document(tmpl_path)
    return tmpl_path, profile
