"""DOCX Rendering Service – turns DocumentModel into fully editable Word documents using template styles."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches, Pt, RGBColor

from app.models.document_model import (
    BulletsBlock,
    DocumentModel,
    ParagraphBlock,
    TableBlock,
)
from app.models.template_profile import TemplateProfile

logger = logging.getLogger(__name__)


def render_docx(
    model: DocumentModel,
    template_path: str | Path,
    profile: TemplateProfile | None = None,
    out_path: str | Path = "output.docx",
    sources_map: dict[int, dict[str, Any]] | None = None,
) -> Path:
    """Render a DocumentModel into an editable Word (.docx) document using a template file."""
    tmpl_path = Path(template_path)
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if not tmpl_path.exists():
        raise FileNotFoundError(f"DOCX template file not found: {template_path}")

    try:
        doc = docx.Document(tmpl_path)
    except Exception as exc:
        raise ValueError(f"Failed to open template DOCX '{tmpl_path.name}': {exc}") from exc

    # 1. Clean old body elements, keeping the final sectPr (headers/footers/margins)
    body = doc.element.body
    children_to_remove = [c for c in body if not c.tag.endswith('sectPr')]
    for child in children_to_remove:
        body.remove(child)

    # 2. Extract available style names from template
    available_styles = {s.name for s in doc.styles}

    def get_heading_style(level: int) -> str:
        s_name = f"Heading {level}"
        return s_name if s_name in available_styles else "Normal"

    def format_citations(source_ids: list[int]) -> str:
        if not source_ids:
            return ""
        unique_ids = sorted(list(set(source_ids)))
        return " " + "".join(f"[{sid}]" for sid in unique_ids)

    all_referenced_source_ids: set[int] = set()

    # 3. Render Title & Cover Info
    if model.title:
        p_title = doc.add_paragraph()
        p_title.paragraph_format.keep_with_next = True
        if "Title" in available_styles:
            p_title.style = "Title"
            p_title.text = model.title
        else:
            p_title.paragraph_format.space_before = Pt(24)
            p_title.paragraph_format.space_after = Pt(8)
            run = p_title.add_run(model.title)
            run.font.size = Pt(22)
            run.bold = True

    if model.subtitle:
        p_sub = doc.add_paragraph()
        p_sub.paragraph_format.keep_with_next = True
        if "Subtitle" in available_styles:
            p_sub.style = "Subtitle"
            p_sub.text = model.subtitle
        else:
            p_sub.paragraph_format.space_after = Pt(18)
            run = p_sub.add_run(model.subtitle)
            run.font.size = Pt(13)

    if model.client_name or model.date:
        meta_lines = []
        if model.client_name:
            meta_lines.append(f"PREPARED FOR: {model.client_name}")
        if model.date:
            meta_lines.append(f"DATE: {model.date}")

        p_meta = doc.add_paragraph()
        p_meta.paragraph_format.space_after = Pt(24)
        run_meta = p_meta.add_run("\n".join(meta_lines))
        run_meta.font.size = Pt(10)

    # 4. Render Sections
    for section in model.sections:
        h_style = get_heading_style(section.level)
        p_h = doc.add_paragraph(style=h_style)
        p_h.paragraph_format.keep_with_next = True  # Prevent orphaned headings
        p_h.add_run(section.heading)

        for block in section.blocks:
            if isinstance(block, ParagraphBlock):
                p = doc.add_paragraph()
                p.paragraph_format.line_spacing = 1.15
                p.paragraph_format.space_after = Pt(6)
                p.add_run(block.text)

                if block.source_ids:
                    c_str = format_citations(block.source_ids)
                    r_c = p.add_run(c_str)
                    r_c.font.bold = True
                    r_c.font.size = Pt(9.5)
                    all_referenced_source_ids.update(block.source_ids)

            elif isinstance(block, BulletsBlock):
                style_name = "List Number" if block.ordered else ("List Bullet" if "List Bullet" in available_styles else "Normal")
                for item in block.items:
                    p_b = doc.add_paragraph(style=style_name)
                    p_b.paragraph_format.line_spacing = 1.15
                    p_b.paragraph_format.space_after = Pt(4)
                    if not block.ordered and "List Bullet" not in available_styles:
                        p_b.add_run("• ")
                    p_b.add_run(item)

                if block.source_ids:
                    all_referenced_source_ids.update(block.source_ids)

            elif isinstance(block, TableBlock):
                if block.headers or block.rows:
                    total_rows = (1 if block.headers else 0) + len(block.rows)
                    total_cols = len(block.headers) if block.headers else (len(block.rows[0]) if block.rows else 1)

                    table = doc.add_table(rows=total_rows, cols=total_cols)
                    table.alignment = WD_TABLE_ALIGNMENT.CENTER
                    if "Table Grid" in available_styles:
                        table.style = "Table Grid"

                    # Set proportional column widths (total ~6.5 inches)
                    col_w = Inches(6.5 / max(1, total_cols))
                    for col in table.columns:
                        col.width = col_w
                        for cell in col.cells:
                            cell.width = col_w

                    curr_r = 0
                    if block.headers:
                        for col_idx, text in enumerate(block.headers):
                            cell = table.cell(0, col_idx)
                            cell.text = text
                            _set_header_cell_style(cell)
                        curr_r = 1

                    for row_idx, row_data in enumerate(block.rows):
                        for col_idx, text in enumerate(row_data):
                            if col_idx < total_cols:
                                cell = table.cell(curr_r + row_idx, col_idx)
                                cell.text = text
                                _set_body_cell_style(cell, is_alt=(row_idx % 2 == 1))

                    doc.add_paragraph().paragraph_format.space_after = Pt(8)

                if block.source_ids:
                    all_referenced_source_ids.update(block.source_ids)

    # 5. Render Sources Section if citations exist
    if all_referenced_source_ids or sources_map:
        doc.add_paragraph().paragraph_format.space_after = Pt(18)
        p_src_h = doc.add_paragraph(style=get_heading_style(1))
        p_src_h.paragraph_format.keep_with_next = True  # Prevent orphaned Sources heading
        p_src_h.add_run("Sources & References")

        ref_ids = sorted(list(all_referenced_source_ids)) if all_referenced_source_ids else (sorted(list(sources_map.keys())) if sources_map else [])

        for sid in ref_ids:
            s_info = (sources_map or {}).get(sid, {})
            title = s_info.get("title") or f"Source {sid}"
            url = s_info.get("url") or ""
            snippet = s_info.get("snippet") or ""

            p_src = doc.add_paragraph(style="List Bullet" if "List Bullet" in available_styles else "Normal")
            r_id = p_src.add_run(f"[{sid}] ")
            r_id.bold = True
            p_src.add_run(f"{title} ")
            if url:
                r_u = p_src.add_run(f"({url}) ")
                r_u.font.italic = True
            if snippet:
                p_src.add_run(f"— \"{snippet[:150]}...\"" if len(snippet) > 150 else f"— \"{snippet}\"")

    doc.save(destination)
    logger.info("Successfully rendered DOCX to %s", destination)
    return destination


# ── Cell Formatting Helpers ────────────────────────────────────────────────

def _set_header_cell_style(cell: Any) -> None:
    """Style table header cell with primary background and bold text."""
    tcPr = cell._element.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="1E3A8A"/>')
    tcPr.append(shd)
    if cell.paragraphs and cell.paragraphs[0].runs:
        cell.paragraphs[0].runs[0].font.bold = True
        cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)


def _set_body_cell_style(cell: Any, is_alt: bool = False) -> None:
    """Style body cell with padding and optional alternating background."""
    if is_alt:
        tcPr = cell._element.get_or_add_tcPr()
        shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="F9FAFB"/>')
        tcPr.append(shd)
