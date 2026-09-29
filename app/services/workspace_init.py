"""Startup workspace initialization from configured template paths."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.agents.doc_analyzer import analyze_document
from app.agents.ppt_analyzer import analyze_presentation
from app.api.workspaces import _extract_logo_from_templates
from app.core.config import get_settings
from app.models.file import UploadedFile
from app.models.workspace import Workspace

logger = logging.getLogger(__name__)


async def init_default_workspace(db: AsyncSession) -> Workspace:
    """Initialize active workspace from configured template paths if not already matching.

    Fails startup with FileNotFoundError if either configured template file is missing.
    """
    settings = get_settings()
    docx_path = Path(settings.default_docx_template_path)
    pptx_path = Path(settings.default_pptx_template_path)

    # 1. Fail startup if either configured template file is missing
    if not docx_path.is_file():
        raise FileNotFoundError(
            f"Configured DEFAULT_DOCX_TEMPLATE_PATH not found: '{docx_path}'. "
            "Startup aborted. Never silently substituting a different file."
        )
    if not pptx_path.is_file():
        raise FileNotFoundError(
            f"Configured DEFAULT_PPTX_TEMPLATE_PATH not found: '{pptx_path}'. "
            "Startup aborted. Never silently substituting a different file."
        )

    resolved_docx = str(docx_path.resolve())
    resolved_pptx = str(pptx_path.resolve())

    # 2. Check if an active Workspace already exists matching these exact paths
    res = await db.execute(select(Workspace).where(Workspace.is_active == True))
    active_ws = res.scalar_one_or_none()

    if active_ws and active_ws.docx_template_file_id and active_ws.pptx_template_file_id:
        f_res = await db.execute(
            select(UploadedFile).where(
                UploadedFile.id.in_([active_ws.docx_template_file_id, active_ws.pptx_template_file_id])
            )
        )
        files = {f.id: f for f in f_res.scalars().all()}
        doc_f = files.get(active_ws.docx_template_file_id)
        ppt_f = files.get(active_ws.pptx_template_file_id)

        if (
            doc_f
            and ppt_f
            and (Path(doc_f.stored_path).resolve() == docx_path.resolve() or doc_f.stored_path in (str(docx_path), resolved_docx))
            and (Path(ppt_f.stored_path).resolve() == pptx_path.resolve() or ppt_f.stored_path in (str(pptx_path), resolved_pptx))
            and active_ws.doc_profile_json
            and active_ws.ppt_profile_json
        ):
            logger.info("Active workspace matching configured template paths already active (id=%s).", active_ws.id)
            return active_ws

    # 3. Register UploadedFile rows for both templates if not existing
    doc_f_query = await db.execute(
        select(UploadedFile).where(
            UploadedFile.stored_path.in_([str(docx_path), resolved_docx])
        )
    )
    doc_file = doc_f_query.scalars().first()
    if not doc_file:
        doc_file = UploadedFile(
            filename=docx_path.name,
            file_type="docx",
            file_size=docx_path.stat().st_size,
            stored_path=str(docx_path),
        )
        db.add(doc_file)
        await db.flush()

    ppt_f_query = await db.execute(
        select(UploadedFile).where(
            UploadedFile.stored_path.in_([str(pptx_path), resolved_pptx])
        )
    )
    ppt_file = ppt_f_query.scalars().first()
    if not ppt_file:
        ppt_file = UploadedFile(
            filename=pptx_path.name,
            file_type="pptx",
            file_size=pptx_path.stat().st_size,
            stored_path=str(pptx_path),
        )
        db.add(ppt_file)
        await db.flush()

    # 3.5 Check if an existing workspace matches these template files
    existing_ws_query = await db.execute(
        select(Workspace).where(
            Workspace.docx_template_file_id == doc_file.id,
            Workspace.pptx_template_file_id == ppt_file.id,
        )
    )
    existing_ws = existing_ws_query.scalars().first()
    if existing_ws and existing_ws.doc_profile_json and existing_ws.ppt_profile_json:
        await db.execute(update(Workspace).values(is_active=False))
        existing_ws.is_active = True
        await db.commit()
        await db.refresh(existing_ws)
        logger.info("Reactivated existing workspace matching configured template paths (id=%s).", existing_ws.id)
        return existing_ws

    # 4. Run analyze_document and analyze_presentation ONCE
    logger.info("Analyzing configured default templates once at startup...")
    doc_profile = analyze_document(docx_path, file_id=doc_file.id)
    ppt_profile = analyze_presentation(pptx_path, file_id=ppt_file.id)

    # 5. Extract logo if present
    logo_dir = Path("storage/workspaces/default")
    logo_path = _extract_logo_from_templates(docx_path, pptx_path, logo_dir)

    # 6. Deactivate all existing workspaces and activate the new workspace row
    await db.execute(update(Workspace).values(is_active=False))

    if existing_ws:
        existing_ws.doc_profile_json = doc_profile.model_dump_json()
        existing_ws.ppt_profile_json = ppt_profile.model_dump_json()
        existing_ws.logo_path = logo_path
        existing_ws.is_active = True
        ws = existing_ws
    else:
        ws = Workspace(
            name="Default Workspace",
            docx_template_file_id=doc_file.id,
            pptx_template_file_id=ppt_file.id,
            doc_profile_json=doc_profile.model_dump_json(),
            ppt_profile_json=ppt_profile.model_dump_json(),
            logo_path=logo_path,
            is_active=True,
        )
        db.add(ws)
    await db.commit()
    await db.refresh(ws)
    logger.info("Created and activated default workspace (id=%s).", ws.id)
    return ws


def init_default_workspace_sync(db: Session) -> Workspace:
    """Synchronous version of workspace initialization for sync contexts or tests."""
    settings = get_settings()
    docx_path = Path(settings.default_docx_template_path)
    pptx_path = Path(settings.default_pptx_template_path)

    if not docx_path.is_file():
        raise FileNotFoundError(
            f"Configured DEFAULT_DOCX_TEMPLATE_PATH not found: '{docx_path}'. "
            "Startup aborted. Never silently substituting a different file."
        )
    if not pptx_path.is_file():
        raise FileNotFoundError(
            f"Configured DEFAULT_PPTX_TEMPLATE_PATH not found: '{pptx_path}'. "
            "Startup aborted. Never silently substituting a different file."
        )

    resolved_docx = str(docx_path.resolve())
    resolved_pptx = str(pptx_path.resolve())

    active_ws = db.query(Workspace).filter(Workspace.is_active == True).first()
    if active_ws and active_ws.docx_template_file_id and active_ws.pptx_template_file_id:
        doc_f = db.query(UploadedFile).filter(UploadedFile.id == active_ws.docx_template_file_id).first()
        ppt_f = db.query(UploadedFile).filter(UploadedFile.id == active_ws.pptx_template_file_id).first()
        if (
            doc_f
            and ppt_f
            and (Path(doc_f.stored_path).resolve() == docx_path.resolve() or doc_f.stored_path in (str(docx_path), resolved_docx))
            and (Path(ppt_f.stored_path).resolve() == pptx_path.resolve() or ppt_f.stored_path in (str(pptx_path), resolved_pptx))
            and active_ws.doc_profile_json
            and active_ws.ppt_profile_json
        ):
            return active_ws

    doc_file = (
        db.query(UploadedFile)
        .filter(UploadedFile.stored_path.in_([str(docx_path), resolved_docx]))
        .first()
    )
    if not doc_file:
        doc_file = UploadedFile(
            filename=docx_path.name,
            file_type="docx",
            file_size=docx_path.stat().st_size,
            stored_path=str(docx_path),
        )
        db.add(doc_file)
        db.flush()

    ppt_file = (
        db.query(UploadedFile)
        .filter(UploadedFile.stored_path.in_([str(pptx_path), resolved_pptx]))
        .first()
    )
    if not ppt_file:
        ppt_file = UploadedFile(
            filename=pptx_path.name,
            file_type="pptx",
            file_size=pptx_path.stat().st_size,
            stored_path=str(pptx_path),
        )
        db.add(ppt_file)
        db.flush()

    existing_ws = (
        db.query(Workspace)
        .filter(
            Workspace.docx_template_file_id == doc_file.id,
            Workspace.pptx_template_file_id == ppt_file.id,
        )
        .first()
    )
    if existing_ws and existing_ws.doc_profile_json and existing_ws.ppt_profile_json:
        db.query(Workspace).update({Workspace.is_active: False})
        existing_ws.is_active = True
        db.commit()
        db.refresh(existing_ws)
        return existing_ws

    doc_profile = analyze_document(docx_path, file_id=doc_file.id)
    ppt_profile = analyze_presentation(pptx_path, file_id=ppt_file.id)

    logo_dir = Path("storage/workspaces/default")
    logo_path = _extract_logo_from_templates(docx_path, pptx_path, logo_dir)

    db.query(Workspace).update({Workspace.is_active: False})

    if existing_ws:
        existing_ws.doc_profile_json = doc_profile.model_dump_json()
        existing_ws.ppt_profile_json = ppt_profile.model_dump_json()
        existing_ws.logo_path = logo_path
        existing_ws.is_active = True
        ws = existing_ws
    else:
        ws = Workspace(
            name="Default Workspace",
            docx_template_file_id=doc_file.id,
            pptx_template_file_id=ppt_file.id,
            doc_profile_json=doc_profile.model_dump_json(),
            ppt_profile_json=ppt_profile.model_dump_json(),
            logo_path=logo_path,
            is_active=True,
        )
        db.add(ws)
    db.commit()
    db.refresh(ws)
    return ws
