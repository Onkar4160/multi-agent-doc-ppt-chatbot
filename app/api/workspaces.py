"""Workspace API endpoints – persistent per-company template profile and logo extraction."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.doc_analyzer import analyze_document
from app.agents.ppt_analyzer import analyze_presentation
from app.api.deps import get_current_user, get_db
from app.core.config import get_settings
from app.models.file import UploadedFile
from app.models.workspace import Workspace

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class CreateWorkspaceRequest(BaseModel):
    """Payload to create a persistent workspace."""

    name: str = Field(description="Name of the workspace / company.")
    docx_template_file_id: int | None = Field(
        default=None, description="Uploaded DOCX template file ID."
    )
    ppt_template_file_id: int | None = Field(
        default=None, description="Uploaded PPTX template file ID."
    )
    pptx_template_file_id: int | None = Field(
        default=None, description="Alias for PPTX template file ID."
    )

    model_config = {"populate_by_name": True}


class WorkspaceResponse(BaseModel):
    """Serialized workspace response."""

    id: int
    name: str
    docx_template_file_id: int | None = None
    pptx_template_file_id: int | None = None
    docx_filename: str | None = None
    pptx_filename: str | None = None
    doc_profile_json: str | None = None
    ppt_profile_json: str | None = None
    logo_path: str | None = None
    is_active: bool = False
    created_at: str | None = None
    updated_at: str | None = None


def _extract_logo_from_templates(
    docx_path: Path | None,
    pptx_path: Path | None,
    output_dir: Path,
) -> str | None:
    """Extract first small embedded image near header or title placeholder, not full-page background."""
    # 1. Try DOCX template
    if docx_path and Path(docx_path).exists():
        try:
            import docx

            doc = docx.Document(str(docx_path))
            candidates: list[bytes] = []

            # Headers first
            for section in doc.sections:
                for rel_id, part in section.header.part.related_parts.items():
                    if getattr(part, "content_type", "").startswith("image/"):
                        candidates.append(part.blob)

            # Body related parts
            for rel_id, part in doc.part.related_parts.items():
                if getattr(part, "content_type", "").startswith("image/"):
                    candidates.append(part.blob)

            for blob in candidates:
                try:
                    img = Image.open(io.BytesIO(blob))
                    w, h = img.size
                    is_bg = (w >= 1000 and h >= 700) or (w >= 1800)
                    if not is_bg and (w <= 800 or h <= 400 or (w * h <= 320000)):
                        ext = img.format.lower() if img.format else "png"
                        if ext == "jpeg":
                            ext = "jpg"
                        output_dir.mkdir(parents=True, exist_ok=True)
                        dest = output_dir / f"logo.{ext}"
                        dest.write_bytes(blob)
                        return str(dest)
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("Error checking DOCX for logo: %s", exc)

    # 2. Try PPTX template
    if pptx_path and Path(pptx_path).exists():
        try:
            from pptx import Presentation

            prs = Presentation(str(pptx_path))
            ppt_candidates: list[tuple[bytes, str]] = []

            for slide in prs.slides:
                for shape in slide.shapes:
                    if hasattr(shape, "image"):
                        w = getattr(shape, "width", 0) or 0
                        is_full = bool(prs.slide_width and w >= prs.slide_width * 0.8)
                        if not is_full:
                            ppt_candidates.append(
                                (shape.image.blob, shape.image.ext)
                            )

            for master in prs.slide_masters:
                for shape in master.shapes:
                    if hasattr(shape, "image"):
                        ppt_candidates.append((shape.image.blob, shape.image.ext))

            for blob, ext in ppt_candidates:
                try:
                    img = Image.open(io.BytesIO(blob))
                    w, h = img.size
                    is_bg = (w >= 1000 and h >= 700) or (w >= 1800)
                    if not is_bg and (w <= 800 or h <= 400 or (w * h <= 320000)):
                        out_ext = ext or (img.format.lower() if img.format else "png")
                        if out_ext == "jpeg":
                            out_ext = "jpg"
                        output_dir.mkdir(parents=True, exist_ok=True)
                        dest = output_dir / f"logo.{out_ext}"
                        dest.write_bytes(blob)
                        return str(dest)
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("Error checking PPTX for logo: %s", exc)

    return None


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: CreateWorkspaceRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Create persistent workspace, analyze templates ONCE, extract logo, and save profiles."""
    settings = get_settings()
    ppt_id = body.ppt_template_file_id or body.pptx_template_file_id
    docx_id = body.docx_template_file_id

    docx_path: Path | None = None
    pptx_path: Path | None = None

    if docx_id:
        res = await db.execute(select(UploadedFile).where(UploadedFile.id == docx_id))
        doc_file = res.scalar_one_or_none()
        if doc_file:
            docx_path = Path(doc_file.stored_path)

    if ppt_id:
        res = await db.execute(select(UploadedFile).where(UploadedFile.id == ppt_id))
        ppt_file = res.scalar_one_or_none()
        if ppt_file:
            pptx_path = Path(ppt_file.stored_path)

    # 1. Run analyzers ONCE
    doc_profile_json = None
    ppt_profile_json = None

    if docx_path and docx_path.exists():
        doc_prof = analyze_document(docx_path, file_id=docx_id)
        doc_profile_json = doc_prof.model_dump_json()

    if pptx_path and pptx_path.exists():
        ppt_prof = analyze_presentation(pptx_path, file_id=ppt_id)
        ppt_profile_json = ppt_prof.model_dump_json()

    # 2. Create Workspace row
    ws = Workspace(
        name=body.name,
        docx_template_file_id=docx_id,
        pptx_template_file_id=ppt_id,
        doc_profile_json=doc_profile_json,
        ppt_profile_json=ppt_profile_json,
        is_active=False,
    )
    db.add(ws)
    await db.flush()

    # 3. Extract logo and save to storage/workspaces/{id}/logo.*
    ws_dir = Path("storage/workspaces") / str(ws.id)
    logo_path = _extract_logo_from_templates(docx_path, pptx_path, ws_dir)
    ws.logo_path = logo_path
    await db.flush()

    return await _build_workspace_response(db, ws)


async def _build_workspace_response(db: AsyncSession, ws: Workspace) -> WorkspaceResponse:
    """Helper to convert Workspace ORM model into WorkspaceResponse with template filenames."""
    docx_fn = None
    pptx_fn = None
    file_ids = [fid for fid in (ws.docx_template_file_id, ws.pptx_template_file_id) if fid]
    if file_ids:
        f_res = await db.execute(select(UploadedFile).where(UploadedFile.id.in_(file_ids)))
        files_map = {f.id: f.filename for f in f_res.scalars().all()}
        docx_fn = files_map.get(ws.docx_template_file_id)
        pptx_fn = files_map.get(ws.pptx_template_file_id)

    return WorkspaceResponse(
        id=ws.id,
        name=ws.name,
        docx_template_file_id=ws.docx_template_file_id,
        pptx_template_file_id=ws.pptx_template_file_id,
        docx_filename=docx_fn,
        pptx_filename=pptx_fn,
        doc_profile_json=ws.doc_profile_json,
        ppt_profile_json=ws.ppt_profile_json,
        logo_path=ws.logo_path,
        is_active=ws.is_active,
        created_at=ws.created_at.isoformat() if ws.created_at else None,
        updated_at=ws.updated_at.isoformat() if ws.updated_at else None,
    )


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """List all configured workspaces."""
    res = await db.execute(select(Workspace).order_by(Workspace.id.desc()))
    items = res.scalars().all()
    return [await _build_workspace_response(db, w) for w in items]


@router.post("/{workspace_id}/activate", response_model=WorkspaceResponse)
async def activate_workspace(
    workspace_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Set is_active True for the target workspace and False for all others."""
    res = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    target = res.scalar_one_or_none()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace with id {workspace_id} not found",
        )

    await db.execute(update(Workspace).values(is_active=False))
    target.is_active = True
    await db.flush()

    return await _build_workspace_response(db, target)


@router.get("/active", response_model=WorkspaceResponse)
async def get_active_workspace(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Retrieve the currently active workspace."""
    res = await db.execute(select(Workspace).where(Workspace.is_active == True))
    active = res.scalar_one_or_none()
    if not active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active workspace found",
        )

    return await _build_workspace_response(db, active)

