"""Deterministic target resolution service.

Resolves conversational target expressions (e.g. 'slide 4', 'slide 4 title',
'introduction', 'conclusion', 'first paragraph', 'table 2') into validated
structural node IDs against the active artifact model.
"""

from __future__ import annotations

import re
from typing import Any, Union
from pydantic import BaseModel, Field

from app.models.deck_model import DeckModel
from app.models.document_model import DocumentModel, ParagraphBlock, TableBlock
from app.models.workbook_model import WorkbookModel
from app.services.artifact_nodes import (
    AnyArtifactModel,
    ensure_node_ids,
    find_node,
    list_all_node_ids,
)


class TargetResolutionResult(BaseModel):
    """Result of deterministic target node resolution."""

    is_valid: bool = Field(description="True if target was resolved to an existing node.")
    target_node_id: str | None = Field(default=None, description="Resolved stable node ID.")
    target_type: str | None = Field(default=None, description="Node type (e.g. slide_title, section, paragraph).")
    parent_node_id: str | None = Field(default=None, description="Parent node ID if applicable.")
    error_code: str | None = Field(default=None, description="Error code if resolution failed (e.g. INVALID_TARGET, TARGET_NOT_FOUND).")
    error_message: str | None = Field(default=None, description="Human-readable error details.")
    resolved_description: str = Field(default="", description="Descriptive label of the target.")


def resolve_target(
    target_ref: str,
    model: AnyArtifactModel,
    context: dict[str, Any] | None = None,
) -> TargetResolutionResult:
    """Deterministically resolve a target reference against the actual artifact structure."""
    if not target_ref or not target_ref.strip():
        return TargetResolutionResult(
            is_valid=False,
            error_code="TARGET_MISSING",
            error_message="No target node or reference specified.",
        )

    ref = target_ref.strip()
    ref_lower = ref.lower()
    ensure_node_ids(model)

    # 1. Exact node_id match check
    node_obj, node_type, parent_obj = find_node(model, ref)
    if node_obj is not None:
        p_id = getattr(parent_obj, "node_id", None) if parent_obj else None
        return TargetResolutionResult(
            is_valid=True,
            target_node_id=ref,
            target_type=node_type,
            parent_node_id=p_id,
            resolved_description=f"Exact node '{ref}' ({node_type})",
        )

    # 2. DeckModel target resolution
    if isinstance(model, DeckModel):
        return _resolve_deck_target(ref_lower, model, context=context)

    # 3. DocumentModel target resolution
    if isinstance(model, DocumentModel):
        return _resolve_document_target(ref_lower, model, context=context)

    # 4. WorkbookModel target resolution
    if isinstance(model, WorkbookModel):
        return _resolve_workbook_target(ref_lower, model, context=context)

    return TargetResolutionResult(
        is_valid=False,
        error_code="UNSUPPORTED_ARTIFACT",
        error_message=f"Unsupported artifact model type {type(model)}",
    )


def _resolve_deck_target(
    ref: str, deck: DeckModel, context: dict[str, Any] | None = None
) -> TargetResolutionResult:
    slide_count = len(deck.slides)

    # Normalize ordinals (e.g. "fifth page" -> "slide 5", "5th slide" -> "slide 5")
    ord_map = {
        "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
        "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    }
    for ord_w, num_v in ord_map.items():
        if f"{ord_w} slide" in ref or f"{ord_w} page" in ref:
            ref = re.sub(rf"\b{ord_w}\s+(?:slide|page)\b", f"slide {num_v}", ref)
    ref = re.sub(r"\b(\d+)(?:st|nd|rd|th)\s+(?:slide|page)\b", r"slide \1", ref)
    ref = re.sub(r"\bpage\s+(\d+)\b", r"slide \1", ref)

    # Match "slide X title" or "title of slide X" or "slide X"
    slide_title_match = re.search(r"\bslide\s+(\d+)\s+(?:title|heading)\b|\btitle\s+(?:of\s+)?slide\s+(\d+)\b", ref)
    if slide_title_match:
        idx_str = slide_title_match.group(1) or slide_title_match.group(2)
        idx = int(idx_str)
        if idx < 1 or idx > slide_count:
            return TargetResolutionResult(
                is_valid=False,
                error_code="INVALID_TARGET",
                error_message=f"Requested slide {idx} does not exist (presentation has {slide_count} slides).",
            )
        slide = deck.slides[idx - 1]
        return TargetResolutionResult(
            is_valid=True,
            target_node_id=f"{slide.node_id}_title",
            target_type="slide_title",
            parent_node_id=slide.node_id,
            resolved_description=f"Slide {idx} Title ({slide.node_id}_title)",
        )

    # Match "slide X notes"
    slide_notes_match = re.search(r"\bslide\s+(\d+)\s+notes\b|\bnotes\s+(?:of\s+)?slide\s+(\d+)\b", ref)
    if slide_notes_match:
        idx_str = slide_notes_match.group(1) or slide_notes_match.group(2)
        idx = int(idx_str)
        if idx < 1 or idx > slide_count:
            return TargetResolutionResult(
                is_valid=False,
                error_code="INVALID_TARGET",
                error_message=f"Requested slide {idx} does not exist (presentation has {slide_count} slides).",
            )
        slide = deck.slides[idx - 1]
        return TargetResolutionResult(
            is_valid=True,
            target_node_id=f"{slide.node_id}_notes",
            target_type="slide_notes",
            parent_node_id=slide.node_id,
            resolved_description=f"Slide {idx} Notes ({slide.node_id}_notes)",
        )

    # Match "slide X"
    slide_match = re.search(r"\bslide\s+(\d+)\b", ref)
    if slide_match:
        idx = int(slide_match.group(1))
        if idx < 1 or idx > slide_count:
            return TargetResolutionResult(
                is_valid=False,
                error_code="INVALID_TARGET",
                error_message=f"Requested slide {idx} does not exist (presentation has {slide_count} slides).",
            )
        slide = deck.slides[idx - 1]
        return TargetResolutionResult(
            is_valid=True,
            target_node_id=slide.node_id,
            target_type="slide",
            parent_node_id=deck.node_id,
            resolved_description=f"Slide {idx} ({slide.node_id})",
        )

    # Match "previous slide" or "prior slide"
    if "previous slide" in ref or "prior slide" in ref or "the previous slide" in ref:
        if not deck.slides:
            return TargetResolutionResult(
                is_valid=False,
                error_code="INVALID_TARGET",
                error_message="Presentation contains no slides.",
            )
        target_idx = None
        if context:
            last_targets = context.get("last_target_nodes") or context.get("target_nodes") or []
            if isinstance(last_targets, str):
                last_targets = [last_targets]
            for lt in last_targets:
                m = re.search(r"\b(\d+)\b", str(lt))
                if m:
                    val = int(m.group(1))
                    if val > 1:
                        target_idx = val - 1
                        break
        if target_idx is None:
            target_idx = max(1, len(deck.slides) - 1)
        slide = deck.slides[target_idx - 1]
        return TargetResolutionResult(
            is_valid=True,
            target_node_id=slide.node_id,
            target_type="slide",
            parent_node_id=deck.node_id,
            resolved_description=f"Previous Slide ({slide.node_id}, slide {target_idx})",
        )

    # Match "last slide" or "final slide" or "conclusion slide"
    if "last slide" in ref or "final slide" in ref or "conclusion slide" in ref:
        if not deck.slides:
            return TargetResolutionResult(
                is_valid=False,
                error_code="INVALID_TARGET",
                error_message="Presentation contains no slides.",
            )
        slide = deck.slides[-1]
        return TargetResolutionResult(
            is_valid=True,
            target_node_id=slide.node_id,
            target_type="slide",
            parent_node_id=deck.node_id,
            resolved_description=f"Last Slide ({slide.node_id})",
        )

    # Match by title keyword (e.g. "introduction", "conclusion", "agenda")
    for s_idx, slide in enumerate(deck.slides, start=1):
        if ref in slide.title.lower():
            return TargetResolutionResult(
                is_valid=True,
                target_node_id=slide.node_id,
                target_type="slide",
                parent_node_id=deck.node_id,
                resolved_description=f"Slide '{slide.title}' ({slide.node_id})",
            )

    # Entire presentation
    if "presentation" in ref or "the deck" in ref or "all slides" in ref:
        return TargetResolutionResult(
            is_valid=True,
            target_node_id=deck.node_id,
            target_type="root",
            parent_node_id=None,
            resolved_description=f"Presentation Root ({deck.node_id})",
        )

    return TargetResolutionResult(
        is_valid=False,
        error_code="TARGET_NOT_FOUND",
        error_message=f"Could not resolve target reference '{ref}' in presentation.",
    )


def _resolve_document_target(
    ref: str, doc: DocumentModel, context: dict[str, Any] | None = None
) -> TargetResolutionResult:
    sec_count = len(doc.sections)

    # Normalize ordinals (e.g. "fifth page" -> "section 5", "5th page" -> "section 5")
    ord_map = {
        "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
        "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    }
    for ord_w, num_v in ord_map.items():
        if f"{ord_w} section" in ref or f"{ord_w} page" in ref:
            ref = re.sub(rf"\b{ord_w}\s+(?:section|page)\b", f"section {num_v}", ref)
    ref = re.sub(r"\b(\d+)(?:st|nd|rd|th)\s+(?:section|page)\b", r"section \1", ref)
    ref = re.sub(r"\bpage\s+(\d+)\b", r"section \1", ref)

    # Match "section X"
    sec_match = re.search(r"\bsection\s+(\d+)\b", ref)
    if sec_match:
        idx = int(sec_match.group(1))
        if idx < 1 or idx > sec_count:
            return TargetResolutionResult(
                is_valid=False,
                error_code="INVALID_TARGET",
                error_message=f"Requested section {idx} does not exist (document has {sec_count} sections).",
            )
        sec = doc.sections[idx - 1]
        return TargetResolutionResult(
            is_valid=True,
            target_node_id=sec.node_id,
            target_type="section",
            parent_node_id=doc.node_id,
            resolved_description=f"Section {idx}: '{sec.heading}' ({sec.node_id})",
        )

    # Match named sections (e.g. "introduction", "conclusion", "executive summary", "methodology")
    for sec in doc.sections:
        h_low = sec.heading.lower()
        if ref in h_low or any(w in h_low for w in ref.split() if len(w) > 3):
            # Check if user specifically requested a paragraph in that section
            if "paragraph" in ref:
                paras = [b for b in sec.blocks if isinstance(b, ParagraphBlock) or getattr(b, "type", "") == "paragraph"]
                if paras:
                    if "last" in ref:
                        target_p = paras[-1]
                    else:
                        target_p = paras[0]
                    return TargetResolutionResult(
                        is_valid=True,
                        target_node_id=target_p.node_id,
                        target_type="paragraph",
                        parent_node_id=sec.node_id,
                        resolved_description=f"Paragraph in Section '{sec.heading}' ({target_p.node_id})",
                    )
            return TargetResolutionResult(
                is_valid=True,
                target_node_id=sec.node_id,
                target_type="section",
                parent_node_id=doc.node_id,
                resolved_description=f"Section '{sec.heading}' ({sec.node_id})",
            )

    # Match "first paragraph" or "last paragraph" across whole doc
    all_paras = [
        (sec, blk)
        for sec in doc.sections
        for blk in sec.blocks
        if isinstance(blk, ParagraphBlock) or getattr(blk, "type", "") == "paragraph"
    ]
    if "first paragraph" in ref or "introductory paragraph" in ref:
        if all_paras:
            sec, p = all_paras[0]
            return TargetResolutionResult(
                is_valid=True,
                target_node_id=p.node_id,
                target_type="paragraph",
                parent_node_id=sec.node_id,
                resolved_description=f"First Paragraph ({p.node_id})",
            )
    if "last paragraph" in ref or "final paragraph" in ref:
        if all_paras:
            sec, p = all_paras[-1]
            return TargetResolutionResult(
                is_valid=True,
                target_node_id=p.node_id,
                target_type="paragraph",
                parent_node_id=sec.node_id,
                resolved_description=f"Last Paragraph ({p.node_id})",
            )

    # Match "table X"
    table_match = re.search(r"\btable\s+(\d+)\b", ref)
    if table_match:
        t_idx = int(table_match.group(1))
        all_tables = [
            (sec, blk)
            for sec in doc.sections
            for blk in sec.blocks
            if isinstance(blk, TableBlock) or getattr(blk, "type", "") == "table"
        ]
        if t_idx < 1 or t_idx > len(all_tables):
            return TargetResolutionResult(
                is_valid=False,
                error_code="INVALID_TARGET",
                error_message=f"Requested table {t_idx} does not exist (document has {len(all_tables)} tables).",
            )
        sec, t = all_tables[t_idx - 1]
        return TargetResolutionResult(
            is_valid=True,
            target_node_id=t.node_id,
            target_type="table",
            parent_node_id=sec.node_id,
            resolved_description=f"Table {t_idx} ({t.node_id})",
        )

    # Document title
    if "title" in ref and ("document" in ref or "report" in ref or len(ref.split()) <= 2):
        return TargetResolutionResult(
            is_valid=True,
            target_node_id=f"{doc.node_id}_title",
            target_type="document_title",
            parent_node_id=doc.node_id,
            resolved_description=f"Document Title ({doc.node_id}_title)",
        )

    # Root document
    if "the document" in ref or "entire document" in ref or "whole report" in ref:
        return TargetResolutionResult(
            is_valid=True,
            target_node_id=doc.node_id,
            target_type="root",
            parent_node_id=None,
            resolved_description=f"Document Root ({doc.node_id})",
        )

    return TargetResolutionResult(
        is_valid=False,
        error_code="TARGET_NOT_FOUND",
        error_message=f"Could not resolve target reference '{ref}' in document.",
    )


def _resolve_workbook_target(
    ref: str, wb: WorkbookModel, context: dict[str, Any] | None = None
) -> TargetResolutionResult:
    # Match cell (e.g. "cell A1" or "A1")
    cell_match = re.search(r"\b(?:cell\s+)?([A-Za-z]+[0-9]+)\b", ref)
    if cell_match:
        coord = cell_match.group(1).upper()
        safe_id = f"cell_{coord}"
        return TargetResolutionResult(
            is_valid=True,
            target_node_id=safe_id,
            target_type="cell",
            parent_node_id=wb.sheets[0].node_id if wb.sheets else None,
            resolved_description=f"Cell {coord} ({safe_id})",
        )

    # Match sheet N
    sheet_match = re.search(r"\bsheet\s+(\d+)\b", ref)
    if sheet_match:
        s_idx = int(sheet_match.group(1))
        if s_idx < 1 or s_idx > len(wb.sheets):
            return TargetResolutionResult(
                is_valid=False,
                error_code="INVALID_TARGET",
                error_message=f"Requested sheet {s_idx} does not exist (workbook has {len(wb.sheets)} sheets).",
            )
        sheet = wb.sheets[s_idx - 1]
        return TargetResolutionResult(
            is_valid=True,
            target_node_id=sheet.node_id,
            target_type="sheet",
            parent_node_id=wb.node_id,
            resolved_description=f"Sheet {s_idx}: '{sheet.name}' ({sheet.node_id})",
        )

    # Match chart
    if "chart" in ref:
        for s in wb.sheets:
            if s.charts:
                ch = s.charts[0]
                return TargetResolutionResult(
                    is_valid=True,
                    target_node_id=ch.node_id,
                    target_type="chart",
                    parent_node_id=s.node_id,
                    resolved_description=f"Chart '{ch.title}' ({ch.node_id})",
                )

    return TargetResolutionResult(
        is_valid=False,
        error_code="TARGET_NOT_FOUND",
        error_message=f"Could not resolve target reference '{ref}' in workbook.",
    )
