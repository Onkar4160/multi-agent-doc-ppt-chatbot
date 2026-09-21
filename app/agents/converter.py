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
from app.llm.client import get_llm_client, LLMClient
from app.models.artifact import Artifact, ArtifactVersion
from app.models.deck_model import DeckModel
from app.models.document_model import DocumentModel
from app.services.docx_renderer import render_docx
from app.services.pptx_renderer import render_pptx

logger = logging.getLogger(__name__)

DEFAULT_DOCX_TEMPLATE = Path("data/sample_templates/Company_Proposal.docx")
DEFAULT_PPTX_TEMPLATE = Path("data/sample_templates/Company_Template.pptx")


class ConvertResult(BaseModel):
    """Result returned after converting an artifact."""

    new_artifact_id: int
    target_kind: str
    title: str
    version_no: int = 1
    file_path: str
    download_url: str


CONVERT_DOC_TO_PPT_PROMPT = """You are an expert presentation designer.
Convert the following technical document proposal into a structured PowerPoint presentation deck matching the DeckModel schema.

TARGET SLIDE COUNT: {slide_count} slides.

DOCUMENT CONTENT:
{doc_json}

RULES:
1. Preserve all facts, metrics, and source_ids from the original document.
2. Slide 1 MUST have role="title".
3. Content slides MUST have 3 to 5 detailed bullet points.
"""

CONVERT_PPT_TO_DOC_PROMPT = """You are an expert technical proposal writer.
Convert the following presentation deck into a comprehensive, detailed technical proposal document matching the DocumentModel schema.

PRESENTATION DECK CONTENT:
{deck_json}

RULES:
1. Expand slide bullets into rich, thorough paragraphs and structured tables.
2. Preserve all facts, metrics, and source_ids from the slides.
3. Include at least 6 structured sections.
"""


def convert_artifact(
    artifact_id: int,
    target_kind: Literal["docx", "pptx"],
    slide_count: int = 10,
    db_session: Session | None = None,
) -> ConvertResult:
    """Convert an existing artifact (docx -> pptx or pptx -> docx).

    Args:
        artifact_id: ID of the source artifact.
        target_kind: Target output format ("docx" or "pptx").
        slide_count: Target slide count when converting to PPTX (default 10).
        db_session: SQLAlchemy Session.

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

    ver_rec = db_session.query(ArtifactVersion).filter(
        ArtifactVersion.artifact_id == artifact_id
    ).order_by(ArtifactVersion.version_no.desc()).first()

    if not ver_rec:
        raise ValueError(f"Version history for artifact {artifact_id} not found.")

    output_dir = Path("data/outputs")
    output_dir.mkdir(parents=True, exist_ok=True)

    # 2. Conversion Logic
    if src_art.artifact_type == "docx" and target_kind == "pptx":
        doc_model = DocumentModel.model_validate_json(ver_rec.model_json)
        prompt = CONVERT_DOC_TO_PPT_PROMPT.format(slide_count=slide_count, doc_json=doc_model.model_dump_json())
        
        try:
            target_model = llm.generate_json(prompt=prompt, schema=DeckModel)
        except Exception as exc:
            raise RuntimeError(
                f"LLM conversion failed [docx\u2192pptx, model={llm._primary}]: {exc}"
            ) from exc

        tmpl_path = DEFAULT_PPTX_TEMPLATE
        profile = analyze_presentation(tmpl_path)
        out_file = output_dir / f"Converted_Deck_{uuid.uuid4().hex[:8]}.pptx"
        render_pptx(target_model, tmpl_path, profile, out_file)
        new_title = target_model.title

    elif src_art.artifact_type == "pptx" and target_kind == "docx":
        deck_model = DeckModel.model_validate_json(ver_rec.model_json)
        prompt = CONVERT_PPT_TO_DOC_PROMPT.format(deck_json=deck_model.model_dump_json())

        try:
            target_model = llm.generate_json(prompt=prompt, schema=DocumentModel)
        except Exception as exc:
            raise RuntimeError(
                f"LLM conversion failed [pptx\u2192docx, model={llm._primary}]: {exc}"
            ) from exc

        tmpl_path = DEFAULT_DOCX_TEMPLATE
        profile = analyze_document(tmpl_path)
        out_file = output_dir / f"Converted_Proposal_{uuid.uuid4().hex[:8]}.docx"
        render_docx(target_model, tmpl_path, profile, out_file)
        new_title = target_model.title

    else:
        raise ValueError(f"Cannot convert artifact of type '{src_art.artifact_type}' to '{target_kind}'.")

    # 3. Create NEW Artifact record
    new_art = Artifact(title=new_title, artifact_type=target_kind)
    db_session.add(new_art)
    db_session.flush()

    new_ver = ArtifactVersion(
        artifact_id=new_art.id,
        version_no=1,
        model_json=target_model.model_dump_json(),
        file_path=str(out_file),
        file_type=target_kind,
        change_summary=f"Converted from artifact {artifact_id} version {ver_rec.version_no}",
    )
    db_session.add(new_ver)
    db_session.commit()

    return ConvertResult(
        new_artifact_id=new_art.id,
        target_kind=target_kind,
        title=new_title,
        version_no=1,
        file_path=str(out_file),
        download_url=f"/artifacts/{new_art.id}/download?version=1",
    )
