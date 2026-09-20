"""File management API endpoints: upload and list files."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.config import get_settings
from app.models.file import UploadedFile

router = APIRouter(prefix="/files", tags=["files"])

ALLOWED_EXTENSIONS = {".docx", ".pdf", ".pptx", ".png", ".jpg", ".jpeg"}


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Upload a document, presentation, or image template file.

    Requires JWT auth. Allows only .docx, .pdf, .pptx, .png, .jpg, .jpeg up to max upload limit.
    """
    settings = get_settings()

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required",
        )

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext}'. Allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Read content and check size
    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File size ({len(content)} bytes) exceeds maximum limit of {settings.max_upload_mb} MB",
        )

    # Sanitize filename & save under storage/uploads/<uuid>/
    safe_filename = Path(file.filename).name
    file_uuid = str(uuid.uuid4())
    upload_dir = Path("storage/uploads") / file_uuid
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path = upload_dir / safe_filename

    with open(saved_path, "wb") as f:
        f.write(content)

    clean_type = ext.lstrip(".")
    uploaded_file = UploadedFile(
        filename=safe_filename,
        file_type=clean_type,
        file_size=len(content),
        stored_path=str(saved_path),
    )
    db.add(uploaded_file)
    await db.flush()

    return {
        "id": uploaded_file.id,
        "filename": uploaded_file.filename,
        "file_type": uploaded_file.file_type,
        "file_size": uploaded_file.file_size,
        "stored_path": uploaded_file.stored_path,
    }


@router.get("", response_model=list[dict])
async def list_files(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """List all uploaded files."""
    result = await db.execute(select(UploadedFile).order_by(UploadedFile.id.desc()))
    files = result.scalars().all()

    return [
        {
            "id": f.id,
            "filename": f.filename,
            "file_type": f.file_type,
            "file_size": f.file_size,
            "stored_path": f.stored_path,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in files
    ]
