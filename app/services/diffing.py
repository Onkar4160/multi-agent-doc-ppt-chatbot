"""Diffing service to compute human-readable changes between document or deck models."""

from __future__ import annotations

import json
from typing import Any

from app.models.deck_model import DeckModel
from app.models.document_model import DocumentModel


def diff_models(
    old_model: DocumentModel | DeckModel | dict[str, Any],
    new_model: DocumentModel | DeckModel | dict[str, Any],
) -> dict[str, Any]:
    """Compute human-readable diff between an old model and a new model.

    Args:
        old_model: Original DocumentModel, DeckModel, or dict.
        new_model: Modified DocumentModel, DeckModel, or dict.

    Returns:
        Dict with keys: 'summary', 'added', 'removed', 'changed'.
    """
    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []

    # Convert dict to model if needed
    if isinstance(old_model, dict):
        if "sections" in old_model:
            old_model = DocumentModel.model_validate(old_model)
        elif "slides" in old_model:
            old_model = DeckModel.model_validate(old_model)

    if isinstance(new_model, dict):
        if "sections" in new_model:
            new_model = DocumentModel.model_validate(new_model)
        elif "slides" in new_model:
            new_model = DeckModel.model_validate(new_model)

    # 1. DocumentModel Diff
    if isinstance(old_model, DocumentModel) and isinstance(new_model, DocumentModel):
        old_headings = {s.heading: s for s in old_model.sections}
        new_headings = {s.heading: s for s in new_model.sections}

        for h in new_headings:
            if h not in old_headings:
                added.append(f"Section '{h}'")
            else:
                # Compare section blocks
                old_sec_json = old_headings[h].model_dump_json()
                new_sec_json = new_headings[h].model_dump_json()
                if old_sec_json != new_sec_json:
                    changed.append(f"Section '{h}'")

        for h in old_headings:
            if h not in new_headings:
                removed.append(f"Section '{h}'")

    # 2. DeckModel Diff
    elif isinstance(old_model, DeckModel) and isinstance(new_model, DeckModel):
        old_slides = old_model.slides
        new_slides = new_model.slides

        old_titles = {idx + 1: s.title for idx, s in enumerate(old_slides)}
        new_titles = {idx + 1: s.title for idx, s in enumerate(new_slides)}

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

    summary_parts = []
    if added:
        summary_parts.append(f"Added {len(added)} item(s)")
    if removed:
        summary_parts.append(f"Removed {len(removed)} item(s)")
    if changed:
        summary_parts.append(f"Changed {len(changed)} item(s)")

    summary = ", ".join(summary_parts) if summary_parts else "No structural changes detected"

    return {
        "summary": summary,
        "added": added,
        "removed": removed,
        "changed": changed,
    }
