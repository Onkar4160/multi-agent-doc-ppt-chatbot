"""Presentation template analyzer for PowerPoint (.pptx) files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
import pptx
from pptx.enum.shapes import PP_PLACEHOLDER

from app.llm.client import get_llm_client, LLMClient
from app.models.template_profile import (
    PlaceholderInfo,
    PptStyleProfile,
    SlideLayoutInfo,
    SlideRole,
    TemplateProfile,
    ToneProfile,
)

logger = logging.getLogger(__name__)


class PptToneAndSummarySchema(BaseModel):
    """Schema for single LLM call extracting tone and summary from slide text."""
    formality: str = Field(description="One of: formal, semi-formal, casual")
    voice: str = Field(description="One of: authoritative, technical, informative, persuasive")
    person: str = Field(description="One of: first_person_plural, third_person, first_person_singular")
    avg_sentence_length: float = Field(description="Approximate average word count per bullet sentence")
    style_notes: list[str] = Field(default_factory=list, description="Key presentation style notes")
    typical_openings: list[str] = Field(default_factory=list, description="Common slide heading styles")
    content_summary: str = Field(description="2-3 sentence executive summary of slide deck topic")


PPT_TONE_PROMPT = """Analyze the following text sample extracted from a PowerPoint presentation:

--- SLIDE TEXT SAMPLE ---
{text_sample}
--- END SAMPLE ---

Extract the presentation tone profile and a concise 2-3 sentence content summary.
"""


def analyze_presentation(
    file_path: str | Path,
    file_id: int | str | None = None,
    llm_client: LLMClient | None = None,
) -> TemplateProfile:
    """Analyze a PPTX file and produce a TemplateProfile.

    Uses python-pptx to inspect slide layouts, placeholders, theme fonts, and shapes without LLMs.
    Makes at most ONE LLM call to derive ToneProfile and content_summary.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PPTX file not found: {file_path}")

    try:
        prs = pptx.Presentation(path)
    except Exception as exc:
        raise ValueError(f"Corrupt or invalid PPTX file '{path.name}': {exc}") from exc

    slide_width = round(prs.slide_width.inches, 2)
    slide_height = round(prs.slide_height.inches, 2)

    # 1. Parse Layouts & Placeholders
    layouts: list[SlideLayoutInfo] = []
    layout_roles: dict[str, int] = {}

    for idx, layout in enumerate(prs.slide_layouts):
        placeholders: list[PlaceholderInfo] = []
        for ph in layout.placeholders:
            ph_type = str(ph.placeholder_format.type).split(".")[-1].lower()
            left = round(ph.left.inches, 2) if ph.left else None
            top = round(ph.top.inches, 2) if ph.top else None
            width = round(ph.width.inches, 2) if ph.width else None
            height = round(ph.height.inches, 2) if ph.height else None

            placeholders.append(PlaceholderInfo(
                idx=ph.placeholder_format.idx,
                type=ph_type,
                name=ph.name,
                left=left,
                top=top,
                width=width,
                height=height,
            ))

        role = _detect_layout_role(layout.name, placeholders)
        layouts.append(SlideLayoutInfo(
            index=idx,
            name=layout.name,
            role=role,
            placeholders=placeholders,
        ))

        if role not in layout_roles:
            layout_roles[role] = idx

    # Ensure fallback role defaults if missing
    if "title" not in layout_roles:
        layout_roles["title"] = 0
    if "title_content" not in layout_roles:
        layout_roles["title_content"] = min(1, len(prs.slide_layouts) - 1)

    # 2. Extract theme fonts & colors from slide master XML if available
    theme_fonts, theme_colors = _extract_theme_info(prs)

    # 3. Analyze existing slides
    existing_slides: list[dict[str, Any]] = []
    total_words = 0
    slide_text_parts: list[str] = []

    for idx, slide in enumerate(prs.slides, start=1):
        title_text = slide.shapes.title.text.strip() if slide.shapes.title and slide.shapes.title.text else ""
        bullet_count = 0
        has_image = False
        has_table = False
        has_chart = False
        words_in_slide = 0

        for shape in slide.shapes:
            if shape.has_text_frame:
                txt = shape.text_frame.text.strip()
                words_in_slide += len(txt.split())
                for paragraph in shape.text_frame.paragraphs:
                    if paragraph.level > 0 or paragraph.text.strip().startswith(("•", "-", "1.")):
                        bullet_count += 1
            if shape.shape_type == pptx.enum.shapes.MSO_SHAPE_TYPE.PICTURE:
                has_image = True
            if shape.has_table:
                has_table = True
            if shape.has_chart:
                has_chart = True

        total_words += words_in_slide
        if title_text:
            slide_text_parts.append(f"Slide {idx}: {title_text}")

        existing_slides.append({
            "slide_number": idx,
            "layout": slide.slide_layout.name,
            "title": title_text,
            "bullet_count": bullet_count,
            "has_image": has_image,
            "has_table": has_table,
            "has_chart": has_chart,
        })

    avg_words = round(total_words / max(1, len(prs.slides)), 1)
    raw_slide_text = "\n".join(slide_text_parts)
    text_preview = raw_slide_text[:1000] + ("..." if len(raw_slide_text) > 1000 else "")

    ppt_style = PptStyleProfile(
        slide_width=slide_width,
        slide_height=slide_height,
        theme_fonts=theme_fonts,
        theme_colors=theme_colors,
        layouts=layouts,
        layout_roles=layout_roles,
        existing_slides=existing_slides,
        avg_words_per_slide=avg_words,
    )

    # 4. Make at most ONE LLM call for ToneProfile and content_summary
    if llm_client is None:
        llm_client = get_llm_client()

    tone = ToneProfile()
    content_summary = "PowerPoint presentation template with slide masters and layouts."

    if raw_slide_text.strip():
        try:
            res: PptToneAndSummarySchema = llm_client.generate_json(
                prompt=PPT_TONE_PROMPT.format(text_sample=raw_slide_text[:3000]),
                schema=PptToneAndSummarySchema,
                system="You are an expert presentation and slide design analyst.",
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
            logger.warning("LLM presentation tone analysis failed for '%s': %s", path.name, exc)

    return TemplateProfile(
        file_id=file_id,
        file_type="pptx",
        source_name=path.name,
        doc_style=None,
        ppt_style=ppt_style,
        tone=tone,
        content_summary=content_summary,
        text_preview=text_preview,
    )


# ── Internal Helpers ───────────────────────────────────────────────────────

def _detect_layout_role(name: str, placeholders: list[PlaceholderInfo]) -> SlideRole:
    """Detect functional slide layout role from name and placeholder types."""
    name_lower = name.lower()
    ph_types = [ph.type for ph in placeholders]

    if "blank" in name_lower or len(placeholders) == 0:
        return "blank"
    if "section" in name_lower or "header" in name_lower:
        return "section_header"
    if "two" in name_lower or "comparison" in name_lower or ph_types.count("body") >= 2 or ph_types.count("object") >= 2:
        return "two_content"
    if "title only" in name_lower or (len(placeholders) == 1 and "title" in ph_types[0]):
        return "title_only"
    if "title" in name_lower and ("sub" in name_lower or "slide" in name_lower or len(placeholders) <= 2):
        return "title"
    if "content" in name_lower or "body" in ph_types or "object" in ph_types:
        return "title_content"

    return "other"


def _extract_theme_info(prs: pptx.Presentation) -> tuple[dict[str, str], dict[str, str]]:
    """Extract theme major/minor fonts and theme color scheme from PPTX XML."""
    theme_fonts = {"major": "Segoe UI", "minor": "Calibri"}
    theme_colors = {"primary": "#1E3A8A", "secondary": "#0D9488", "background": "#FFFFFF"}

    try:
        master = prs.slide_masters[0]
        # Inspect element XML for font or theme names
        xml_str = master.element.xml
        if 'latin typeface="' in xml_str:
            parts = xml_str.split('latin typeface="')
            if len(parts) > 1:
                theme_fonts["major"] = parts[1].split('"')[0]
            if len(parts) > 2:
                theme_fonts["minor"] = parts[2].split('"')[0]
    except Exception:
        pass

    return theme_fonts, theme_colors
