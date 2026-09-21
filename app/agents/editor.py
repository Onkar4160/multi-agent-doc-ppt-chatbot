"""Conversational Editor Agent for deterministic model edits, concise deck rewriting, and web refreshes."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.doc_analyzer import analyze_document
from app.agents.ppt_analyzer import analyze_presentation
from app.agents.validator import validate_outputs
from app.llm.client import get_llm_client, LLMClient
from app.models.artifact import Artifact, ArtifactVersion
from app.models.deck_model import BulletItem, DeckModel, SlideModel
from app.models.document_model import DocumentModel, Section
from app.models.edit_ops import (
    AddSectionOp,
    AddSlideOp,
    CondenseDeckOp,
    DeleteSectionOp,
    DeleteSlideOp,
    EditPlan,
    MoveSlideOp,
    Op,
    RefreshWithWebOp,
    UpdateSectionOp,
    UpdateSlideOp,
)
from app.services.context_builder import build_context
from app.services.docx_renderer import render_docx
from app.services.pptx_renderer import render_pptx
from app.services.diffing import diff_models

logger = logging.getLogger(__name__)

DEFAULT_DOCX_TEMPLATE = Path("data/sample_templates/Company_Proposal.docx")
DEFAULT_PPTX_TEMPLATE = Path("data/sample_templates/Company_Template.pptx")


class EditResult(BaseModel):
    """Result returned after editing an artifact."""

    artifact_id: int
    new_version_no: int
    summary: str
    diff: dict[str, Any]
    file_path: str
    download_url: str
    llm_calls: int


EDITOR_PROMPT = """You are an expert technical editor.
The user wants to make a conversational edit to an existing document or presentation deck.

USER INSTRUCTION:
"{instruction}"

CURRENT ARTIFACT TYPE: {artifact_type}
CURRENT OUTLINE / CONTENT SUMMARY:
{outline_summary}

TEMPLATE OUTLINE / LAYOUT ROLES:
{layout_roles}

TONE PROFILE:
{tone_profile}

RULES:
1. Return a valid EditPlan object containing the target, list of ops to perform, and a clear summary.
2. Change ONLY what the user asked for. Keep everything else identical.
3. Keep the same tone throughout.
4. Cite sources for new facts using valid source_ids.
"""


def _generate_edit_plan(
    instruction: str,
    artifact_type: str,
    model: DocumentModel | DeckModel,
    profile: Any,
    llm_client: LLMClient,
) -> EditPlan:
    """ONE LLM call to parse user instruction into an EditPlan of operations."""
    if isinstance(model, DocumentModel):
        outline_lines = [f"- Heading: '{s.heading}' ({len(s.blocks)} blocks)" for s in model.sections]
        outline_summary = "\n".join(outline_lines)
        layout_roles = ", ".join(profile.doc_outline) if getattr(profile, "doc_outline", None) else "Standard document section layout"
    else:
        outline_lines = [f"- Slide {idx+1} [{s.role}]: '{s.title}'" for idx, s in enumerate(model.slides)]
        outline_summary = "\n".join(outline_lines)
        layout_roles = ", ".join([l.name for l in profile.ppt_style.layouts]) if (getattr(profile, "ppt_style", None) and profile.ppt_style.layouts) else "Standard deck layout roles"

    tone_str = profile.tone_profile.primary_tone if getattr(profile, "tone_profile", None) and profile.tone_profile.primary_tone else "Professional and technical"

    prompt = EDITOR_PROMPT.format(
        instruction=instruction,
        artifact_type=artifact_type,
        outline_summary=outline_summary,
        layout_roles=layout_roles,
        tone_profile=tone_str,
    )

    try:
        plan = llm_client.generate_json(
            prompt=prompt,
            schema=EditPlan,
            system="You are a precise technical editor producing granular edit ops.",
        )
    except Exception as exc:
        raise RuntimeError(
            f"EditPlan LLM call failed [model={llm_client._primary}]: {exc}"
        ) from exc
    return plan


def _apply_ops_to_docx(model: DocumentModel, ops: list[Op]) -> DocumentModel:
    """Deterministically apply docx operations to DocumentModel copy."""
    new_model = DocumentModel.model_validate(model.model_dump())
    sections = new_model.sections

    for op in ops:
        op_type = getattr(op, "type", "")
        if op_type == "add_section":
            target_h = getattr(op, "after_heading", None)
            new_sec = getattr(op, "section")
            if target_h:
                idx = next((i for i, s in enumerate(sections) if s.heading.lower() == target_h.lower()), -1)
                if idx != -1:
                    sections.insert(idx + 1, new_sec)
                else:
                    sections.append(new_sec)
            else:
                sections.append(new_sec)

        elif op_type == "update_section":
            target_h = getattr(op, "heading", "")
            new_blocks = getattr(op, "new_blocks", [])
            idx = next((i for i, s in enumerate(sections) if s.heading.lower() == target_h.lower()), -1)
            if idx != -1:
                sections[idx].blocks = new_blocks

        elif op_type == "delete_section":
            target_h = getattr(op, "heading", "")
            sections[:] = [s for s in sections if s.heading.lower() != target_h.lower()]

    return new_model


def _apply_ops_to_pptx(model: DeckModel, ops: list[Op]) -> DeckModel:
    """Deterministically apply pptx operations to DeckModel copy."""
    new_model = DeckModel.model_validate(model.model_dump())
    slides = new_model.slides

    for op in ops:
        op_type = getattr(op, "type", "")
        if op_type == "add_slide":
            after_idx = getattr(op, "after_index", None)
            new_slide = getattr(op, "slide")
            if after_idx and 1 <= after_idx <= len(slides):
                slides.insert(after_idx, new_slide)
            else:
                slides.append(new_slide)

        elif op_type == "update_slide":
            s_idx = getattr(op, "index", 1) - 1
            new_slide = getattr(op, "slide")
            if 0 <= s_idx < len(slides):
                slides[s_idx] = new_slide

        elif op_type == "delete_slide":
            s_idx = getattr(op, "index", 1) - 1
            if 0 <= s_idx < len(slides):
                slides.pop(s_idx)

        elif op_type == "move_slide":
            from_i = getattr(op, "from_index", 1) - 1
            to_i = getattr(op, "to_index", 1) - 1
            if 0 <= from_i < len(slides) and 0 <= to_i < len(slides):
                slide = slides.pop(from_i)
                slides.insert(to_i, slide)

    return new_model


def edit_artifact(
    artifact_id: int,
    instruction: str,
    base_version: int | None = None,
    db_session: Session | None = None,
) -> EditResult:
    """Conversational edit agent executing deterministic ops, deck condensing, or web refresh.

    Args:
        artifact_id: ID of the artifact to edit.
        instruction: Conversational edit prompt.
        base_version: Specific version number to base edit on (default latest).
        db_session: SQLAlchemy Session.

    Returns:
        EditResult containing new version number, diff, file path, and download URL.
    """
    if not db_session:
        raise ValueError("DB session required for edit_artifact")

    llm = get_llm_client()
    initial_llm_calls = llm.stats.total_calls

    # 1. Fetch Artifact & Target Version
    artifact = db_session.query(Artifact).filter(Artifact.id == artifact_id).first()
    if not artifact:
        raise ValueError(f"Artifact ID {artifact_id} not found.")

    if base_version:
        ver_rec = db_session.query(ArtifactVersion).filter(
            ArtifactVersion.artifact_id == artifact_id,
            ArtifactVersion.version_no == base_version,
        ).first()
    else:
        ver_rec = db_session.query(ArtifactVersion).filter(
            ArtifactVersion.artifact_id == artifact_id
        ).order_by(ArtifactVersion.version_no.desc()).first()

    if not ver_rec:
        raise ValueError(f"Version for artifact {artifact_id} not found.")

    old_model_json = ver_rec.model_json
    art_type = artifact.artifact_type

    if art_type == "docx":
        old_model = DocumentModel.model_validate_json(old_model_json)
        tmpl_path = DEFAULT_DOCX_TEMPLATE
        profile = analyze_document(tmpl_path)
    else:
        old_model = DeckModel.model_validate_json(old_model_json)
        tmpl_path = DEFAULT_PPTX_TEMPLATE
        profile = analyze_presentation(tmpl_path)

    # 2. ONE LLM call -> EditPlan
    edit_plan = _generate_edit_plan(instruction, art_type, old_model, profile, llm)

    # 3. Apply Ops
    if art_type == "docx":
        new_model = _apply_ops_to_docx(old_model, edit_plan.ops)
    else:
        new_model = _apply_ops_to_pptx(old_model, edit_plan.ops)

    # Collect existing source IDs
    existing_sids: list[int] = []
    if ver_rec.source_ids_json:
        try:
            existing_sids = json.loads(ver_rec.source_ids_json)
        except Exception:
            pass
    if not existing_sids:
        if isinstance(old_model, DocumentModel):
            existing_sids = [sid for sec in old_model.sections for b in sec.blocks for sid in getattr(b, "source_ids", [])]
        else:
            existing_sids = [sid for sl in old_model.slides for sid in sl.source_ids]

    merged_source_ids = list(set(existing_sids))

    # Handle special ops: condense_deck & refresh_with_web
    for op in edit_plan.ops:
        op_type = getattr(op, "type", "")

        if op_type == "condense_deck" and isinstance(new_model, DeckModel):
            # Single second LLM call to rewrite slides shorter
            max_b = getattr(op, "max_bullets", 4)
            max_w = getattr(op, "max_words_per_bullet", 15)
            condense_prompt = (
                f"Condense the following presentation deck to have AT MOST {max_b} bullets per slide "
                f"and AT MOST {max_w} words per bullet point. Keep exact slide roles, titles, and source_ids.\n\n"
                f"DECK MODEL:\n{new_model.model_dump_json()}"
            )
            try:
                condensed_deck = llm.generate_json(
                    prompt=condense_prompt,
                    schema=DeckModel,
                    system="You are a concise executive presentation editor.",
                )
                new_model = condensed_deck
            except Exception as exc:
                logger.warning(f"Condense deck LLM call failed: {exc}")

        elif op_type == "refresh_with_web":
            # Compute fresh SourceRegistry start_id after max existing source ID
            max_id = max(merged_source_ids) if merged_source_ids else 0
            start_id = max_id + 1

            topic = getattr(op, "topic", instruction)
            ctx = build_context(topic, use_web=True, use_kb=True, db_session=db_session, start_id=start_id)

            refresh_prompt = (
                f"Update and refresh content for topic '{topic}' using the following new research findings:\n"
                f"{json.dumps([f.model_dump() for f in ctx.findings])}\n\n"
                f"NEW CITATION SOURCES AVAILABLE (STARTING AT ID {start_id}):\n"
                f"{json.dumps(ctx.sources_list)}\n\n"
                f"CURRENT MODEL:\n{new_model.model_dump_json()}\n\n"
                f"INSTRUCTION: Rewrite only affected sections or slides using the new findings. Keep existing source IDs stable and cite new source IDs for new facts."
            )
            try:
                if isinstance(new_model, DocumentModel):
                    refreshed = llm.generate_json(prompt=refresh_prompt, schema=DocumentModel)
                else:
                    refreshed = llm.generate_json(prompt=refresh_prompt, schema=DeckModel)
                new_model = refreshed
                merged_source_ids = list(set(merged_source_ids + list(ctx.sources_map.keys())))
            except Exception as exc:
                logger.warning(f"Refresh with web LLM call failed: {exc}")

    # 4. Compute Diff
    diff = diff_models(old_model, new_model)

    # 5. Render & Validate
    output_dir = Path("data/outputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    next_ver_no = ver_rec.version_no + 1

    if art_type == "docx":
        out_file = output_dir / f"Proposal_{artifact_id}_v{next_ver_no}.docx"
        render_docx(new_model, tmpl_path, profile, out_file)
        validate_outputs(doc_model=new_model)
    else:
        out_file = output_dir / f"Deck_{artifact_id}_v{next_ver_no}.pptx"
        render_pptx(new_model, tmpl_path, profile, out_file)
        validate_outputs(deck_model=new_model, expected_slide_count=len(new_model.slides))

    # 6. Save NEW version record
    new_ver_rec = ArtifactVersion(
        artifact_id=artifact_id,
        version_no=next_ver_no,
        model_json=new_model.model_dump_json(),
        file_path=str(out_file),
        file_type=art_type,
        parent_version_id=ver_rec.id,
        change_summary=edit_plan.summary or instruction[:150],
        source_ids_json=json.dumps(merged_source_ids),
        diff_json=json.dumps(diff),
    )
    db_session.add(new_ver_rec)
    db_session.commit()

    llm_calls_used = llm.stats.total_calls - initial_llm_calls

    return EditResult(
        artifact_id=artifact_id,
        new_version_no=next_ver_no,
        summary=edit_plan.summary,
        diff=diff,
        file_path=str(out_file),
        download_url=f"/artifacts/{artifact_id}/download?version={next_ver_no}",
        llm_calls=llm_calls_used,
    )
