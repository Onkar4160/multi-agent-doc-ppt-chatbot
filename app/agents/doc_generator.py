"""Document Generator Agent – produces structured DocumentModel using Gemini structured JSON output."""

from __future__ import annotations

import logging
from typing import Any

from app.llm.client import get_llm_client, LLMClient
from app.models.document_model import DocumentModel
from app.models.template_profile import TemplateProfile

logger = logging.getLogger(__name__)

DOC_GEN_PROMPT = """You are an expert technical proposal writer for NexaWorks AI Solutions (Pune, India).
Generate a comprehensive, detailed, 3-to-4 page business proposal matching the user's brief.

--- USER BRIEF ---
{brief}

--- TEMPLATE OUTLINE & SECTION HEADINGS ---
{template_outline}

--- TARGET STYLE & TONE ---
Formality: {formality}
Voice: {voice}
Person: {person}
Style Notes: {style_notes}

--- AVAILABLE SOURCES ---
{sources_formatted}

--- MANDATORY CONTENT RULES ---
1. You MUST return a valid JSON object matching the DocumentModel schema.
2. Follow the TEMPLATE OUTLINE HEADINGS in exact sequence. Include 6 to 8 detailed sections.
3. Target document length is 3 to 4 pages (each section MUST contain 100 to 180 words of thorough text, bullets, or tables).
4. All facts, metrics, pricing figures, and market claims MUST cite provided `source_ids`. Never invent statistics.
5. Include structured TableBlock elements for Timeline and Pricing/Commercial Investment if present in outline.
6. Use clear active voice with direct, professional, short sentences.
{extra_error_context}
"""


def generate_document_model(
    brief: str,
    profile: TemplateProfile,
    sources: list[dict[str, Any]] = [],
    llm_client: LLMClient | None = None,
) -> DocumentModel:
    """Generate a structured DocumentModel via Gemini structured output."""
    if llm_client is None:
        llm_client = get_llm_client()

    tone = profile.tone
    outline_items = (
        [item.get("heading", "") for item in profile.doc_style.outline]
        if (profile and profile.doc_style and profile.doc_style.outline)
        else [
            "1. Introduction & Executive Context",
            "2. Problem Statement",
            "3. Proposed Solution & Architecture",
            "4. Approach and Methodology",
            "5. Project Timeline & Milestones",
            "6. Commercial Investment & Pricing",
            "7. Engagement Team",
            "8. Conclusion & Next Steps",
        ]
    )

    outline_str = "\n".join(f"- {h}" for h in outline_items if h)
    formatted_sources = _format_sources(sources)

    prompt = DOC_GEN_PROMPT.format(
        brief=brief,
        template_outline=outline_str,
        formality=tone.formality,
        voice=tone.voice,
        person=tone.person,
        style_notes=", ".join(tone.style_notes) if tone.style_notes else "Professional & direct",
        sources_formatted=formatted_sources,
        extra_error_context="",
    )

    try:
        model: DocumentModel = llm_client.generate_json(
            prompt=prompt,
            schema=DocumentModel,
            system="You generate detailed enterprise documents adhering strictly to the JSON schema.",
            use_cache=False,
        )
        return model
    except Exception as exc:
        logger.warning("Document generation first attempt failed: %s. Retrying once with error context...", exc)
        retry_prompt = DOC_GEN_PROMPT.format(
            brief=brief,
            template_outline=outline_str,
            formality=tone.formality,
            voice=tone.voice,
            person=tone.person,
            style_notes=", ".join(tone.style_notes),
            sources_formatted=formatted_sources,
            extra_error_context=f"\nCRITICAL FIX REQUIRED: Previous attempt failed validation with error: {exc}",
        )
        model: DocumentModel = llm_client.generate_json(
            prompt=retry_prompt,
            schema=DocumentModel,
            system="You generate detailed enterprise documents adhering strictly to the JSON schema.",
            use_cache=False,
        )
        return model


def _format_sources(sources: list[dict[str, Any]]) -> str:
    """Format list of sources into string for prompt context."""
    if not sources:
        return "No specific sources provided. Generate based on general domain knowledge."

    lines = []
    for s in sources:
        sid = s.get("id", 1)
        title = s.get("title", "Untitled Source")
        snippet = s.get("snippet", "")
        lines.append(f"- Source [{sid}]: {title}\n  Snippet: {snippet}")

    return "\n".join(lines)
