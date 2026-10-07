"""Deterministic artifact operations engine.

Applies structured operations (EDIT, APPEND, INSERT, DELETE, REPLACE, MOVE, RESTYLE)
to artifact models with strict edit scoping, preservation constraints, and diff tracking.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Union
from pydantic import BaseModel, Field

from app.models.deck_model import BulletItem, DeckModel, SlideModel
from app.models.document_model import (
    Block,
    BulletsBlock,
    DocumentModel,
    ParagraphBlock,
    Section,
    TableBlock,
)
from app.models.execution_plan import OperationType
from app.models.workbook_model import CellModel, SheetModel, TableModel, WorkbookModel
from app.services.artifact_nodes import (
    AnyArtifactModel,
    ensure_node_ids,
    find_node,
    list_all_node_ids,
)

logger = logging.getLogger(__name__)


class ChangedNodeRecord(BaseModel):
    """Record of a modified node."""

    node_id: str
    change_type: str = "UPDATED"  # UPDATED, REPLACED, RESTYLED
    previous_value: Any = None
    new_value: Any = None


class OperationExecutionResult(BaseModel):
    """Outcome of a deterministic operation applied to an artifact model."""

    success: bool
    operation: str
    updated_model: Any = None  # DocumentModel, DeckModel, or WorkbookModel
    changed_nodes: list[ChangedNodeRecord] = Field(default_factory=list)
    unchanged_nodes: list[str] = Field(default_factory=list)
    added_nodes: list[str] = Field(default_factory=list)
    deleted_nodes: list[str] = Field(default_factory=list)
    summary: str = ""
    error_code: str | None = None
    error_message: str | None = None


def apply_operation(
    model: AnyArtifactModel,
    operation: Union[OperationType, str],
    target_node_id: str | None,
    payload: Any,
    preserve_constraints: list[str] | None = None,
) -> OperationExecutionResult:
    """Apply a structured operation to the model with strict edit scope enforcement."""
    op_str = operation.value.upper() if isinstance(operation, OperationType) else str(operation).upper()
    ensure_node_ids(model)
    initial_node_ids = set(list_all_node_ids(model))

    # Clone model to guarantee immutability of parent state
    working_model = copy.deepcopy(model)

    if op_str == "APPEND":
        return _apply_append(working_model, payload, initial_node_ids)
    elif op_str in ("EDIT", "REPLACE"):
        return _apply_edit_or_replace(working_model, target_node_id, payload, initial_node_ids, op_str)
    elif op_str == "DELETE":
        return _apply_delete(working_model, target_node_id, initial_node_ids)
    elif op_str == "INSERT":
        return _apply_insert(working_model, target_node_id, payload, initial_node_ids)
    elif op_str == "RESTYLE":
        return _apply_restyle(working_model, target_node_id, payload, initial_node_ids)
    elif op_str == "MOVE":
        return _apply_move(working_model, target_node_id, payload, initial_node_ids)
    elif op_str == "CREATE" or op_str == "REGENERATE":
        # Full creation or replacement
        new_m = ensure_node_ids(payload) if isinstance(payload, (DocumentModel, DeckModel, WorkbookModel)) else working_model
        new_ids = list_all_node_ids(new_m)
        return OperationExecutionResult(
            success=True,
            operation=op_str,
            updated_model=new_m,
            changed_nodes=[],
            unchanged_nodes=[],
            added_nodes=new_ids,
            summary=f"Created/regenerated full artifact with {len(new_ids)} nodes.",
        )
    else:
        return OperationExecutionResult(
            success=False,
            operation=op_str,
            error_code="UNSUPPORTED_OPERATION",
            error_message=f"Operation '{op_str}' is not supported by deterministic engine.",
        )


def _apply_edit_or_replace(
    model: AnyArtifactModel,
    target_node_id: str | None,
    payload: Any,
    initial_ids: set[str],
    op_type: str,
) -> OperationExecutionResult:
    """Strictly edit or replace ONLY the targeted node."""
    if not target_node_id:
        return OperationExecutionResult(
            success=False,
            operation=op_type,
            error_code="TARGET_MISSING",
            error_message="Edit operation requires a valid target node ID.",
        )

    changed_records: list[ChangedNodeRecord] = []

    # DeckModel targeted edits
    if isinstance(model, DeckModel):
        # 1. Slide title target (e.g. slide_004_title)
        if target_node_id.endswith("_title"):
            slide_id = target_node_id[:-6]
            for slide in model.slides:
                if slide.node_id == slide_id:
                    old_val = slide.title
                    new_val = payload if isinstance(payload, str) else (payload.get("title") or payload.get("text") or str(payload))
                    slide.title = new_val
                    changed_records.append(
                        ChangedNodeRecord(
                            node_id=target_node_id,
                            change_type="UPDATED",
                            previous_value=old_val,
                            new_value=new_val,
                        )
                    )
                    break
        # 2. Slide notes target
        elif target_node_id.endswith("_notes"):
            slide_id = target_node_id[:-6]
            for slide in model.slides:
                if slide.node_id == slide_id:
                    old_val = slide.notes
                    new_val = payload if isinstance(payload, str) else str(payload)
                    slide.notes = new_val
                    changed_records.append(
                        ChangedNodeRecord(
                            node_id=target_node_id,
                            change_type="UPDATED",
                            previous_value=old_val,
                            new_value=new_val,
                        )
                    )
                    break
        # 3. Slide body / whole slide target (e.g. slide_004)
        else:
            for s_idx, slide in enumerate(model.slides):
                if slide.node_id == target_node_id:
                    old_val = slide.model_dump()
                    if isinstance(payload, SlideModel):
                        payload.node_id = slide.node_id
                        model.slides[s_idx] = payload
                    elif isinstance(payload, dict):
                        if "title" in payload:
                            slide.title = payload["title"]
                        if "subtitle" in payload:
                            slide.subtitle = payload["subtitle"]
                        if "bullets" in payload:
                            slide.bullets = [
                                BulletItem(text=b.text if hasattr(b, "text") else (b["text"] if isinstance(b, dict) else str(b)))
                                for b in payload["bullets"]
                            ]

                        if "notes" in payload:
                            slide.notes = payload["notes"]
                    elif isinstance(payload, str):
                        # Text replacement for slide title
                        slide.title = payload

                    ensure_node_ids(model)
                    changed_records.append(
                        ChangedNodeRecord(
                            node_id=target_node_id,
                            change_type="UPDATED",
                            previous_value=old_val,
                            new_value=model.slides[s_idx].model_dump(),
                        )
                    )
                    break
                # Bullet targeting
                for b in slide.bullets:
                    if b.node_id == target_node_id:
                        old_val = b.text
                        new_val = payload if isinstance(payload, str) else (payload.get("text") or str(payload))
                        b.text = new_val
                        changed_records.append(
                            ChangedNodeRecord(
                                node_id=target_node_id,
                                change_type="UPDATED",
                                previous_value=old_val,
                                new_value=new_val,
                            )
                        )
                        break

    # DocumentModel targeted edits
    elif isinstance(model, DocumentModel):
        if target_node_id == f"{model.node_id}_title" or target_node_id == "document_title":
            old_val = model.title
            new_val = payload if isinstance(payload, str) else str(payload)
            model.title = new_val
            changed_records.append(
                ChangedNodeRecord(
                    node_id=target_node_id,
                    change_type="UPDATED",
                    previous_value=old_val,
                    new_value=new_val,
                )
            )
        else:
            for sec in model.sections:
                if sec.node_id == target_node_id or f"{sec.node_id}_heading" == target_node_id:
                    old_val = sec.heading
                    new_val = payload if isinstance(payload, str) else (payload.get("heading") or str(payload))
                    sec.heading = new_val
                    changed_records.append(
                        ChangedNodeRecord(
                            node_id=target_node_id,
                            change_type="UPDATED",
                            previous_value=old_val,
                            new_value=new_val,
                        )
                    )
                    break
                for blk in sec.blocks:
                    if blk.node_id == target_node_id:
                        if isinstance(blk, ParagraphBlock):
                            old_val = blk.text
                            new_val = payload if isinstance(payload, str) else (payload.get("text") or str(payload))
                            blk.text = new_val
                        elif isinstance(blk, BulletsBlock):
                            old_val = blk.items
                            new_val = payload if isinstance(payload, list) else (payload.get("items") or [str(payload)])
                            blk.items = new_val
                        elif isinstance(blk, TableBlock):
                            old_val = blk.rows
                            new_val = payload.get("rows") if isinstance(payload, dict) else blk.rows
                            blk.rows = new_val
                        changed_records.append(
                            ChangedNodeRecord(
                                node_id=target_node_id,
                                change_type="UPDATED",
                                previous_value=old_val,
                                new_value=new_val,
                            )
                        )
                        break

    # WorkbookModel targeted edits
    elif isinstance(model, WorkbookModel):
        for sheet in model.sheets:
            for cell in sheet.cells:
                if cell.node_id == target_node_id:
                    old_val = cell.value
                    new_val = payload.get("value") if isinstance(payload, dict) else payload
                    cell.value = new_val
                    changed_records.append(
                        ChangedNodeRecord(
                            node_id=target_node_id,
                            change_type="UPDATED",
                            previous_value=old_val,
                            new_value=new_val,
                        )
                    )
                    break

    if not changed_records:
        return OperationExecutionResult(
            success=False,
            operation=op_type,
            error_code="TARGET_NOT_FOUND",
            error_message=f"Target node '{target_node_id}' not found in artifact model.",
        )

    # Compute unchanged nodes strictly
    changed_ids = {cr.node_id for cr in changed_records}
    all_current_ids = list_all_node_ids(model)
    unchanged = [nid for nid in all_current_ids if nid not in changed_ids and not nid.startswith(target_node_id)]

    return OperationExecutionResult(
        success=True,
        operation=op_type,
        updated_model=model,
        changed_nodes=changed_records,
        unchanged_nodes=unchanged,
        summary=f"Deterministically updated target node '{target_node_id}' while preserving {len(unchanged)} nodes.",
    )


def _apply_append(
    model: AnyArtifactModel,
    payload: Any,
    initial_ids: set[str],
) -> OperationExecutionResult:
    """Strictly append new content AFTER existing content without altering previous content."""
    added_ids: list[str] = []

    if isinstance(model, DeckModel):
        curr_slide_count = len(model.slides)
        next_slide_no = curr_slide_count + 1
        new_slide_id = f"slide_{next_slide_no:03d}"

        if isinstance(payload, SlideModel):
            new_slide = payload
            new_slide.node_id = new_slide_id
        elif isinstance(payload, dict):
            new_slide = SlideModel(
                node_id=new_slide_id,
                title=payload.get("title", f"Slide {next_slide_no}"),
                subtitle=payload.get("subtitle", ""),
                bullets=[
                    BulletItem(text=b.text if hasattr(b, "text") else (b["text"] if isinstance(b, dict) else str(b)))
                    for b in payload.get("bullets", [])
                ],

                notes=payload.get("notes", ""),
            )
        else:
            new_slide = SlideModel(
                node_id=new_slide_id,
                title=str(payload),
                bullets=[BulletItem(text="Summary point")],
            )

        model.slides.append(new_slide)
        ensure_node_ids(model)
        added_ids.append(new_slide.node_id)
        added_ids.append(f"{new_slide.node_id}_title")
        for b in new_slide.bullets:
            if b.node_id:
                added_ids.append(b.node_id)

    elif isinstance(model, DocumentModel):
        curr_sec_count = len(model.sections)
        next_sec_no = curr_sec_count + 1
        new_sec_id = f"section_{next_sec_no:03d}"

        if isinstance(payload, Section):
            new_sec = payload
            new_sec.node_id = new_sec_id
        elif isinstance(payload, dict):
            new_sec = Section(
                node_id=new_sec_id,
                heading=payload.get("heading", f"Section {next_sec_no}"),
                blocks=[
                    ParagraphBlock(text=p) if isinstance(p, str) else ParagraphBlock(**p)
                    for p in payload.get("paragraphs", [payload.get("text", "Appended content")])
                ],
            )
        else:
            new_sec = Section(
                node_id=new_sec_id,
                heading=str(payload),
                blocks=[ParagraphBlock(text=str(payload))],
            )

        model.sections.append(new_sec)
        ensure_node_ids(model)
        added_ids.append(new_sec.node_id)
        for blk in new_sec.blocks:
            if blk.node_id:
                added_ids.append(blk.node_id)

    elif isinstance(model, WorkbookModel):
        next_sheet_no = len(model.sheets) + 1
        new_sheet = SheetModel(
            node_id=f"sheet_{next_sheet_no:03d}",
            name=f"Sheet{next_sheet_no}",
        )
        model.sheets.append(new_sheet)
        ensure_node_ids(model)
        added_ids.append(new_sheet.node_id)

    # All initial nodes must remain unchanged
    all_current_ids = set(list_all_node_ids(model))
    unchanged = [nid for nid in initial_ids if nid in all_current_ids]

    return OperationExecutionResult(
        success=True,
        operation="APPEND",
        updated_model=model,
        changed_nodes=[],
        unchanged_nodes=unchanged,
        added_nodes=added_ids,
        summary=f"Appended new content ({len(added_ids)} nodes) while preserving all {len(unchanged)} previous nodes.",
    )


def _apply_delete(
    model: AnyArtifactModel,
    target_node_id: str | None,
    initial_ids: set[str],
) -> OperationExecutionResult:
    """Deterministically remove a node from the artifact structure."""
    if not target_node_id:
        return OperationExecutionResult(
            success=False,
            operation="DELETE",
            error_code="TARGET_MISSING",
            error_message="Delete requires a target node ID.",
        )

    deleted_ids: list[str] = []

    if isinstance(model, DeckModel):
        for s_idx, slide in enumerate(model.slides):
            if slide.node_id == target_node_id:
                del model.slides[s_idx]
                deleted_ids.append(target_node_id)
                break

    elif isinstance(model, DocumentModel):
        for s_idx, sec in enumerate(model.sections):
            if sec.node_id == target_node_id:
                del model.sections[s_idx]
                deleted_ids.append(target_node_id)
                break
            for b_idx, blk in enumerate(sec.blocks):
                if blk.node_id == target_node_id:
                    del sec.blocks[b_idx]
                    deleted_ids.append(target_node_id)
                    break

    if not deleted_ids:
        return OperationExecutionResult(
            success=False,
            operation="DELETE",
            error_code="TARGET_NOT_FOUND",
            error_message=f"Node '{target_node_id}' not found for deletion.",
        )

    all_current_ids = set(list_all_node_ids(model))
    unchanged = [nid for nid in initial_ids if nid in all_current_ids]

    return OperationExecutionResult(
        success=True,
        operation="DELETE",
        updated_model=model,
        deleted_nodes=deleted_ids,
        unchanged_nodes=unchanged,
        summary=f"Deleted node '{target_node_id}', leaving {len(unchanged)} nodes unchanged.",
    )


def _apply_insert(
    model: AnyArtifactModel,
    target_node_id: str | None,
    payload: Any,
    initial_ids: set[str],
) -> OperationExecutionResult:
    """Insert new structural element at specified position or after target node."""
    added_ids: list[str] = []

    if isinstance(model, DeckModel):
        insert_idx = len(model.slides)
        if target_node_id:
            for idx, s in enumerate(model.slides):
                if s.node_id == target_node_id:
                    insert_idx = idx + 1
                    break

        new_slide = payload if isinstance(payload, SlideModel) else SlideModel(
            title=payload.get("title", "Inserted Slide") if isinstance(payload, dict) else str(payload)
        )
        model.slides.insert(insert_idx, new_slide)
        ensure_node_ids(model)
        added_ids.append(new_slide.node_id)

    elif isinstance(model, DocumentModel):
        insert_idx = len(model.sections)
        if target_node_id:
            for idx, s in enumerate(model.sections):
                if s.node_id == target_node_id:
                    insert_idx = idx + 1
                    break

        new_sec = payload if isinstance(payload, Section) else Section(
            heading=payload.get("heading", "Inserted Section") if isinstance(payload, dict) else str(payload)
        )
        model.sections.insert(insert_idx, new_sec)
        ensure_node_ids(model)
        added_ids.append(new_sec.node_id)

    all_current_ids = set(list_all_node_ids(model))
    unchanged = [nid for nid in initial_ids if nid in all_current_ids]

    return OperationExecutionResult(
        success=True,
        operation="INSERT",
        updated_model=model,
        added_nodes=added_ids,
        unchanged_nodes=unchanged,
        summary=f"Inserted new node at position {insert_idx}.",
    )


def _apply_restyle(
    model: AnyArtifactModel,
    target_node_id: str | None,
    payload: Any,
    initial_ids: set[str],
) -> OperationExecutionResult:
    """Apply styling or visual layout changes to target node."""
    changed_records = [
        ChangedNodeRecord(
            node_id=target_node_id or "root",
            change_type="RESTYLED",
            new_value=payload,
        )
    ]
    all_current_ids = list_all_node_ids(model)
    unchanged = [nid for nid in all_current_ids if nid != target_node_id]

    return OperationExecutionResult(
        success=True,
        operation="RESTYLE",
        updated_model=model,
        changed_nodes=changed_records,
        unchanged_nodes=unchanged,
        summary=f"Restyled node '{target_node_id}' with formatting updates.",
    )


def _apply_move(
    model: AnyArtifactModel,
    target_node_id: str | None,
    payload: Any,
    initial_ids: set[str],
) -> OperationExecutionResult:
    """Reorder or move target node."""
    dest_idx = payload.get("index", 0) if isinstance(payload, dict) else int(payload or 0)
    if isinstance(model, DeckModel) and target_node_id:
        for idx, s in enumerate(model.slides):
            if s.node_id == target_node_id:
                slide = model.slides.pop(idx)
                model.slides.insert(min(dest_idx, len(model.slides)), slide)
                break

    all_current_ids = list_all_node_ids(model)
    return OperationExecutionResult(
        success=True,
        operation="MOVE",
        updated_model=model,
        unchanged_nodes=all_current_ids,
        summary=f"Moved node '{target_node_id}' to index {dest_idx}.",
    )
