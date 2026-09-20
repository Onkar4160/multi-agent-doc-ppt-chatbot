"""Presentation Deck Generator Agent – produces rich structured DeckModel using Gemini structured JSON output."""

from __future__ import annotations

import logging
from typing import Any

from app.llm.client import get_llm_client, LLMClient
from app.models.deck_model import DeckModel
from app.models.template_profile import TemplateProfile

logger = logging.getLogger(__name__)

PPT_GEN_PROMPT = """You are an expert presentation designer and executive speechwriter for NexaWorks AI Solutions.
Generate a comprehensive, highly professional PowerPoint presentation deck matching the user's brief.

--- USER BRIEF ---
{brief}

--- TARGET SLIDE COUNT ---
EXACTLY {slide_count} slides (excluding the Sources slide).

--- TARGET STYLE & TONE ---
Formality: {formality}
Voice: {voice}
Person: {person}

--- ALLOWED SLIDE ROLES ---
- "title": Title slide (MUST be Slide 1)
- "section_header": Topic transition header slide (use 2-3 throughout the deck)
- "title_content": Standard title + bullet list slide
- "two_content": Title + left column bullets + right column bullets (MUST compare two distinct items/approaches)
- "title_only": Single high-impact callout slide

--- AVAILABLE SOURCES ---
{sources_formatted}

--- MANDATORY SLIDE CONTENT RULES ---
1. You MUST return a valid JSON object matching the DeckModel schema.
2. The `slides` array MUST contain EXACTLY {slide_count} slides.
3. Slide 1 MUST have role="title".
4. For every content slide (`title_content`, `two_content`), provide 4 to 6 detailed bullet points.
5. Each bullet MUST contain 12 to 20 words with concrete numbers, metrics, and facts from the sources.
6. `two_content` slides MUST compare two things (e.g., Traditional Manual Operations vs NexaWorks Multi-Agent Automation).
7. Every slide MUST include 2 to 3 detailed sentences of speaker notes in `notes`.
8. Total words per slide must stay around 80 to 90 words.
9. ONLY use facts from the provided sources and cite them by `source_ids`; do NOT invent sources or IDs.
{extra_error_context}
"""


def generate_deck_model(
    brief: str,
    profile: TemplateProfile,
    sources: list[dict[str, Any]] = [],
    slide_count: int = 12,
    llm_client: LLMClient | None = None,
) -> DeckModel:
    """Generate a structured DeckModel via Gemini structured output."""
    if llm_client is None:
        llm_client = get_llm_client()

    tone = profile.tone
    formatted_sources = _format_sources(sources)

    prompt = PPT_GEN_PROMPT.format(
        brief=brief,
        slide_count=slide_count,
        formality=tone.formality,
        voice=tone.voice,
        person=tone.person,
        sources_formatted=formatted_sources,
        extra_error_context="",
    )

    try:
        deck: DeckModel = llm_client.generate_json(
            prompt=prompt,
            schema=DeckModel,
            system="You generate structured PowerPoint deck models adhering strictly to the JSON schema.",
            use_cache=False,
        )
        return _validate_and_adjust_deck(deck, slide_count)
    except Exception as exc:
        logger.warning("Deck generation first attempt failed: %s. Retrying once with error context...", exc)
        retry_prompt = PPT_GEN_PROMPT.format(
            brief=brief,
            slide_count=slide_count,
            formality=tone.formality,
            voice=tone.voice,
            person=tone.person,
            sources_formatted=formatted_sources,
            extra_error_context=f"\nCRITICAL FIX REQUIRED: Previous attempt failed validation with error: {exc}",
        )
        deck: DeckModel = llm_client.generate_json(
            prompt=retry_prompt,
            schema=DeckModel,
            system="You generate structured PowerPoint deck models adhering strictly to the JSON schema.",
            use_cache=False,
        )
        return _validate_and_adjust_deck(deck, slide_count)


def _validate_and_adjust_deck(deck: DeckModel, target_count: int) -> DeckModel:
    """Ensure slide count matches target_count."""
    if len(deck.slides) == target_count:
        return deck

    logger.info("Adjusting generated slide count from %d to target %d", len(deck.slides), target_count)
    if len(deck.slides) > target_count:
        deck.slides = deck.slides[:target_count]
    return deck


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
