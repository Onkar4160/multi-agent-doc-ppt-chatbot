"""Diffing service to compute human-readable and structured node diffs between models."""

from __future__ import annotations

import json
from typing import Any, Union

from app.models.deck_model import DeckModel
from app.models.document_model import DocumentModel
from app.models.workbook_model import WorkbookModel
from app.services.artifact_nodes import (
    AnyArtifactModel,
    ensure_node_ids,
    list_all_node_ids,
)


def diff_models(
    old_model: DocumentModel | DeckModel | WorkbookModel | dict[str, Any],
    new_model: DocumentModel | DeckModel | WorkbookModel | dict[str, Any],
    operation: str | None = None,
) -> dict[str, Any]:
    """Compute structured and human-readable diff between an old model and a new model.

    Args:
        old_model: Original model or dict.
        new_model: Modified model or dict.
        operation: Optional explicit operation name (e.g. 'EDIT', 'APPEND').

    Returns:
        Dict with keys:
            'summary', 'added', 'removed', 'changed',
            'operation', 'changed_nodes', 'unchanged_nodes', 'added_nodes', 'deleted_nodes', 'modified_nodes'
    """
    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []

    # Convert dict to model if needed
    if isinstance(old_model, dict):
        if "sheets" in old_model:
            old_model = WorkbookModel.model_validate(old_model)
        elif "sections" in old_model:
            old_model = DocumentModel.model_validate(old_model)
        elif "slides" in old_model:
            old_model = DeckModel.model_validate(old_model)

    if isinstance(new_model, dict):
        if "sheets" in new_model:
            new_model = WorkbookModel.model_validate(new_model)
        elif "sections" in new_model:
            new_model = DocumentModel.model_validate(new_model)
        elif "slides" in new_model:
            new_model = DeckModel.model_validate(new_model)

    changed_nodes: list[dict[str, Any]] = []
    added_nodes: list[str] = []
    deleted_nodes: list[str] = []
    modified_nodes: list[str] = []
    unchanged_nodes: list[str] = []

    # 1. DocumentModel Diff
    if isinstance(old_model, DocumentModel) and isinstance(new_model, DocumentModel):
        ensure_node_ids(old_model)
        ensure_node_ids(new_model)

        old_sec_map = {s.node_id: s for s in old_model.sections if s.node_id}
        new_sec_map = {s.node_id: s for s in new_model.sections if s.node_id}

        old_headings = {s.heading: s for s in old_model.sections}
        new_headings = {s.heading: s for s in new_model.sections}

        for h in new_headings:
            if h not in old_headings:
                added.append(f"Section '{h}'")
            else:
                old_sec_json = old_headings[h].model_dump_json()
                new_sec_json = new_headings[h].model_dump_json()
                if old_sec_json != new_sec_json:
                    changed.append(f"Section '{h}'")

        for h in old_headings:
            if h not in new_headings:
                removed.append(f"Section '{h}'")

        # Track node-level diff
        old_ids = set(list_all_node_ids(old_model))
        new_ids = set(list_all_node_ids(new_model))

        for nid in new_ids - old_ids:
            added_nodes.append(nid)
        for nid in old_ids - new_ids:
            deleted_nodes.append(nid)

        common_ids = old_ids & new_ids
        for nid in common_ids:
            # Check if block/section content changed
            old_sec = old_sec_map.get(nid)
            new_sec = new_sec_map.get(nid)
            if old_sec and new_sec:
                if old_sec.model_dump_json() != new_sec.model_dump_json():
                    changed_nodes.append({"node_id": nid, "change_type": "UPDATED"})
                    modified_nodes.append(nid)
                else:
                    unchanged_nodes.append(nid)
            else:
                unchanged_nodes.append(nid)

        # Check granular block level diffs
        old_blk_map = {b.node_id: b for s in old_model.sections for b in s.blocks if b.node_id}
        new_blk_map = {b.node_id: b for s in new_model.sections for b in s.blocks if b.node_id}
        for b_id, new_b in new_blk_map.items():
            if b_id in old_blk_map:
                if old_blk_map[b_id].model_dump_json() != new_b.model_dump_json():
                    if b_id not in modified_nodes:
                        changed_nodes.append({"node_id": b_id, "change_type": "UPDATED"})
                        modified_nodes.append(b_id)
                        changed.append(f"Block '{b_id}'")

    # 2. DeckModel Diff
    elif isinstance(old_model, DeckModel) and isinstance(new_model, DeckModel):
        ensure_node_ids(old_model)
        ensure_node_ids(new_model)

        old_slides = old_model.slides
        new_slides = new_model.slides

        old_slide_map = {s.node_id: s for s in old_slides if s.node_id}
        new_slide_map = {s.node_id: s for s in new_slides if s.node_id}

        max_len = max(len(old_slides), len(new_slides))
        for idx in range(1, max_len + 1):
            s_old = old_slides[idx - 1] if idx <= len(old_slides) else None
            s_new = new_slides[idx - 1] if idx <= len(new_slides) else None

            if s_new and not s_old:
                added.append(f"Slide {idx}: '{s_new.title}'")
            elif s_old and not s_new:
                removed.append(f"Slide {idx}: '{s_old.title}'")
            elif s_old and s_new:
                if s_old.model_dump_json() != s_new.model_dump_json():
                    changed.append(f"Slide {idx}: '{s_new.title}'")

        old_ids = set(list_all_node_ids(old_model))
        new_ids = set(list_all_node_ids(new_model))

        for nid in new_ids - old_ids:
            added_nodes.append(nid)
        for nid in old_ids - new_ids:
            deleted_nodes.append(nid)

        for nid in old_ids & new_ids:
            if nid in old_slide_map and nid in new_slide_map:
                s_o = old_slide_map[nid]
                s_n = new_slide_map[nid]
                if s_o.title != s_n.title:
                    changed_nodes.append({"node_id": f"{nid}_title", "change_type": "UPDATED"})
                    modified_nodes.append(f"{nid}_title")
                if s_o.model_dump_json() != s_n.model_dump_json():
                    changed_nodes.append({"node_id": nid, "change_type": "UPDATED"})
                    modified_nodes.append(nid)
                else:
                    unchanged_nodes.append(nid)
            else:
                unchanged_nodes.append(nid)

    # 3. WorkbookModel Diff
    elif isinstance(old_model, WorkbookModel) and isinstance(new_model, WorkbookModel):
        ensure_node_ids(old_model)
        ensure_node_ids(new_model)

        old_ids = set(list_all_node_ids(old_model))
        new_ids = set(list_all_node_ids(new_model))

        for nid in new_ids - old_ids:
            added_nodes.append(nid)
            added.append(f"Node '{nid}'")
        for nid in old_ids - new_ids:
            deleted_nodes.append(nid)
            removed.append(f"Node '{nid}'")

        # Check sheets
        old_sheet_map = {s.name.lower(): s for s in old_model.sheets}
        new_sheet_map = {s.name.lower(): s for s in new_model.sheets}
        for s_name, new_s in new_sheet_map.items():
            if s_name not in old_sheet_map:
                added.append(f"Sheet '{new_s.name}'")
            else:
                old_s = old_sheet_map[s_name]
                if old_s.model_dump_json() != new_s.model_dump_json():
                    changed.append(f"Sheet '{new_s.name}'")
        for s_name, old_s in old_sheet_map.items():
            if s_name not in new_sheet_map:
                removed.append(f"Sheet '{old_s.name}'")

        # Check cells
        old_cell_map = {c.coordinate.upper(): c for s in old_model.sheets for c in s.cells}
        new_cell_map = {c.coordinate.upper(): c for s in new_model.sheets for c in s.cells}
        for coord, new_c in new_cell_map.items():
            safe_id = f"cell_{coord}"
            if coord in old_cell_map:
                old_c = old_cell_map[coord]
                if old_c.value != new_c.value or old_c.formula != new_c.formula:
                    changed_nodes.append({"node_id": safe_id, "change_type": "UPDATED"})
                    modified_nodes.append(safe_id)
                    changed.append(f"Cell {coord}")
            else:
                added_nodes.append(safe_id)
                changed.append(f"Cell {coord} added")

        for nid in old_ids & new_ids:
            if nid not in modified_nodes:
                unchanged_nodes.append(nid)

    summary_parts = []
    if added:
        summary_parts.append(f"Added {len(added)} item(s)")
    if removed:
        summary_parts.append(f"Removed {len(removed)} item(s)")
    if changed:
        summary_parts.append(f"Changed {len(changed)} item(s)")

    summary = ", ".join(summary_parts) if summary_parts else "No structural changes detected"
    detected_op = operation or ("APPEND" if added and not removed and not changed else ("EDIT" if changed else "REPLACE"))

    return {
        "operation": detected_op,
        "summary": summary,
        "added": added,
        "removed": removed,
        "changed": changed,
        "changed_nodes": changed_nodes,
        "unchanged_nodes": sorted(list(set(unchanged_nodes))),
        "added_nodes": sorted(added_nodes),
        "deleted_nodes": sorted(deleted_nodes),
        "modified_nodes": sorted(modified_nodes),
    }
