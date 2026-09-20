"""Upload endpoint – accepts templates, analyses them, stores profiles."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.config import get_settings
from app.models.template_profile import TemplateProfile
from app.services.file_utils import validate_upload, save_upload

router = APIRouter(prefix="/api", tags=["upload"])


@router.post("/upload")
async def upload_template(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Upload a template file, analyse it, and store the style profile."""
    settings = get_settings()
    ext = validate_upload(file, max_mb=settings.upload_max_mb)
    saved_path = await save_upload(file)

    # Analyse based on file type
    profile_schema = _analyse(ext, str(saved_path))

    # Persist
    tp = TemplateProfile(
        project_id=project_id,
        filename=file.filename or "unknown",
        file_type=ext.lstrip("."),
        profile_json=profile_schema.model_dump_json(),
        template_path=str(saved_path),
    )
    db.add(tp)
    await db.flush()

    return {
        "id": tp.id,
        "filename": tp.filename,
        "file_type": tp.file_type,
        "profile": json.loads(tp.profile_json),
        "template_path": tp.template_path,
    }


def _analyse(ext: str, path: str):
    """Route to the correct analyzer based on extension."""
    if ext == ".docx":
        from app.services.analyzers.docx_analyzer import analyze_docx
        return analyze_docx(path)
    elif ext == ".pptx":
        from app.services.analyzers.pptx_analyzer import analyze_pptx
        return analyze_pptx(path)
    elif ext == ".pdf":
        from app.services.analyzers.pdf_analyzer import analyze_pdf
        return analyze_pdf(path)
    else:
        # Image types
        from app.services.analyzers.image_analyzer import analyze_image
        return analyze_image(path)
