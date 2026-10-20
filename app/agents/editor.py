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
from app.models.workbook_model import CellModel, SheetModel, TableModel, WorkbookModel
from app.models.edit_ops import (
    AddRowOp,
    AddSectionOp,
    AddSheetOp,
    AddSlideOp,
    CondenseDeckOp,
    DeleteSectionOp,
    DeleteSheetOp,
    DeleteSlideOp,
    EditPlan,
    MoveSlideOp,
    Op,
    RefreshWithWebOp,
    UpdateCellOp,
    UpdateParagraphOp,
    UpdateSectionOp,
    UpdateSheetOp,
    UpdateSlideOp,
    UpdateTitleOp,
)
from app.models.file import UploadedFile
from app.models.source import Source
from app.models.template_profile import TemplateProfile
from app.models.workspace import Workspace
from app.services.context_builder import build_context
from app.services.diffing import diff_models
from app.services.docx_renderer import render_docx
from app.services.evidence_pack import EvidenceItem, build_evidence_pack, format_evidence_pack
from app.services.pptx_renderer import render_pptx
from app.services.versioning import create_version_sync
from app.services.xlsx_renderer import render_xlsx

logger = logging.getLogger(__name__)


class EditResult(BaseModel):
    """Result returned after editing an artifact."""

    artifact_id: int
    new_version_no: int
    summary: str
    diff: dict[str, Any]
    file_path: str
    download_url: str
    llm_calls: int
    no_sources_warning: str | None = None


EDITOR_PROMPT = """You are an expert technical editor.
The user wants to make a conversational edit to an existing document or presentation deck.

USER INSTRUCTION:
"{instruction}"

ACTIVE REPORT / PRESENTATION TOPIC:
{active_topic}

CURRENT ARTIFACT TYPE: {artifact_type}
CURRENT CONTENT (every unit with its node_id; points are numbered as the ops expect):
{outline_summary}

UNITS THE USER NAMED (resolved): {targets}

TEMPLATE OUTLINE / LAYOUT ROLES:
{layout_roles}

TONE PROFILE:
{tone_profile}

--- EVIDENCE PACK ---
{evidence_pack_formatted}

RULES:
1. Return a valid EditPlan object containing the target, list of ops to perform, and a clear summary.
2. Change ONLY what the user asked for. Everything else in the file is kept exactly as it is in the current version,
   so use the SMALLEST operation that does the job:
   - one point on a slide: update_point / add_point / delete_point (slide_index and point_index from the content above);
   - a slide's title, subtitle or notes: update_slide_text;
   - a word, phrase or figure: replace_text with the exact text as shown above and the node_id of its slide/section;
   - one paragraph of a document: update_paragraph; a whole section or slide only when the user asks to rewrite it
     (update_section / update_slide — then copy every part that should not change verbatim).
   Find the unit the user means by its content when they describe it ("the slide about costs", "the point on risks").
3. scope_node_ids: list the node_id of every slide/section your ops change. Units not listed are restored from the
   current version, so list exactly what you change.
3. Keep the same tone throughout.
4. GROUNDING & FACT CITATION:
   You may only state a specific fact (number, date, statistic, name, claimed event) if it matches an entry in the EVIDENCE PACK below, citing that entry's exact id as source_id. General connective text needs no citation but must contain no invented specific fact. If something isn't covered by the evidence pack, omit it or state it as general knowledge without invented precision — never fabricate a number or date.
   - New or changed blocks and slides may cite ONLY ids from the EVIDENCE PACK above, or ids already cited by that same block before.
   - If the EVIDENCE PACK is empty: write NO specific facts (no numbers, dates, version numbers, names), and attach NO citation source_ids. Added content must be general knowledge without citations.
5. WHEN ADDING A TOPIC:
   - For DOCX documents: add a full substantive section (about 150-250 words, mixing paragraphs, bullets, or a table where useful).
   - For PPTX decks: add 2 matching slides after the related slide, with citations.
6. TITLE & SCOPE UPDATE:
   - When an edit widens the scope (e.g., adding a new topic, language, or technology), include an `update_title` op with an updated overarching title and subtitle covering the whole scope.
7. Never use markdown syntax (**, __, #, -) in any text field. Write plain text only. Bold/emphasis is not supported in this schema.
{extra_error_context}
"""


def _generate_edit_plan(
    instruction: str,
    artifact_type: str,
    model: DocumentModel | DeckModel | WorkbookModel,
    profile: Any,
    llm_client: LLMClient,
    evidence_pack: list[Any] | None = None,
    active_topic: str = "",
    extra_error_context: str = "",
    targets: list[str] | None = None,
) -> EditPlan:
    """ONE LLM call to parse user instruction into an EditPlan of operations."""
    from app.agents.edit_content import outline_for_editor

    if isinstance(model, DocumentModel):
        outline_lines = [
            f"- Heading: '{s.heading}' ({len(s.blocks)} blocks)" for s in model.sections
        ]
        outline_summary = "\n".join(outline_lines)
        layout_roles = (
            ", ".join(profile.doc_outline)
            if getattr(profile, "doc_outline", None)
            else "Standard document section layout"
        )
    elif isinstance(model, WorkbookModel):
        outline_lines = [
            f"- Sheet: '{s.name}' ({len(s.tables)} tables, {len(s.cells)} cells)"
            for s in model.sheets
        ]
        outline_summary = "\n".join(outline_lines)
        layout_roles = "Standard spreadsheet workbook tables and formulas"
    else:
        outline_lines = [
            f"- Slide {idx+1} [{s.role}]: '{s.title}'"
            for idx, s in enumerate(model.slides)
        ]
        outline_summary = "\n".join(outline_lines)
        layout_roles = (
            ", ".join([l.name for l in profile.ppt_style.layouts])
            if (getattr(profile, "ppt_style", None) and profile.ppt_style.layouts)
            else "Standard deck layout roles"
        )

    tone_str = (
        profile.tone.voice
        if getattr(profile, "tone", None) and getattr(profile.tone, "voice", None)
        else (
            profile.tone_profile.primary_tone
            if getattr(profile, "tone_profile", None)
            and getattr(profile.tone_profile, "primary_tone", None)
            else "Professional and technical"
        )
    )

    formatted_pack = (
        format_evidence_pack(evidence_pack)
        if evidence_pack
        else "(No evidence items provided)"
    )

    prompt = EDITOR_PROMPT.format(
        instruction=instruction,
        active_topic=active_topic or "(None)",
        artifact_type=artifact_type,
        outline_summary=outline_for_editor(model, targets) or outline_summary,
        targets=", ".join(targets or []) or "(none named: find them from the content)",
        layout_roles=layout_roles,
        tone_profile=tone_str,
        evidence_pack_formatted=formatted_pack,
        extra_error_context=extra_error_context,
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
                idx = next(
                    (
                        i
                        for i, s in enumerate(sections)
                        if s.heading.lower() == target_h.lower()
                    ),
                    -1,
                )
                if idx != -1:
                    sections.insert(idx + 1, new_sec)
                else:
                    sections.append(new_sec)
            else:
                sections.append(new_sec)

        elif op_type == "update_section":
            target_h = getattr(op, "heading", "")
            new_blocks = getattr(op, "new_blocks", [])
            idx = next(
                (
                    i
                    for i, s in enumerate(sections)
                    if s.heading.lower() == target_h.lower()
                ),
                -1,
            )
            if idx != -1:
                sections[idx].blocks = new_blocks

        elif op_type == "update_paragraph":
            from app.models.document_model import ParagraphBlock
            target_h = getattr(op, "heading", None)
            p_idx = getattr(op, "paragraph_index", 1) - 1
            new_text = getattr(op, "new_text", "")
            sids = getattr(op, "source_ids", [])
            for sec in sections:
                if not target_h or sec.heading.lower() == target_h.lower():
                    paras = [b for b in sec.blocks if isinstance(b, ParagraphBlock) or getattr(b, "type", "") == "paragraph"]
                    if 0 <= p_idx < len(paras):
                        paras[p_idx].text = new_text
                        if sids:
                            paras[p_idx].source_ids = sids
                        break

        elif op_type == "delete_section":
            target_h = getattr(op, "heading", "")
            sections[:] = [s for s in sections if s.heading.lower() != target_h.lower()]

        elif op_type == "update_title":
            if getattr(op, "title", None):
                new_model.title = op.title
            if getattr(op, "subtitle", None):
                new_model.subtitle = op.subtitle

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

        elif op_type == "update_title":
            if getattr(op, "title", None):
                new_model.title = op.title
            if getattr(op, "subtitle", None):
                if hasattr(new_model, "subtitle"):
                    new_model.subtitle = op.subtitle
            for sl in new_model.slides:
                if sl.role == "title":
                    if getattr(op, "title", None):
                        sl.title = op.title
                    if getattr(op, "subtitle", None):
                        sl.subtitle = op.subtitle

    return new_model


def _apply_ops_to_xlsx(model: WorkbookModel, ops: list[Op]) -> WorkbookModel:
    """Deterministically apply spreadsheet operations to WorkbookModel copy."""
    new_model = WorkbookModel.model_validate(model.model_dump())
    sheets = new_model.sheets

    for op in ops:
        op_type = getattr(op, "type", "")
        if op_type == "add_sheet":
            sheet = getattr(op, "sheet", None)
            if sheet:
                sheets.append(sheet)

        elif op_type == "update_sheet":
            sheet_name = getattr(op, "sheet_name", "")
            target_sheet = next((s for s in sheets if s.name.lower() == sheet_name.lower()), None)
            if target_sheet:
                if getattr(op, "tables", None) is not None:
                    target_sheet.tables = op.tables
                if getattr(op, "cells", None) is not None:
                    target_sheet.cells = op.cells

        elif op_type == "update_cell":
            sheet_name = getattr(op, "sheet_name", "")
            coord = getattr(op, "coordinate", "").upper()
            target_sheet = next((s for s in sheets if s.name.lower() == sheet_name.lower()), None)
            if target_sheet:
                existing_cell = next((c for c in target_sheet.cells if c.coordinate.upper() == coord), None)
                if existing_cell:
                    if getattr(op, "value", None) is not None:
                        existing_cell.value = op.value
                    if getattr(op, "formula", None) is not None:
                        existing_cell.formula = op.formula
                else:
                    from app.models.workbook_model import CellModel
                    target_sheet.cells.append(
                        CellModel(
                            coordinate=coord,
                            value=getattr(op, "value", None),
                            formula=getattr(op, "formula", None),
                        )
                    )

        elif op_type == "add_row":
            sheet_name = getattr(op, "sheet_name", "")
            table_title = getattr(op, "table_title", "")
            row_data = getattr(op, "row_data", [])
            target_sheet = next((s for s in sheets if s.name.lower() == sheet_name.lower()), None)
            if target_sheet:
                target_table = None
                if table_title:
                    target_table = next((t for t in target_sheet.tables if t.title and t.title.lower() == table_title.lower()), None)
                if not target_table and target_sheet.tables:
                    target_table = target_sheet.tables[0]
                if target_table:
                    target_table.rows.append(row_data)

        elif op_type == "delete_sheet":
            sheet_name = getattr(op, "sheet_name", "")
            sheets[:] = [s for s in sheets if s.name.lower() != sheet_name.lower()]

        elif op_type == "update_title":
            if getattr(op, "title", None):
                new_model.title = op.title

    return new_model



def edit_artifact(
    artifact_id: int,
    instruction: str,
    base_version: int | None = None,
    evidence_pack: list[Any] | None = None,
    db_session: Session | None = None,
    active_topic: str = "",
    shared_context: Any = None,
    target_nodes: list[str] | None = None,
) -> EditResult:
    """Conversational edit agent executing deterministic ops, deck condensing, or web refresh.

    Args:
        artifact_id: ID of the artifact to edit.
        instruction: Conversational edit prompt.
        base_version: Specific version number to base edit on (default latest).
        evidence_pack: Optional evidence pack items for grounding new facts.
        db_session: SQLAlchemy Session.
        active_topic: Active topic context from session to anchor additions.
        shared_context: Optional precomputed research context to reuse across formats.
        target_nodes: Planned target node IDs from ExecutionPlan for scope validation.

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
        ver_rec = (
            db_session.query(ArtifactVersion)
            .filter(
                ArtifactVersion.artifact_id == artifact_id,
                ArtifactVersion.version_no == base_version,
            )
            .first()
        )
    else:
        ver_rec = (
            db_session.query(ArtifactVersion)
            .filter(ArtifactVersion.artifact_id == artifact_id)
            .order_by(ArtifactVersion.version_no.desc())
            .first()
        )

    if not ver_rec:
        raise ValueError(f"Version for artifact {artifact_id} not found.")

    old_model_json = ver_rec.model_json
    from app.services.render_context import RenderContext, deck_photos_by_node, photos_for_deck

    render_ctx = RenderContext.from_json(getattr(ver_rec, "render_json", None))
    ver_id = ver_rec.id
    ver_no = ver_rec.version_no
    old_file_path = ver_rec.file_path
    art_type = str(artifact.artifact_type)
    art_title = str(artifact.title)

    # Check for active workspace templates & profiles
    active_ws = (
        db_session.query(Workspace).filter(Workspace.is_active == True).first()
    )

    from app.services.artifact_nodes import ensure_node_ids
    from app.services.target_resolver import resolve_target

    if art_type == "docx":
        old_model = DocumentModel.model_validate_json(old_model_json)
        ensure_node_ids(old_model)
        target_check = resolve_target(instruction, old_model)
        if not target_check.is_valid and target_check.error_code == "INVALID_TARGET":
            raise ValueError(f"Target validation failed: {target_check.error_message}")
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
        if render_ctx is not None and render_ctx.template() is not None:
            tmpl_path, profile = render_ctx.template(), None
        if tmpl_path and not profile:
            from app.services.template_cache import cached_profile

            profile = cached_profile(tmpl_path, db_session)
        elif not profile:
            profile = TemplateProfile(colors={"primary": "#000000"})
    elif art_type == "xlsx":
        old_model = WorkbookModel.model_validate_json(old_model_json)
        ensure_node_ids(old_model)
        target_check = resolve_target(instruction, old_model)
        if not target_check.is_valid and target_check.error_code == "INVALID_TARGET":
            raise ValueError(f"Target validation failed: {target_check.error_message}")
        tmpl_path = None
        profile = TemplateProfile(colors={"primary": "#107C41"})
    else:
        old_model = DeckModel.model_validate_json(old_model_json)
        ensure_node_ids(old_model)
        target_check = resolve_target(instruction, old_model)
        if not target_check.is_valid and target_check.error_code == "INVALID_TARGET":
            raise ValueError(f"Target validation failed: {target_check.error_message}")
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
        if render_ctx is not None:  # the next version is drawn exactly like this one (same template or design)
            tmpl_path = render_ctx.template()
            profile = None
            if tmpl_path is None and render_ctx.mode == "template" and render_ctx.template_path:
                logger.warning("Template %s of artifact %s is gone; using the default", render_ctx.template_path, artifact_id)
                tmpl_path = Path("data/sample_templates/presentation_Template.pptx")
        if tmpl_path and not profile:
            from app.services.template_cache import cached_profile

            profile = cached_profile(tmpl_path, db_session)
        elif not profile:
            profile = TemplateProfile(colors={"primary": "#000000"})

    # Helper: backfill sources from DB if sources_json is missing
    def _backfill_sources_from_db(art: Artifact, v_rec: ArtifactVersion, db_s: Session) -> dict[str, dict[str, Any]]:
        import datetime
        t_str = datetime.date.today().strftime("%B %Y")
        res_map: dict[str, dict[str, Any]] = {}
        r_id = art.run_id
        p_id = getattr(art, "project_id", None) or getattr(v_rec, "project_id", None)
        sids_j = v_rec.source_ids_json

        s_rows = []
        if r_id:
            s_rows = db_s.query(Source).filter(Source.metadata_json.like(f"%{r_id}%")).all()

        if not s_rows and sids_j:
            try:
                sids = json.loads(sids_j)
                if sids:
                    s_rows = db_s.query(Source).filter(Source.id.in_(sids)).all()
            except Exception:
                pass

        if not s_rows and p_id:
            s_rows = db_s.query(Source).filter(Source.project_id == p_id).all()

        for s in s_rows:
            cid = s.id
            if s.metadata_json:
                try:
                    m = json.loads(s.metadata_json)
                    if "citation_id" in m:
                        cid = m["citation_id"]
                except Exception:
                    pass
            dt_s = s.created_at.strftime("%B %Y") if (s.created_at and hasattr(s.created_at, "strftime")) else t_str
            url_or_fn = s.url or ""
            title = s.title or url_or_fn or f"Source {cid}"
            res_map[str(cid)] = {
                "id": int(cid),
                "title": title,
                "url_or_filename": url_or_fn,
                "kind": s.kind or "web",
                "accessed_at": dt_s,
            }
        return res_map

    # Grounding violations check helper
    def _find_grounding_violations(
        o_model: DocumentModel | DeckModel,
        n_model: DocumentModel | DeckModel,
        allowed_sids: set[int],
    ) -> list[str]:
        violations = []
        if isinstance(n_model, DocumentModel) and isinstance(o_model, DocumentModel):
            old_block_sids: dict[str, set[int]] = {}
            for sec in o_model.sections:
                for b in sec.blocks:
                    b_txt = getattr(b, "text", "").strip()
                    if b_txt:
                        old_block_sids[b_txt] = set(getattr(b, "source_ids", []))

            for sec in n_model.sections:
                for b in sec.blocks:
                    b_txt = getattr(b, "text", "").strip()
                    cited = set(getattr(b, "source_ids", []))
                    if not cited:
                        continue
                    allowed = allowed_sids | old_block_sids.get(b_txt, set())
                    invalid = cited - allowed
                    if invalid:
                        violations.append(
                            f"Section '{sec.heading}' block ('{b_txt[:50]}...') cites invalid/unrelated source IDs {list(invalid)}"
                        )
        elif isinstance(n_model, DeckModel) and isinstance(o_model, DeckModel):
            old_bullet_sids: dict[str, set[int]] = {}
            for sl in o_model.slides:
                for b in (sl.bullets or []) + (sl.left or []) + (sl.right or []):
                    if b.text.strip():
                        old_bullet_sids[b.text.strip()] = set(b.source_ids)

            for sl in n_model.slides:
                for b in (sl.bullets or []) + (sl.left or []) + (sl.right or []):
                    cited = set(b.source_ids)
                    if not cited:
                        continue
                    allowed = allowed_sids | old_bullet_sids.get(b.text.strip(), set())
                    invalid = cited - allowed
                    if invalid:
                        violations.append(
                            f"Slide '{sl.title}' bullet ('{b.text[:50]}...') cites invalid/unrelated source IDs {list(invalid)}"
                        )
        return violations

    # Load previous version's sources_map or backfill
    old_sources_map: dict[str, dict[str, Any]] = {}
    if ver_rec.sources_json:
        try:
            old_sources_map = json.loads(ver_rec.sources_json)
        except Exception:
            pass
    if not old_sources_map:
        old_sources_map = _backfill_sources_from_db(artifact, ver_rec, db_session)

    existing_sids = [int(k) for k in old_sources_map.keys()]
    if not existing_sids and ver_rec.source_ids_json:
        try:
            existing_sids = json.loads(ver_rec.source_ids_json)
        except Exception:
            pass
    if not existing_sids:
        if isinstance(old_model, DocumentModel):
            existing_sids = [
                sid
                for sec in old_model.sections
                for b in sec.blocks
                for sid in getattr(b, "source_ids", [])
            ]
        else:
            existing_sids = [
                sid for sl in old_model.slides for sid in sl.source_ids
            ]

    max_existing_id = max(existing_sids + [0])
    start_id = max_existing_id + 1

    # Check if edit adds/expands a topic
    import re
    is_topic_addition = bool(
        re.search(
            r"\b(add|append|include|also|expand|new\s+topic|more\s+about|information\s+about)\b",
            instruction,
            re.IGNORECASE,
        )
    )

    clean_sub = re.sub(
        r"^(also\s+)?(add\s+a\s+section\s+on|add\s+section\s+on|add\s+|include\s+a\s+section\s+on|include\s+|append\s+)",
        "",
        instruction,
        flags=re.IGNORECASE,
    ).strip()

    fresh_pack: list[EvidenceItem] = []
    fresh_sources_map: dict[Any, dict[str, Any]] = {}
    no_sources_warning: str | None = None

    if shared_context:
        fresh_pack = shared_context.evidence_pack or []
        fresh_sources_map = shared_context.sources_map or {}
    elif is_topic_addition:
        query = (
            f"{active_topic} {clean_sub}"
            if (active_topic and active_topic.lower() not in clean_sub.lower())
            else (clean_sub or instruction)
        )
        try:
            ctx = build_context(
                query,
                use_web=True,
                use_kb=True,
                db_session=db_session,
                start_id=start_id,
            )
            fresh_pack = ctx.evidence_pack or []
            fresh_sources_map = ctx.sources_map or {}
        except Exception as r_exc:
            logger.warning(f"Scoped research failed during edit: {r_exc}")
            fresh_pack = []
            fresh_sources_map = {}
    elif evidence_pack:
        fresh_pack = list(evidence_pack)

    if is_topic_addition and not fresh_pack:
        topic_name = clean_sub or instruction
        no_sources_warning = f"No sources found for {topic_name}; added general content without citations."

    # 2. ONE LLM call -> EditPlan
    extra_prompt_err = ""
    if is_topic_addition and not fresh_pack:
        extra_prompt_err = "\nCRITICAL: No sources found for this topic. You MUST write NO specific facts (no numbers, dates, version numbers, names) and attach NO citations (source_ids=[])."

    # A formatting-only request is applied to the model's styles without an LLM call (FR-DOCX-5, ADR-033).
    from app.agents.interpreter import parse_style_changes
    from app.models.style import StyleOverrides
    from app.services.edit_guard import is_pure_restyle, regression

    from app.agents.edit_content import apply_precise_ops, normalize_targets, op_units

    named_targets = normalize_targets(old_model, target_nodes)
    style_changes = parse_style_changes(instruction)
    restyle: StyleOverrides | None = None
    if is_pure_restyle(instruction, style_changes):
        restyle = (old_model.style or StyleOverrides()).model_copy()
        applied = restyle.apply(style_changes)
        edit_plan = EditPlan(target=art_type, ops=[], summary="Restyled: " + "; ".join(applied))
    else:
        edit_plan = _generate_edit_plan(
            instruction=instruction,
            artifact_type=art_type,
            model=old_model,
            profile=profile,
            llm_client=llm,
            evidence_pack=fresh_pack,
            active_topic=active_topic,
            extra_error_context=extra_prompt_err,
            targets=named_targets,
        )

    # 3. Apply Ops
    def _apply(plan: EditPlan) -> Any:
        if art_type == "docx":
            out = _apply_ops_to_docx(old_model, plan.ops)
        elif art_type == "xlsx":
            out = _apply_ops_to_xlsx(old_model, plan.ops)
        else:
            out = _apply_ops_to_pptx(old_model, plan.ops)
        apply_precise_ops(out, plan.ops)
        return out

    new_model = _apply(edit_plan)

    if restyle is not None:
        new_model.style = restyle

    # 4. Check Grounding Rule: new/changed content may cite ONLY fresh_pack IDs
    allowed_new_sids = set(e.id for e in fresh_pack)
    violations = _find_grounding_violations(old_model, new_model, allowed_new_sids)

    if violations and fresh_pack:
        logger.warning(f"Edit grounding violations detected: {violations}. Retrying once...")
        retry_err = (
            f"\nCRITICAL FIX REQUIRED: The following blocks violated grounding by citing old/unrelated sources: "
            f"{'; '.join(violations)}. You may ONLY cite IDs from the fresh evidence pack: {list(allowed_new_sids)}."
        )
        try:
            edit_plan = _generate_edit_plan(
                instruction=instruction,
                artifact_type=art_type,
                model=old_model,
                profile=profile,
                llm_client=llm,
                evidence_pack=fresh_pack,
                active_topic=active_topic,
                extra_error_context=retry_err,
                targets=named_targets,
            )
            new_model = _apply(edit_plan)
        except Exception as retry_exc:
            logger.warning(f"EditPlan retry call failed: {retry_exc}")

    # Fallback safety: strip any lingering invalid IDs if not in allowed_new_sids or old block
    if not fresh_pack and is_topic_addition:
        # Strip all citations on newly added blocks
        if isinstance(new_model, DocumentModel):
            old_h_set = {s.heading.lower() for s in old_model.sections}
            for s in new_model.sections:
                if s.heading.lower() not in old_h_set:
                    for b in s.blocks:
                        b.source_ids = []
        elif isinstance(new_model, DeckModel):
            old_t_set = {sl.title.lower() for sl in old_model.slides}
            for sl in new_model.slides:
                if sl.title.lower() not in old_t_set:
                    sl.source_ids = []
                    for b in (sl.bullets or []) + (sl.left or []) + (sl.right or []):
                        b.source_ids = []

    # 5. Handle special ops: condense_deck & refresh_with_web
    for op in edit_plan.ops:
        op_type = getattr(op, "type", "")
        if op_type == "condense_deck" and isinstance(new_model, DeckModel):
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
            r_ctx = shared_context or build_context(
                active_topic or instruction,
                use_web=True,
                use_kb=True,
                db_session=db_session,
                start_id=start_id,
            )
            r_pack = r_ctx.evidence_pack or []
            r_pack_text = format_evidence_pack(r_pack)
            grounding_rule = (
                "You may only state a specific fact (number, date, statistic, name, claimed event) if it "
                "matches an entry in the EVIDENCE PACK below, citing that entry's exact id as source_id. "
                "General connective text needs no citation but must contain no invented specific fact. "
                "If something isn't covered by the evidence pack, omit it or state it as general knowledge "
                "without invented precision — never fabricate a number or date."
            )
            refresh_prompt = (
                f"Update and refresh content for topic '{active_topic}' using the following FRESH EVIDENCE PACK:\n\n"
                f"--- EVIDENCE PACK ---\n{r_pack_text}\n\n"
                f"CURRENT MODEL:\n{new_model.model_dump_json()}\n\n"
                f"RULES:\n"
                f"1. Rewrite only affected sections or slides using the fresh evidence pack.\n"
                f"2. {grounding_rule}"
            )
            try:
                if isinstance(new_model, DocumentModel):
                    new_model = llm.generate_json(prompt=refresh_prompt, schema=DocumentModel)
                elif isinstance(new_model, WorkbookModel):
                    new_model = llm.generate_json(prompt=refresh_prompt, schema=WorkbookModel)
                else:
                    new_model = llm.generate_json(prompt=refresh_prompt, schema=DeckModel)
                for sid_k, s_v in r_ctx.sources_map.items():
                    fresh_sources_map[sid_k] = s_v
            except Exception as exc:
                logger.warning(f"Refresh with web LLM call failed: {exc}")

    # 6. No re-stamping of dates: an edit leaves the cover and the document date as they were (FR-EDIT-3)
    import datetime
    today_str = datetime.date.today().strftime("%B %Y")

    # 7. Merge Sources Map & Standardize
    merged_sources_map = dict(old_sources_map)
    for sid_k, s_data in fresh_sources_map.items():
        merged_sources_map[str(sid_k)] = s_data

    standard_sources_map: dict[str, dict[str, Any]] = {}
    for sid_k, s_data in merged_sources_map.items():
        cid = int(sid_k)
        u_or_f = s_data.get("url_or_filename") or s_data.get("url", "")
        title = s_data.get("title") or u_or_f or f"Source {cid}"
        accessed = s_data.get("accessed_at", today_str)
        standard_sources_map[str(cid)] = {
            "id": cid,
            "title": title,
            "url_or_filename": u_or_f,
            "kind": s_data.get("kind", "web"),
            "accessed_at": accessed,
        }

    valid_sids_set = set(int(k) for k in standard_sources_map.keys())

    # 8. Keep the edit inside its scope, then compute the diff
    ensure_node_ids(new_model)
    from app.services.edit_guard import restore_out_of_scope

    whole = any(getattr(op, "type", "") in ("condense_deck", "refresh_with_web") for op in edit_plan.ops)
    scope = named_targets or list(dict.fromkeys(
        op_units(old_model, edit_plan.ops) + normalize_targets(old_model, edit_plan.scope_node_ids)))
    restored = restore_out_of_scope(old_model, new_model, scope if (restyle is None and not whole) else None)
    if restored:
        logger.info("Edit touched units outside its scope; restored %s from version %s", restored, ver_no)
    diff = diff_models(old_model, new_model)
    actually_changed_nodes = diff.get("changed", []) + diff.get("added", []) + diff.get("removed", [])

    # 9. Render & Validate
    from app.services.render_context import output_dir as _out

    output_dir = _out()
    next_ver_no = ver_no + 1

    if art_type == "docx":
        out_file = output_dir / f"Proposal_{artifact_id}_v{next_ver_no}.docx"
        render_docx(new_model, tmpl_path, profile, out_file, sources_map=standard_sources_map)
        val_rep = validate_outputs(
            doc_model=new_model,
            valid_source_ids=valid_sids_set,
            target_nodes=target_nodes,
            actually_changed_nodes=actually_changed_nodes,
        )
    elif art_type == "xlsx":
        from app.services.xlsx_renderer import render_xlsx
        out_file = output_dir / f"Workbook_{artifact_id}_v{next_ver_no}.xlsx"
        render_xlsx(new_model, out_file)
        val_rep = validate_outputs(
            workbook_model=new_model,
            valid_source_ids=valid_sids_set,
            target_nodes=target_nodes,
            actually_changed_nodes=actually_changed_nodes,
        )
    else:
        out_file = output_dir / f"Deck_{artifact_id}_v{next_ver_no}.pptx"
        deck_photos = photos_for_deck(new_model, render_ctx)
        old_ids = {s.node_id for s in old_model.slides}
        if render_ctx is not None and any(s.node_id not in old_ids for s in new_model.slides):
            from app.services.images import fetch_deck_photos

            fetched = fetch_deck_photos(new_model, tmpl_path, profile, out_file.parent / "media",
                                        subject=active_topic, existing=deck_photos)
            # Slides kept from the previous version keep exactly the pictures they had; only new slides get one.
            deck_photos.update({i: p for i, p in fetched.items() if new_model.slides[i].node_id not in old_ids})
        render_pptx(new_model, tmpl_path, profile, out_file, sources_map=standard_sources_map, photos=deck_photos,
                    design_hint=render_ctx.design_hint if render_ctx else "")
        val_rep = validate_outputs(
            deck_model=new_model,
            expected_slide_count=len(new_model.slides),
            valid_source_ids=valid_sids_set,
            target_nodes=target_nodes,
            actually_changed_nodes=actually_changed_nodes,
        )

    # Scope error check
    scope_errors = [issue for issue in val_rep.issues if issue.where.startswith("scope:")]
    if scope_errors:
        logger.warning(f"Scope validation failed: {scope_errors[0].message}")

    # Regression guard: units outside the scope stay identical in the model and in the file (FR-EDIT-3)
    guard = regression(old_model, new_model, scope if restyle is None else [], old_file=old_file_path,
                       new_file=out_file, kind=art_type)
    if restyle is not None and guard.changed_units:
        logger.warning("Restyle changed content units %s", guard.changed_units)
    if isinstance(diff, dict):
        diff["regression"] = guard.model_dump() | {"restored_out_of_scope": restored}

    # 10. Update Artifact Title if changed
    if new_model.title and str(artifact.title) != str(new_model.title):
        artifact.title = str(new_model.title)

    # 11. Authoritative Version Creation via versioning service
    from app.services.versioning import create_version_sync
    new_ver_rec = create_version_sync(
        db=db_session,
        artifact_id=artifact_id,
        model_json=new_model.model_dump_json(),
        source_file_path=out_file,
        change_summary=edit_plan.summary or instruction[:150],
        source_ids=list(sorted(valid_sids_set)),
        sources_json=standard_sources_map,
        parent_version_id=ver_id,
        diff_json=diff,
    )
    if art_type == "pptx":
        new_ver_rec.render_json = RenderContext(
            template_path=str(tmpl_path) if tmpl_path else None,
            template_file_id=render_ctx.template_file_id if render_ctx else None,
            mode="template" if tmpl_path else "generated",
            design_hint=render_ctx.design_hint if render_ctx else "",
            photos=deck_photos_by_node(new_model, deck_photos, artifact_id)).to_json()
    elif render_ctx is not None:
        new_ver_rec.render_json = render_ctx.to_json()
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
        no_sources_warning=no_sources_warning,
    )
