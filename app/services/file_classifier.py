"""File intelligence and semantic role classification service for attached files.

Analyzes the prompt, conversation context, filename, and file structure to classify
attached files into semantic roles (TEMPLATE, SOURCE, REFERENCE, IMAGE, etc.) without
requiring manual user dropdowns or UI configuration.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Sequence, Union

from app.models.file import UploadedFile
from app.models.file_role import FileRole, FileRoleClassification

logger = logging.getLogger(__name__)

TEMPLATE_KEYWORDS = re.compile(
    r"\b("
    r"attached\s+template|"
    r"given\s+template|"
    r"company\s+template|"
    r"custom\s+template|"
    r"my\s+template|"
    r"in\s+(?:the\s+)?(?:given\s+)?template\s+format|"
    r"using\s+(?:the\s+)?(?:given\s+)?template|"
    r"use\s+(?:the\s+)?(?:given\s+)?template|"
    r"template\s+format|"
    r"in\s+this\s+template"
    r")\b",
    re.IGNORECASE,
)

SOURCE_KEYWORDS = re.compile(
    r"\b("
    r"attached\s+research|"
    r"research\s+documents?|"
    r"attached\s+pdf|"
    r"attached\s+information|"
    r"using\s+(?:the\s+)?attached\s+information|"
    r"information\s+from\s+(?:the\s+)?attached|"
    r"attached\s+documents?|"
    r"attached\s+files?|"
    r"attached\s+notes|"
    r"from\s+(?:the\s+)?attached\s+(?:file|document|pdf|notes|data)"
    r")\b",
    re.IGNORECASE,
)


def classify_attached_files(
    files: Sequence[Union[UploadedFile, dict[str, Any], int]] | None = None,
    user_prompt: str = "",
    context: dict[str, Any] | None = None,
    file_ids: Sequence[int] | None = None,
) -> list[FileRoleClassification]:
    """Deterministically classify attached files into semantic roles based on prompt and file metadata."""
    resolved_files: list[Any] = []
    if files:
        for f in files:
            if isinstance(f, (int, str)) and str(f).isdigit():
                fid = int(f)
                resolved_files.append({"id": fid, "filename": f"file_{fid}"})
            else:
                resolved_files.append(f)
    elif file_ids:
        for fid in file_ids:
            resolved_files.append({"id": int(fid), "filename": f"file_{fid}"})

    # Try resolving filenames from database if only IDs are known
    ids_to_fetch = [
        f["id"] for f in resolved_files
        if isinstance(f, dict) and f.get("filename", "").startswith("file_")
    ]
    if ids_to_fetch:
        try:
            from app.agents.graph import _get_sync_session
            db_sess = _get_sync_session()
            if db_sess:
                db_files = db_sess.query(UploadedFile).filter(UploadedFile.id.in_(ids_to_fetch)).all()
                db_map = {f.id: f for f in db_files}
                for i, rf in enumerate(resolved_files):
                    if isinstance(rf, dict) and rf.get("id") in db_map:
                        resolved_files[i] = db_map[rf["id"]]
                db_sess.close()
        except Exception:
            pass

    if not resolved_files:
        return []

    prompt = (user_prompt or "").strip()
    prompt_lower = prompt.lower()
    has_template_req = bool(TEMPLATE_KEYWORDS.search(prompt))
    has_source_req = bool(SOURCE_KEYWORDS.search(prompt))

    # Identify requested artifact type from prompt
    wants_ppt = any(w in prompt_lower for w in ["ppt", "slide", "presentation", "deck", "powerpoint"])
    wants_doc = any(w in prompt_lower for w in ["report", "proposal", "document", "doc", "word"])
    wants_sheet = any(w in prompt_lower for w in ["excel", "spreadsheet", "workbook", "sheet"])

    results: list[FileRoleClassification] = []

    # First pass: categorize candidate files
    doc_candidates = []
    ppt_candidates = []
    sheet_candidates = []
    pdf_candidates = []
    img_candidates = []

    for f in resolved_files:
        f_id = getattr(f, "id", None) if not isinstance(f, dict) else f.get("id")
        f_name = getattr(f, "filename", "") if not isinstance(f, dict) else f.get("filename", "")
        f_type = (getattr(f, "file_type", "") if not isinstance(f, dict) else f.get("file_type", "")).lower().lstrip(".")
        if not f_type and f_name:
            f_type = Path(f_name).suffix.lower().lstrip(".")

        file_tuple = (f_id, f_name, f_type, f)
        if f_type in ("docx", "doc"):
            doc_candidates.append(file_tuple)
        elif f_type in ("pptx", "ppt"):
            ppt_candidates.append(file_tuple)
        elif f_type in ("xlsx", "xls"):
            sheet_candidates.append(file_tuple)
        elif f_type == "pdf":
            pdf_candidates.append(file_tuple)
        elif f_type in ("png", "jpg", "jpeg", "webp"):
            img_candidates.append(file_tuple)
        else:
            doc_candidates.append(file_tuple)

    # 1. Images are always IMAGE
    for f_id, f_name, f_type, _ in img_candidates:
        results.append(
            FileRoleClassification(
                file_id=f_id,
                filename=f_name,
                file_type=f_type,
                role=FileRole.IMAGE,
                confidence=1.0,
                reason="File is an image graphic or diagram.",
            )
        )

    # 2. PDFs are SOURCE / REFERENCE materials
    for f_id, f_name, f_type, _ in pdf_candidates:
        results.append(
            FileRoleClassification(
                file_id=f_id,
                filename=f_name,
                file_type=f_type,
                role=FileRole.SOURCE,
                confidence=0.98,
                reason="PDF document provided as factual reference material.",
            )
        )

    # 3. Classify DOCX candidates
    _classify_document_type_candidates(
        candidates=doc_candidates,
        has_template_req=has_template_req,
        wants_type=wants_doc or (not wants_ppt and not wants_sheet),
        type_name="DOCX",
        results=results,
        user_prompt=prompt_lower,
    )

    # 4. Classify PPTX candidates
    _classify_document_type_candidates(
        candidates=ppt_candidates,
        has_template_req=has_template_req,
        wants_type=wants_ppt,
        type_name="PPTX",
        results=results,
        user_prompt=prompt_lower,
    )

    # 5. Classify XLSX candidates
    _classify_document_type_candidates(
        candidates=sheet_candidates,
        has_template_req=has_template_req,
        wants_type=wants_sheet,
        type_name="XLSX",
        results=results,
        user_prompt=prompt_lower,
    )

    return results


def _classify_document_type_candidates(
    candidates: list[tuple[Any, str, str, Any]],
    has_template_req: bool,
    wants_type: bool,
    type_name: str,
    results: list[FileRoleClassification],
    user_prompt: str = "",
) -> None:
    """Classify editable document format candidates (DOCX, PPTX, XLSX) into TEMPLATE vs SOURCE."""
    if not candidates:
        return

    # Check which candidates have "template" in their filename
    template_named = [c for c in candidates if "template" in c[1].lower()]
    source_named = [
        c
        for c in candidates
        if any(w in c[1].lower() for w in ["research", "notes", "data", "source", "competitor", "analysis", "strategy", "input"])
    ]

    is_ambiguous_template = (
        len(template_named) > 1
        and not any(Path(t[1]).stem.lower() in user_prompt for t in template_named)
    )

    for f_id, f_name, f_type, orig_f in candidates:
        fn_low = f_name.lower()
        purpose_db = (
            getattr(orig_f, "purpose", "") if not isinstance(orig_f, dict) else orig_f.get("purpose", "")
        ).upper()

        if has_template_req and wants_type:
            # User wants a template in this format
            if len(candidates) == 1:
                # Only 1 candidate file of this type -> It is the template
                results.append(
                    FileRoleClassification(
                        file_id=f_id,
                        filename=f_name,
                        file_type=f_type,
                        role=FileRole.TEMPLATE,
                        confidence=0.96,
                        reason=f"User prompt explicitly requested template format and {f_name} is the attached {type_name}.",
                    )
                )
                continue
            elif f_name in [t[1] for t in template_named]:
                if is_ambiguous_template:
                    results.append(
                        FileRoleClassification(
                            file_id=f_id,
                            filename=f_name,
                            file_type=f_type,
                            role=FileRole.TEMPLATE,
                            confidence=0.5,
                            reason=f"Ambiguous template candidate: multiple template files detected ({', '.join(t[1] for t in template_named)}).",
                            is_ambiguous=True,
                        )
                    )
                else:
                    results.append(
                        FileRoleClassification(
                            file_id=f_id,
                            filename=f_name,
                            file_type=f_type,
                            role=FileRole.TEMPLATE,
                            confidence=0.98,
                            reason="Filename matches 'template' and prompt explicitly requests template format.",
                        )
                    )
                continue
            elif purpose_db == "TEMPLATE":
                results.append(
                    FileRoleClassification(
                        file_id=f_id,
                        filename=f_name,
                        file_type=f_type,
                        role=FileRole.TEMPLATE,
                        confidence=0.95,
                        reason="File purpose is designated as TEMPLATE.",
                    )
                )
                continue
            else:
                # Multiple candidates and this one is not explicitly named template
                results.append(
                    FileRoleClassification(
                        file_id=f_id,
                        filename=f_name,
                        file_type=f_type,
                        role=FileRole.SOURCE,
                        confidence=0.88,
                        reason="File provided alongside template as source reference material.",
                    )
                )
                continue

        # No explicit template request in prompt
        if "template" in fn_low:
            if is_ambiguous_template:
                results.append(
                    FileRoleClassification(
                        file_id=f_id,
                        filename=f_name,
                        file_type=f_type,
                        role=FileRole.TEMPLATE,
                        confidence=0.5,
                        reason=f"Ambiguous template candidate: multiple template files detected ({', '.join(t[1] for t in template_named)}).",
                        is_ambiguous=True,
                    )
                )
            else:
                results.append(
                    FileRoleClassification(
                        file_id=f_id,
                        filename=f_name,
                        file_type=f_type,
                        role=FileRole.TEMPLATE,
                        confidence=0.90,
                        reason=f"Filename '{f_name}' explicitly denotes template layout.",
                    )
                )
        else:
            results.append(
                FileRoleClassification(
                    file_id=f_id,
                    filename=f_name,
                    file_type=f_type,
                    role=FileRole.SOURCE,
                    confidence=0.92,
                    reason=f"File provided as factual source and reference material.",
                )
            )

