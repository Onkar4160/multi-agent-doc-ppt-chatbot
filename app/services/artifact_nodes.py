"""Node ID management and structural tree utilities for artifacts.

Assigns stable, deterministic node IDs to all structural elements of DOCX, PPTX,
and XLSX models, enabling precise targeted editing and diff tracking.
"""

from __future__ import annotations

import re
from typing import Any, Union

from app.models.deck_model import BulletItem, DeckModel, SlideModel
from app.models.document_model import (
    Block,
    BulletsBlock,
    DocumentModel,
    ParagraphBlock,
    Section,
    TableBlock,
)
from app.models.workbook_model import (
    CellModel,
    ChartModel,
    SheetModel,
    TableModel,
    WorkbookModel,
)

AnyArtifactModel = Union[DocumentModel, DeckModel, WorkbookModel]


def ensure_node_ids(model: AnyArtifactModel) -> AnyArtifactModel:
    """Ensure every structural node in the artifact model has a stable node_id.

    Existing node_ids are preserved so that IDs survive edits and appends.
    """
    if isinstance(model, DocumentModel):
        _ensure_document_node_ids(model)
    elif isinstance(model, DeckModel):
        _ensure_deck_node_ids(model)
    elif isinstance(model, WorkbookModel):
        _ensure_workbook_node_ids(model)
    return model


def _ensure_document_node_ids(doc: DocumentModel) -> None:
    if not doc.node_id:
        doc.node_id = "document_001"

    for s_idx, sec in enumerate(doc.sections, start=1):
        if not sec.node_id:
            sec.node_id = f"section_{s_idx:03d}"

        for b_idx, blk in enumerate(sec.blocks, start=1):
            if blk.node_id:
                continue
            if isinstance(blk, ParagraphBlock) or getattr(blk, "type", "") == "paragraph":
                blk.node_id = f"paragraph_{s_idx:03d}_{b_idx:03d}"
            elif isinstance(blk, BulletsBlock) or getattr(blk, "type", "") == "bullets":
                blk.node_id = f"bullets_{s_idx:03d}_{b_idx:03d}"
            elif isinstance(blk, TableBlock) or getattr(blk, "type", "") == "table":
                blk.node_id = f"table_{s_idx:03d}_{b_idx:03d}"
            else:
                blk.node_id = f"block_{s_idx:03d}_{b_idx:03d}"


def _ensure_deck_node_ids(deck: DeckModel) -> None:
    if not deck.node_id:
        deck.node_id = "presentation_001"

    for s_idx, slide in enumerate(deck.slides, start=1):
        if not slide.node_id:
            slide.node_id = f"slide_{s_idx:03d}"

        for b_idx, bullet in enumerate(slide.bullets, start=1):
            if not bullet.node_id:
                bullet.node_id = f"slide_{s_idx:03d}_bullet_{b_idx:03d}"

        for b_idx, bullet in enumerate(slide.left, start=1):
            if not bullet.node_id:
                bullet.node_id = f"slide_{s_idx:03d}_left_{b_idx:03d}"

        for b_idx, bullet in enumerate(slide.right, start=1):
            if not bullet.node_id:
                bullet.node_id = f"slide_{s_idx:03d}_right_{b_idx:03d}"


def _ensure_workbook_node_ids(wb: WorkbookModel) -> None:
    if not wb.node_id:
        wb.node_id = "workbook_001"

    for s_idx, sheet in enumerate(wb.sheets, start=1):
        if not sheet.node_id:
            sheet.node_id = f"sheet_{s_idx:03d}"

        for cell in sheet.cells:
            if not cell.node_id:
                safe_coord = cell.coordinate.replace(":", "_").replace("$", "")
                cell.node_id = f"cell_{safe_coord}"

        for t_idx, table in enumerate(sheet.tables, start=1):
            if not table.node_id:
                table.node_id = f"table_{s_idx:03d}_{t_idx:03d}"

        for c_idx, chart in enumerate(sheet.charts, start=1):
            if not chart.node_id:
                chart.node_id = f"chart_{s_idx:03d}_{c_idx:03d}"


def list_all_node_ids(model: AnyArtifactModel) -> list[str]:
    """Return a flat ordered list of all node IDs currently in the model."""
    ensure_node_ids(model)
    ids: list[str] = []

    if isinstance(model, DocumentModel):
        if model.node_id:
            ids.append(model.node_id)
        for sec in model.sections:
            if sec.node_id:
                ids.append(sec.node_id)
            for blk in sec.blocks:
                if blk.node_id:
                    ids.append(blk.node_id)

    elif isinstance(model, DeckModel):
        if model.node_id:
            ids.append(model.node_id)
        for slide in model.slides:
            if slide.node_id:
                ids.append(slide.node_id)
                # Virtual sub-nodes for targeting
                ids.append(f"{slide.node_id}_title")
                ids.append(f"{slide.node_id}_notes")
            for b in slide.bullets:
                if b.node_id:
                    ids.append(b.node_id)
            for b in slide.left:
                if b.node_id:
                    ids.append(b.node_id)
            for b in slide.right:
                if b.node_id:
                    ids.append(b.node_id)

    elif isinstance(model, WorkbookModel):
        if model.node_id:
            ids.append(model.node_id)
        for sheet in model.sheets:
            if sheet.node_id:
                ids.append(sheet.node_id)
            for cell in sheet.cells:
                if cell.node_id:
                    ids.append(cell.node_id)
            for table in sheet.tables:
                if table.node_id:
                    ids.append(table.node_id)
            for chart in sheet.charts:
                if chart.node_id:
                    ids.append(chart.node_id)

    return ids


def find_node(
    model: AnyArtifactModel, node_id: str
) -> tuple[Any | None, str | None, Any | None]:
    """Find a node by its node_id or virtual sub-node (e.g. 'slide_004_title').

    Returns:
        (node_obj, node_type, parent_obj) or (None, None, None) if not found.
    """
    ensure_node_ids(model)

    if model.node_id == node_id:
        return model, "root", None

    if isinstance(model, DocumentModel):
        for sec in model.sections:
            if sec.node_id == node_id:
                return sec, "section", model
            # Virtual target for heading
            if f"{sec.node_id}_heading" == node_id:
                return sec.heading, "heading", sec
            for blk in sec.blocks:
                if blk.node_id == node_id:
                    return blk, getattr(blk, "type", "block"), sec

    elif isinstance(model, DeckModel):
        for slide in model.slides:
            if slide.node_id == node_id:
                return slide, "slide", model
            if f"{slide.node_id}_title" == node_id:
                return slide.title, "slide_title", slide
            if f"{slide.node_id}_notes" == node_id:
                return slide.notes, "slide_notes", slide
            for b in slide.bullets:
                if b.node_id == node_id:
                    return b, "bullet", slide
            for b in slide.left:
                if b.node_id == node_id:
                    return b, "bullet_left", slide
            for b in slide.right:
                if b.node_id == node_id:
                    return b, "bullet_right", slide

    elif isinstance(model, WorkbookModel):
        for sheet in model.sheets:
            if sheet.node_id == node_id:
                return sheet, "sheet", model
            for cell in sheet.cells:
                if cell.node_id == node_id:
                    return cell, "cell", sheet
            for table in sheet.tables:
                if table.node_id == node_id:
                    return table, "table", sheet
            for chart in sheet.charts:
                if chart.node_id == node_id:
                    return chart, "chart", sheet

    return None, None, None
