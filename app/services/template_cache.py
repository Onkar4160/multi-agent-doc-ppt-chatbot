"""Template profiles cached by file content (FR-TPL-7).

The key is the file's SHA-256 plus ``ANALYZER_VERSION``: the same template uploaded twice (or under another name)
is analysed once, and a new analyser version recomputes old profiles. Analysis includes one LLM call for the tone
of PPTX/DOCX templates, so the cache also saves quota.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.models.template_profile import ANALYZER_VERSION, TemplateProfile, TemplateProfileRecord
from app.services.analyzers.template_package import file_sha256

logger = logging.getLogger(__name__)

_PPTX = {".pptx", ".potx"}
_DOCX = {".docx", ".dotx", ".pdf", ".png", ".jpg", ".jpeg"}
_XLSX = {".xlsx", ".xltx"}


def analyze_template(path: str | Path, file_id: int | str | None = None) -> TemplateProfile:
    """Run the analyser that fits the file type."""
    suffix = Path(path).suffix.lower()
    if suffix in _PPTX:
        from app.agents.ppt_analyzer import analyze_presentation

        return analyze_presentation(path, file_id=file_id)
    if suffix in _XLSX:
        from app.agents.xlsx_analyzer import analyze_workbook

        return analyze_workbook(path, file_id=file_id)
    if suffix in _DOCX:
        from app.agents.doc_analyzer import analyze_document

        return analyze_document(path, file_id=file_id)
    raise ValueError(f"Unsupported template type '{suffix}'")


def cached_profile(path: str | Path, db: Any = None, file_id: int | str | None = None) -> TemplateProfile:
    """Profile of ``path`` from the cache when its content and the analyser version match, else analysed and stored."""
    digest = file_sha256(path)
    if db is not None:
        try:
            rec = (
                db.query(TemplateProfileRecord)
                .filter(TemplateProfileRecord.sha256 == digest, TemplateProfileRecord.analyzer_version == ANALYZER_VERSION)
                .order_by(TemplateProfileRecord.id.desc())
                .first()
            )
            if rec is not None:
                profile = TemplateProfile.model_validate_json(rec.profile_json)
                if file_id is not None:
                    profile.file_id = file_id
                logger.info("Template profile cache hit for %s", Path(path).name)
                return profile
        except Exception as exc:
            logger.warning("Template profile cache lookup failed: %s", exc)
    profile = analyze_template(path, file_id=file_id)
    if db is not None:
        try:
            db.add(TemplateProfileRecord(
                file_id=int(file_id) if isinstance(file_id, int) or (isinstance(file_id, str) and file_id.isdigit()) else None,
                profile_json=profile.model_dump_json(), sha256=digest, analyzer_version=ANALYZER_VERSION,
            ))
            db.commit()
        except Exception as exc:
            logger.warning("Template profile could not be cached: %s", exc)
            db.rollback()
    return profile
