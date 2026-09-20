"""Document template analyzer for DOCX, PDF, and image files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import docx
from pydantic import BaseModel, Field

from app.llm.client import get_llm_client, LLMClient
from app.models.template_profile import (
    DocStyleProfile,
    FontInfo,
    TemplateProfile,
    ToneProfile,
)
from app.services.parsers import parse_file, ParsedContent

logger = logging.getLogger(__name__)


class ToneAndSummarySchema(BaseModel):
    """Schema for single LLM call extracting tone profile and content summary."""
    formality: str = Field(description="One of: formal, semi-formal, casual")
    voice: str = Field(description="One of: authoritative, technical, informative, persuasive")
    person: str = Field(description="One of: first_person_plural, third_person, first_person_singular")
    avg_sentence_length: float = Field(description="Approximate average word count per sentence")
    style_notes: list[str] = Field(default_factory=list, description="3-5 key writing style observations")
    typical_openings: list[str] = Field(default_factory=list, description="Common section/paragraph opening phrases")
    content_summary: str = Field(description="2-3 sentence executive summary of document subject matter")


TONE_ANALYSIS_PROMPT = """Analyze the following text sample from a corporate document template:

--- TEXT SAMPLE ---
{text_sample}
--- END SAMPLE ---

Extract the writing tone profile and a concise 2-3 sentence content summary.
"""


def analyze_document(
    file_path: str | Path,
    file_id: int | str | None = None,
    llm_client: LLMClient | None = None,
) -> TemplateProfile:
    """Analyze a DOCX, PDF, or image file and produce a TemplateProfile.

    Uses python-docx / parsers to extract real visual styles without LLM calls.
    Makes at most ONE LLM call to derive ToneProfile and content_summary.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Document file not found: {file_path}")

    # 1. Parse document structure
    parsed: ParsedContent = parse_file(path)

    # 2. Extract visual style (DocStyleProfile) without LLM
    doc_style = _extract_doc_style(path, parsed)

    # 3. Make at most ONE LLM call for Tone and Content Summary
    if llm_client is None:
        llm_client = get_llm_client()

    tone = ToneProfile()
    content_summary = "Document template containing standard text blocks and sections."

    if parsed.raw_text.strip():
        text_sample = parsed.raw_text[:3000]
        try:
            res: ToneAndSummarySchema = llm_client.generate_json(
                prompt=TONE_ANALYSIS_PROMPT.format(text_sample=text_sample),
                schema=ToneAndSummarySchema,
                system="You are an expert linguistic and document style analyst.",
                use_cache=True,
            )
            tone = ToneProfile(
                formality=res.formality,
                voice=res.voice,
                person=res.person,
                avg_sentence_length=res.avg_sentence_length,
                style_notes=res.style_notes,
                typical_openings=res.typical_openings,
            )
            content_summary = res.content_summary
        except Exception as exc:
            logger.warning("LLM tone analysis failed for '%s': %s", path.name, exc)

    return TemplateProfile(
        file_id=file_id,
        file_type=parsed.file_type,
        source_name=path.name,
        doc_style=doc_style,
        ppt_style=None,
        tone=tone,
        content_summary=content_summary,
        text_preview=parsed.text_preview,
    )


# ── Internal Helpers ───────────────────────────────────────────────────────

def _extract_doc_style(path: Path, parsed: ParsedContent) -> DocStyleProfile:
    """Extract visual styling tokens from file without using an LLM."""
    heading_styles: dict[str, FontInfo] = {}
    body_font = FontInfo(name="Calibri", size_pt=11.0, color_hex="1F2937")
    page_size = "A4"
    margins = {"top": 1.0, "bottom": 1.0, "left": 1.0, "right": 1.0}
    header_text = None
    footer_text = None
    palette_set: set[str] = set()
    outline: list[dict[str, Any]] = []

    if path.suffix.lower() == ".docx":
        try:
            doc = docx.Document(path)
            section = doc.sections[0]
            margins = {
                "top": round(section.top_margin.inches, 2),
                "bottom": round(section.bottom_margin.inches, 2),
                "left": round(section.left_margin.inches, 2),
                "right": round(section.right_margin.inches, 2),
            }
            if section.header and section.header.paragraphs:
                header_text = section.header.paragraphs[0].text.strip() or None
            if section.footer and section.footer.paragraphs:
                footer_text = section.footer.paragraphs[0].text.strip() or None
        except Exception:
            pass

    # Process parsed blocks for fonts, colors, outline
    for block in parsed.blocks:
        if block.font_info:
            if block.font_info.color_hex:
                palette_set.add(block.font_info.color_hex)
            if block.type == "heading":
                lvl_key = f"h{block.level}"
                heading_styles[lvl_key] = block.font_info
                outline.append({
                    "level": block.level,
                    "heading": block.text,
                    "approx_words": len(block.text.split()),
                })
            elif block.type == "paragraph" and body_font.name == "Calibri":
                body_font = block.font_info

    palette = sorted(list(palette_set))[:5]
    if not palette:
        palette = ["#1E3A8A", "#0D9488", "#1F2937"]

    return DocStyleProfile(
        page_size=page_size,
        margins=margins,
        heading_styles=heading_styles,
        body_font=body_font,
        line_spacing=1.15,
        list_styles=["bullet", "number"],
        table_style_summary="Styled table grid with colored header row",
        header_text=header_text,
        footer_text=footer_text,
        palette=palette,
        outline=outline,
    )
