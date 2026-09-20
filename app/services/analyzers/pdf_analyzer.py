"""Analyse a PDF template to extract a TemplateProfileSchema."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber

from app.llm.client import get_llm_client
from app.llm.schemas import TemplateProfileSchema

logger = logging.getLogger(__name__)


def analyze_pdf(path: str | Path) -> TemplateProfileSchema:
    """Extract layout, fonts, colors from a PDF; uses Gemini vision for complex cases."""
    path = Path(path)

    fonts: set[str] = set()
    font_sizes: dict[str, float] = {}
    colors: dict[str, str] = {}
    section_patterns: list[str] = []

    # ── pdfplumber: text and font metadata ───────────────
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages[:5]:  # sample first 5 pages
            for char in page.chars:
                if char.get("fontname"):
                    fonts.add(char["fontname"])
                if char.get("size"):
                    size = round(float(char["size"]), 1)
                    font_sizes[char.get("fontname", "unknown")] = size

    # ── PyMuPDF: render first page as image for vision ───
    doc = fitz.open(str(path))
    if doc.page_count > 0:
        page = doc[0]
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")

        # Use Gemini vision for style analysis
        try:
            llm = get_llm_client()
            profile = llm.generate_json(
                prompt=(
                    "Analyse this document page image. Extract:\n"
                    "- fonts (list of font family names)\n"
                    "- colors (dict of role→hex, e.g. primary, accent, heading)\n"
                    "- section_patterns (list of section types like intro, body, conclusion)\n"
                    "- tone (one of: professional, academic, casual/marketing, formal/legal)\n"
                    "- bullet_style (the bullet character used)\n"
                    "Return only the fields you can detect."
                ),
                schema=TemplateProfileSchema,
                use_cache=True,
            )
            # Merge vision results with extracted data
            fonts.update(profile.fonts)
            colors.update(profile.colors)
            section_patterns = profile.section_patterns or section_patterns
            tone = profile.tone
        except Exception as exc:
            logger.warning("Vision analysis failed for PDF: %s", exc)
            tone = "professional"
    else:
        tone = "professional"

    doc.close()

    return TemplateProfileSchema(
        fonts=sorted(fonts),
        font_sizes=font_sizes,
        colors=colors,
        tone=tone,
        section_patterns=section_patterns,
    )
