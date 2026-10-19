"""Workbook template analyser for Excel (.xlsx / .xltx) files (FR-TPL-6). No LLM call: structure only."""

from __future__ import annotations

from pathlib import Path

import openpyxl

from app.models.template_profile import ANALYZER_VERSION, TemplateProfile
from app.services.analyzers.template_package import file_sha256, regular_package
from app.services.analyzers.xlsx_manifest import xlsx_manifest, xlsx_tokens


def analyze_workbook(file_path: str | Path, file_id: int | str | None = None) -> TemplateProfile:
    original = Path(file_path)
    if not original.exists():
        raise FileNotFoundError(f"Workbook file not found: {file_path}")
    try:
        wb = openpyxl.load_workbook(regular_package(original), data_only=False)
    except Exception as exc:
        raise ValueError(f"Corrupt or invalid workbook '{original.name}': {exc}") from exc
    manifest = xlsx_manifest(wb)
    roles = ", ".join(f"{s.name} ({s.role})" for s in manifest.sheets)
    return TemplateProfile(
        file_id=file_id,
        file_type="xlsx",
        source_name=original.name,
        xlsx_style=manifest,
        content_summary=f"Workbook template with {len(manifest.sheets)} sheet(s): {roles}.",
        text_preview="; ".join(", ".join(s.header[:8]) for s in manifest.sheets if s.header)[:1000],
        analyzer_version=ANALYZER_VERSION,
        design_tokens=xlsx_tokens(wb, manifest),
        sha256=file_sha256(original),
    )
