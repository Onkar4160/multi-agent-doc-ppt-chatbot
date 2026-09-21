"""File validation, safe naming, and upload helpers."""

import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings

ALLOWED_EXTENSIONS = {".docx", ".pptx", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"}


def get_upload_dir() -> Path:
    p = Path(get_settings().storage_dir) / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_filename(name: str) -> str:
    """Sanitise a filename: keep extension, replace unsafe chars, add UUID prefix."""
    stem = Path(name).stem
    suffix = Path(name).suffix.lower()
    clean = re.sub(r"[^\w\-.]", "_", stem)[:100]
    return f"{uuid.uuid4().hex[:8]}_{clean}{suffix}"


def validate_upload(file: UploadFile, max_mb: int = 25) -> str:
    """Validate file type and size; return the lowercase extension."""
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No filename provided")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"File type {ext} not allowed")
    if file.size and file.size > max_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"File exceeds {max_mb} MB limit")
    return ext


async def save_upload(file: UploadFile, dest_dir: Path | None = None) -> Path:
    """Save an uploaded file to disk and return the path."""
    dest = dest_dir or get_upload_dir()
    dest.mkdir(parents=True, exist_ok=True)
    name = safe_filename(file.filename or "unknown")
    path = dest / name
    content = await file.read()
    path.write_bytes(content)
    return path
