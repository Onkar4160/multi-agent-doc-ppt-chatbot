"""Analyse a PPTX template to extract a TemplateProfileSchema."""

from __future__ import annotations

import logging
from pathlib import Path

from pptx import Presentation
from pptx.util import Emu

from app.llm.schemas import PlaceholderInfo, SlideLayoutInfo, TemplateProfileSchema

logger = logging.getLogger(__name__)


def analyze_pptx(path: str | Path) -> TemplateProfileSchema:
    """Extract slide layouts, placeholders, fonts, and colors from a PPTX."""
    prs = Presentation(str(path))
    slide_width = prs.slide_width or Emu(9144000)
    slide_height = prs.slide_height or Emu(6858000)

    fonts: set[str] = set()
    colors: dict[str, str] = {}
    layouts: list[SlideLayoutInfo] = []

    # ── Enumerate layouts and placeholders ────────────────
    for i, layout in enumerate(prs.slide_layouts):
        phs: list[PlaceholderInfo] = []
        for ph in layout.placeholders:
            ph_type = _placeholder_type(ph)
            w_pct = (ph.width / slide_width * 100) if ph.width else None
            h_pct = (ph.height / slide_height * 100) if ph.height else None
            phs.append(PlaceholderInfo(
                idx=ph.placeholder_format.idx,
                name=ph.name,
                type=ph_type,
                width_pct=round(w_pct, 1) if w_pct else None,
                height_pct=round(h_pct, 1) if h_pct else None,
            ))
        layouts.append(SlideLayoutInfo(name=layout.name, index=i, placeholders=phs))

    # ── Collect fonts and colors from existing slides ────
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        if run.font.name:
                            fonts.add(run.font.name)
                        if run.font.color and run.font.color.rgb:
                            colors["slide_text"] = f"#{run.font.color.rgb}"

    # ── Theme colors (if accessible) ─────────────────────
    try:
        theme = prs.slide_masters[0].element
        # Extract accent colors from theme XML
        for elem in theme.iter():
            if elem.tag.endswith("}srgbClr"):
                val = elem.get("val")
                if val:
                    colors.setdefault("theme", f"#{val}")
    except Exception:
        pass  # theme extraction is best-effort

    return TemplateProfileSchema(
        fonts=sorted(fonts),
        colors=colors,
        slide_layouts=layouts,
        tone="professional",
    )


def _placeholder_type(ph) -> str:
    """Map python-pptx placeholder type enum to a simple string."""
    idx = ph.placeholder_format.idx
    if idx == 0:
        return "title"
    if idx == 1:
        return "body"
    name_lower = ph.name.lower()
    if "picture" in name_lower or "image" in name_lower:
        return "picture"
    if "chart" in name_lower:
        return "chart"
    if "table" in name_lower:
        return "table"
    return "body"
