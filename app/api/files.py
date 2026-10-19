"""File management and analysis API endpoints: upload, list, analyze, and get profile."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.config import get_settings
from app.core.errors import UnsupportedFileError
from app.core.file_safety import ALLOWED_EXTENSIONS as FILE_SAFETY_EXTENSIONS, check_upload
from app.core.ownership import require_file, username_of, visible
from app.models.file import UploadedFile
from app.models.template_profile import TemplateProfileRecord
from app.models.trace import AgentTrace

router = APIRouter(prefix="/files", tags=["files"])

ALLOWED_EXTENSIONS = FILE_SAFETY_EXTENSIONS  # judged by content in app/core/file_safety.py


async def _apply_role(db: AsyncSession, existing: UploadedFile, purpose: str | None) -> str:
    """A re-uploaded file takes the role chosen this time (template / source / dataset); 'auto' keeps the old one."""
    chosen = (purpose or "").upper()
    if chosen and chosen != "REFERENCE" and existing.purpose != chosen:
        existing.purpose = chosen
        await db.flush()
    return existing.purpose or "REFERENCE"


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(...),
    purpose: str = "REFERENCE",
    session_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Upload a document, presentation, or image template file."""
    settings = get_settings()

    content = await file.read()
    try:
        ext = check_upload(file.filename or "", content, settings.max_upload_mb)
    except UnsupportedFileError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    content_hash = hashlib.sha256(content).hexdigest()

    safe_filename = Path(file.filename).name

    # Check for duplicate upload (same name and same size hash returns existing record)
    result = await db.execute(
        select(UploadedFile).where(
            UploadedFile.filename == safe_filename,
            UploadedFile.file_size == len(content),
            visible(UploadedFile.owner, user),
        ).order_by(UploadedFile.id.desc())
    )
    existing_candidates = result.scalars().all()
    if existing_candidates:
        for existing in existing_candidates:
            existing_path = Path(existing.stored_path)
            if existing_path.exists():
                try:
                    if hashlib.sha256(existing_path.read_bytes()).hexdigest() == content_hash:
                        return {
                            "id": existing.id,
                            "filename": existing.filename,
                            "file_type": existing.file_type,
                            "file_size": existing.file_size,
                            "stored_path": existing.stored_path,
                            "session_id": existing.session_id,
                            "purpose": await _apply_role(db, existing, purpose),
                        }
                except Exception:
                    pass
            else:
                return {
                    "id": existing.id,
                    "filename": existing.filename,
                    "file_type": existing.file_type,
                    "file_size": existing.file_size,
                    "stored_path": existing.stored_path,
                    "session_id": existing.session_id,
                    "purpose": await _apply_role(db, existing, purpose),
                }

    file_uuid = str(uuid.uuid4())
    upload_dir = Path(settings.storage_dir) / "uploads" / file_uuid
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
        session_id=session_id,
        purpose=purpose.upper() if purpose else "REFERENCE",
        owner=username_of(user),
        sha256=content_hash,
    )
    db.add(uploaded_file)
    await db.flush()

    return {
        "id": uploaded_file.id,
        "filename": uploaded_file.filename,
        "file_type": uploaded_file.file_type,
        "file_size": uploaded_file.file_size,
        "stored_path": uploaded_file.stored_path,
        "session_id": uploaded_file.session_id,
        "purpose": uploaded_file.purpose,
    }


@router.get("", response_model=list[dict])
async def list_files(
    session_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """List all uploaded files, optionally filtered by session_id."""
    stmt = select(UploadedFile).where(visible(UploadedFile.owner, user))
    if session_id:
        stmt = stmt.where(UploadedFile.session_id == session_id)
    result = await db.execute(stmt.order_by(UploadedFile.id.desc()))
    files = result.scalars().all()

    profile_result = await db.execute(select(TemplateProfileRecord.file_id).distinct())
    analyzed_ids = set(profile_result.scalars().all())

    return [
        {
            "id": f.id,
            "filename": f.filename,
            "file_type": f.file_type,
            "file_size": f.file_size,
            "stored_path": f.stored_path,
            "session_id": f.session_id,
            "purpose": f.purpose or "REFERENCE",
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "analyzed": f.id in analyzed_ids,
        }
        for f in files
    ]


@router.post("/{file_id}/analyze")
async def analyze_file(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Analyze an uploaded template file and store its TemplateProfile."""
    uploaded_file = await require_file(db, file_id, user)

    file_path = Path(uploaded_file.stored_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stored file for id {file_id} missing on disk at '{uploaded_file.stored_path}'",
        )

    start_time = time.perf_counter()
    agent_name = "doc_analyzer"

    try:
        from app.services.template_cache import analyze_template

        agent_name = {"pptx": "ppt_analyzer", "potx": "ppt_analyzer", "xlsx": "xlsx_analyzer",
                      "xltx": "xlsx_analyzer"}.get(uploaded_file.file_type, "doc_analyzer")
        profile = analyze_template(file_path, file_id=file_id)

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        profile_json = profile.model_dump_json()

        # Save profile record
        rec = TemplateProfileRecord(
            file_id=file_id,
            profile_json=profile_json,
        )
        db.add(rec)

        # Log trace
        trace = AgentTrace(
            trace_id=str(uuid.uuid4()),
            agent_name=agent_name,
            input_summary=f"Analyze file {file_id}: '{uploaded_file.filename}' ({uploaded_file.file_type})",
            output_summary=f"Successfully extracted TemplateProfile for '{uploaded_file.filename}'",
            duration_ms=duration_ms,
            status="ok",
        )
        db.add(trace)
        await db.flush()

        return profile.model_dump()

    except Exception as exc:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        trace_err = AgentTrace(
            trace_id=str(uuid.uuid4()),
            agent_name=agent_name,
            input_summary=f"Analyze file {file_id}: '{uploaded_file.filename}'",
            output_summary=f"Analysis failed: {str(exc)[:200]}",
            duration_ms=duration_ms,
            status="error",
        )
        db.add(trace_err)
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Template analysis failed: {exc}",
        ) from exc


@router.get("/{file_id}/profile")
async def get_file_profile(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Retrieve the latest stored TemplateProfile for an uploaded file."""
    await require_file(db, file_id, user)
    result = await db.execute(
        select(TemplateProfileRecord)
        .where(TemplateProfileRecord.file_id == file_id)
        .order_by(TemplateProfileRecord.id.desc())
    )
    rec = result.scalars().first()

    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No profile found for file_id {file_id}. Run POST /files/{file_id}/analyze first.",
        )

    return json.loads(rec.profile_json)
