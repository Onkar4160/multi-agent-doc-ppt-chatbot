"""Template resolution and validation service for Part 2.

Handles template modes (UPLOADED, DEFAULT, EXISTING, NONE), verifies uploaded
file types, enforces explicit error states like TEMPLATE_FILE_REQUIRED, and prevents
silent fallback to default templates when an uploaded template is requested.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Sequence
from pydantic import BaseModel, Field

from app.models.execution_plan import ArtifactType, TemplateMode
from app.models.file import UploadedFile
from app.models.workspace import Workspace

logger = logging.getLogger(__name__)


class TemplateResolutionResult(BaseModel):
    """Result of template resolution and validation."""

    is_valid: bool = Field(description="True if template resolution succeeded without errors.")
    error_code: str | None = Field(
        default=None,
        description="Explicit error code if resolution failed (e.g. TEMPLATE_FILE_REQUIRED, INVALID_TEMPLATE_TYPE).",
    )
    error_message: str | None = Field(default=None, description="Human-readable explanation of error.")
    resolved_mode: str = "DEFAULT"
    docx_template_file_id: int | None = None
    docx_template_path: str | None = None
    pptx_template_file_id: int | None = None
    pptx_template_path: str | None = None


def resolve_templates(
    template_mode: TemplateMode | str,
    artifact_types: Sequence[ArtifactType | str],
    file_ids: Sequence[int] | None = None,
    uploaded_files: Sequence[UploadedFile] | None = None,
    active_workspace: Workspace | None = None,
    user_instruction: str = "",
) -> TemplateResolutionResult:
    """Deterministically resolve and validate template files.

    Enforces strict rules:
    - If UPLOADED is requested and no file is provided: return TEMPLATE_FILE_REQUIRED.
    - If file type doesn't match requested artifact type: return INVALID_TEMPLATE_TYPE.
    - Never silently fall back to default template when UPLOADED is requested.
    """
    mode_str = template_mode.value.upper() if isinstance(template_mode, TemplateMode) else str(template_mode).upper()
    file_ids = list(file_ids or [])
    available_files = list(uploaded_files or [])

    # Filter available files to matching file_ids if specified
    if file_ids and available_files:
        available_files = [f for f in available_files if f.id in file_ids]

    # Convert artifact_types to string set
    art_type_strs = {
        (at.value.lower() if isinstance(at, ArtifactType) else str(at).lower())
        for at in artifact_types
    }
    # If UNKNOWN or empty, default to checking DOCX and PPTX
    if not art_type_strs or "unknown" in art_type_strs:
        art_type_strs = {"docx", "pptx"}

    # ── 1. UPLOADED TEMPLATE MODE ─────────────────────────────────────────────
    if mode_str == "UPLOADED":
        if not available_files and not file_ids:
            return TemplateResolutionResult(
                is_valid=False,
                error_code="TEMPLATE_FILE_REQUIRED",
                error_message="An uploaded template was requested, but no template file has been uploaded.",
                resolved_mode="UPLOADED",
            )

        docx_id: int | None = None
        docx_path: str | None = None
        pptx_id: int | None = None
        pptx_path: str | None = None

        # Check DOCX requirement
        if "docx" in art_type_strs:
            docx_candidates = [
                f for f in available_files if f.file_type.lower() in ("docx", "doc")
            ]
            if not docx_candidates:
                # User uploaded a file, but not a DOCX (e.g. PNG or PDF)
                non_matching = [f.file_type.upper() for f in available_files]
                non_matching_str = ", ".join(non_matching) if non_matching else "None"
                return TemplateResolutionResult(
                    is_valid=False,
                    error_code="INVALID_TEMPLATE_TYPE",
                    error_message=f"Uploaded file type ({non_matching_str}) is not a valid DOCX template.",
                    resolved_mode="UPLOADED",
                )

            # Resolve best candidate (prefer purpose=TEMPLATE or filename match)
            chosen_docx = _pick_best_candidate(docx_candidates, user_instruction)
            docx_id = chosen_docx.id
            docx_path = chosen_docx.stored_path

        # Check PPTX requirement
        if "pptx" in art_type_strs:
            pptx_candidates = [
                f for f in available_files if f.file_type.lower() in ("pptx", "ppt")
            ]
            if not pptx_candidates:
                non_matching = [f.file_type.upper() for f in available_files]
                non_matching_str = ", ".join(non_matching) if non_matching else "None"
                return TemplateResolutionResult(
                    is_valid=False,
                    error_code="INVALID_TEMPLATE_TYPE",
                    error_message=f"Uploaded file type ({non_matching_str}) is not a valid PPTX template.",
                    resolved_mode="UPLOADED",
                )

            chosen_pptx = _pick_best_candidate(pptx_candidates, user_instruction)
            pptx_id = chosen_pptx.id
            pptx_path = chosen_pptx.stored_path

        return TemplateResolutionResult(
            is_valid=True,
            resolved_mode="UPLOADED",
            docx_template_file_id=docx_id,
            docx_template_path=docx_path,
            pptx_template_file_id=pptx_id,
            pptx_template_path=pptx_path,
        )

    # ── 2. DEFAULT TEMPLATE MODE ──────────────────────────────────────────────
    if mode_str == "DEFAULT" or mode_str == "EXISTING":
        docx_id = getattr(active_workspace, "docx_template_file_id", None) if active_workspace else None
        pptx_id = getattr(active_workspace, "pptx_template_file_id", None) if active_workspace else None

        docx_path = None
        pptx_path = None
        if available_files:
            for f in available_files:
                if f.id == docx_id:
                    docx_path = f.stored_path
                elif f.id == pptx_id:
                    pptx_path = f.stored_path

        # Fallback to sample templates paths if not in DB
        if not docx_path:
            sample_d = Path("data/sample_templates/proposal_Template.docx")
            if sample_d.exists():
                docx_path = str(sample_d)
        if not pptx_path:
            sample_p = Path("data/sample_templates/presentation_Template.pptx")
            if sample_p.exists():
                pptx_path = str(sample_p)

        return TemplateResolutionResult(
            is_valid=True,
            resolved_mode=mode_str,
            docx_template_file_id=docx_id,
            docx_template_path=docx_path,
            pptx_template_file_id=pptx_id,
            pptx_template_path=pptx_path,
        )

    # ── 3. NONE TEMPLATE MODE ─────────────────────────────────────────────────
    return TemplateResolutionResult(
        is_valid=True,
        resolved_mode="NONE",
        docx_template_file_id=None,
        docx_template_path=None,
        pptx_template_file_id=None,
        pptx_template_path=None,
    )


def _pick_best_candidate(
    candidates: list[UploadedFile], user_instruction: str
) -> UploadedFile:
    """Pick the most appropriate candidate file among multiple uploaded files."""
    if len(candidates) == 1:
        return candidates[0]

    # 1. Prefer candidate whose purpose is explicitly TEMPLATE
    template_purposed = [
        c for c in candidates if getattr(c, "purpose", "").upper() == "TEMPLATE"
    ]
    if len(template_purposed) == 1:
        return template_purposed[0]
    elif len(template_purposed) > 1:
        candidates = template_purposed

    # 2. Check if user prompt mentions filename words
    user_words = set(user_instruction.lower().split())
    for c in candidates:
        fn_words = set(Path(c.filename).stem.lower().replace("_", " ").replace("-", " ").split())
        if user_words & fn_words:
            return c

    # 3. Prefer files with 'template' in their filename
    for c in candidates:
        if "template" in c.filename.lower():
            return c

    # Fallback to the most recently uploaded candidate
    return candidates[-1]
