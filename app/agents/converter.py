"""Converter agent for bi-directional conversion between DOCX documents and PPTX presentations."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents.doc_analyzer import analyze_document
from app.agents.ppt_analyzer import analyze_presentation
from app.agents.validator import validate_outputs
from app.llm.client import get_llm_client, LLMClient
from app.models.artifact import Artifact, ArtifactVersion
from app.models.deck_model import DeckModel
from app.models.document_model import DocumentModel
from app.models.file import UploadedFile
from app.models.template_profile import TemplateProfile
from app.models.workspace import Workspace
from app.services.docx_renderer import render_docx
from app.services.pptx_renderer import render_pptx

logger = logging.getLogger(__name__)


class ConvertResult(BaseModel):
    """Result returned after converting an artifact."""

    new_artifact_id: int
    target_kind: str
    title: str
    version_no: int = 1
    file_path: str
    download_url: str


CONVERT_DOC_TO_PPT_PROMPT = """You are an expert presentation designer.
Convert the following document into a structured PowerPoint presentation deck matching the DeckModel schema.

TARGET SLIDE COUNT: {slide_count} slides.

DOCUMENT CONTENT:
{doc_json}

RULES:
1. Preserve all facts, metrics, and source_ids from the original document.
2. Do not introduce any new factual claim while converting. Only reorganize or rephrase EXISTING content, and preserve every source_id exactly as given. Do not invent new citations.
3. Slide 1 MUST have role="title".
4. Content slides MUST have 3 to 5 detailed bullet points.
"""

CONVERT_PPT_TO_DOC_PROMPT = """You are an expert technical and business writer.
Convert the following presentation deck into a comprehensive, detailed document matching the DocumentModel schema.

PRESENTATION DECK CONTENT:
{deck_json}

RULES:
1. Expand slide bullets into rich, thorough paragraphs and structured tables.
2. Preserve all facts, metrics, and source_ids from the slides.
3. Do not introduce any new factual claim while converting. Only reorganize or rephrase EXISTING content, and preserve every source_id exactly as given. Do not invent new citations.
4. Organize the document into logically coherent structured sections reflecting the deck content.
"""


def convert_artifact(
    artifact_id: int,
    target_kind: Literal["docx", "pptx"],
    slide_count: int = 10,
    db_session: Session | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
) -> ConvertResult:
    """Convert an existing artifact (docx -> pptx or pptx -> docx).

    Args:
        artifact_id: ID of the source artifact.
        target_kind: Target output format ("docx" or "pptx").
        slide_count: Target slide count when converting to PPTX (default 10).
        db_session: SQLAlchemy Session.
        session_id: Optional session ID to attach to the new artifact.
        run_id: Optional run ID to attach to the new artifact.

    Returns:
        ConvertResult with new artifact ID, file path, and download URL.
    """
    if not db_session:
        raise ValueError("DB session required for convert_artifact")

    llm = get_llm_client()

    # 1. Fetch source artifact & latest version
    src_art = db_session.query(Artifact).filter(Artifact.id == artifact_id).first()
    if not src_art:
        raise ValueError(f"Source artifact ID {artifact_id} not found.")

    sess_id = session_id or src_art.session_id
    r_id = run_id or src_art.run_id

    ver_rec = (
        db_session.query(ArtifactVersion)
        .filter(ArtifactVersion.artifact_id == artifact_id)
        .order_by(ArtifactVersion.version_no.desc())
        .first()
    )

    if not ver_rec:
        raise ValueError(f"Version history for artifact {artifact_id} not found.")

    output_dir = Path("data/outputs")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check for active workspace templates & profiles
    active_ws = (
        db_session.query(Workspace).filter(Workspace.is_active == True).first()
    )

    source_ids: list[int] = []
    if ver_rec.source_ids_json:
        try:
            source_ids = json.loads(ver_rec.source_ids_json)
        except Exception:
            pass

    # 2. Conversion Logic
    if src_art.artifact_type == "docx" and target_kind == "pptx":
        doc_model = DocumentModel.model_validate_json(ver_rec.model_json)
        if not source_ids:
            source_ids = [
                sid
                for sec in doc_model.sections
                for b in sec.blocks
                for sid in getattr(b, "source_ids", [])
            ]

        prompt = CONVERT_DOC_TO_PPT_PROMPT.format(
            slide_count=slide_count, doc_json=doc_model.model_dump_json()
        )

        try:
            target_model = llm.generate_json(prompt=prompt, schema=DeckModel)
        except Exception as exc:
            raise RuntimeError(
                f"LLM conversion failed [docx->pptx, model={llm._primary}]: {exc}"
            ) from exc

        tmpl_path = None
        profile = None
        if active_ws:
            up_f = (
                db_session.query(UploadedFile)
                .filter(UploadedFile.id == active_ws.pptx_template_file_id)
                .first()
            )
            if up_f and Path(up_f.stored_path).exists():
                tmpl_path = Path(up_f.stored_path)
            if active_ws.ppt_profile_json:
                profile = TemplateProfile.model_validate_json(active_ws.ppt_profile_json)
        if not tmpl_path:
            def_pptx = Path("data/sample_templates/presentation_Template.pptx")
            if def_pptx.exists():
                tmpl_path = def_pptx
        if tmpl_path and not profile:
            profile = analyze_presentation(tmpl_path)
        elif not profile:
            profile = TemplateProfile(colors={"primary": "#000000"})

        out_file = output_dir / f"Converted_Deck_{uuid.uuid4().hex[:8]}.pptx"
        render_pptx(target_model, tmpl_path, profile, out_file)
        validate_outputs(
            deck_model=target_model,
            expected_slide_count=len(target_model.slides),
            valid_source_ids=set(source_ids),
        )
        new_title = target_model.title

    elif src_art.artifact_type == "pptx" and target_kind == "docx":
        deck_model = DeckModel.model_validate_json(ver_rec.model_json)
        if not source_ids:
            source_ids = [sid for sl in deck_model.slides for sid in sl.source_ids]

        prompt = CONVERT_PPT_TO_DOC_PROMPT.format(
            deck_json=deck_model.model_dump_json()
        )

        try:
            target_model = llm.generate_json(prompt=prompt, schema=DocumentModel)
        except Exception as exc:
            raise RuntimeError(
                f"LLM conversion failed [pptx->docx, model={llm._primary}]: {exc}"
            ) from exc

        tmpl_path = None
        profile = None
        if active_ws:
            up_f = (
                db_session.query(UploadedFile)
                .filter(UploadedFile.id == active_ws.docx_template_file_id)
                .first()
            )
            if up_f and Path(up_f.stored_path).exists():
                tmpl_path = Path(up_f.stored_path)
            if active_ws.doc_profile_json:
                profile = TemplateProfile.model_validate_json(active_ws.doc_profile_json)
        if not tmpl_path:
            def_docx = Path("data/sample_templates/proposal_Template.docx")
            if def_docx.exists():
                tmpl_path = def_docx
        if tmpl_path and not profile:
            profile = analyze_document(tmpl_path)
        elif not profile:
            profile = TemplateProfile(colors={"primary": "#000000"})

        out_file = output_dir / f"Converted_Proposal_{uuid.uuid4().hex[:8]}.docx"
        render_docx(target_model, tmpl_path, profile, out_file)
        validate_outputs(
            doc_model=target_model, valid_source_ids=set(source_ids)
        )
        new_title = target_model.title

    else:
        raise ValueError(
            f"Cannot convert artifact of type '{src_art.artifact_type}' to '{target_kind}'."
        )

    # 3. Create NEW Artifact record
    new_art = Artifact(
        title=new_title,
        artifact_type=target_kind,
        session_id=sess_id,
        run_id=r_id,
    )
    db_session.add(new_art)
    db_session.flush()
    new_art_id = new_art.id

    new_ver = ArtifactVersion(
        artifact_id=new_art_id,
        version_no=1,
        model_json=target_model.model_dump_json(),
        file_path=str(out_file),
        file_type=target_kind,
        change_summary=f"Converted from artifact {artifact_id} version {ver_rec.version_no}",
        source_ids_json=json.dumps(source_ids),
    )
    db_session.add(new_ver)
    try:
        db_session.commit()
    except Exception as exc:
        db_session.rollback()
        raise exc

    return ConvertResult(
        new_artifact_id=new_art_id,
        target_kind=target_kind,
        title=new_title,
        version_no=1,
        file_path=str(out_file),
        download_url=f"/artifacts/{new_art_id}/download?version=1",
    )
