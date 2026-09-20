"""Analyse an image template to extract a TemplateProfileSchema via Gemini vision."""

from __future__ import annotations

import logging
from pathlib import Path

from app.llm.client import get_llm_client
from app.llm.schemas import TemplateProfileSchema

logger = logging.getLogger(__name__)

_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
}


def analyze_image(path: str | Path) -> TemplateProfileSchema:
    """Send image to Gemini vision to identify layout, colors, typography."""
    path = Path(path)
    mime = _MIME_MAP.get(path.suffix.lower(), "image/png")
    img_bytes = path.read_bytes()

    llm = get_llm_client()
    prompt = (
        "Analyse this document/presentation template image. Extract a style profile:\n"
        "- fonts: list of font family names you can identify\n"
        "- colors: dict mapping roles (primary, secondary, accent, background, heading) to hex codes\n"
        "- heading_styles: list of heading levels/styles visible\n"
        "- section_patterns: ordered list of section types (e.g. title, intro, body, conclusion)\n"
        "- tone: one of professional, academic, casual/marketing, formal/legal\n"
        "- bullet_style: bullet character if visible\n"
        "Be as precise as possible with the hex color values."
    )

    try:
        return llm.generate_json(
            prompt=prompt,
            schema=TemplateProfileSchema,
            use_cache=True,
        )
    except Exception as exc:
        logger.error("Image analysis failed: %s", exc)
        return TemplateProfileSchema(tone="professional")
