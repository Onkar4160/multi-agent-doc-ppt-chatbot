"""Presentation Deck Generator Agent – produces rich structured DeckModel using Gemini structured JSON output."""

from __future__ import annotations

import logging
from typing import Any

from app.llm.client import get_llm_client, LLMClient
from app.models.deck_model import BulletItem, DeckModel, SlideModel
from app.models.template_profile import TemplateProfile
from app.services.evidence_pack import EvidenceItem, format_evidence_pack

logger = logging.getLogger(__name__)

import re

PPT_GEN_PROMPT = """You are an expert presentation designer and executive speechwriter.
Generate a comprehensive, highly professional PowerPoint presentation deck matching the user's brief.

DOCUMENT TYPE (TONE & FRAMING): {document_type}

ORGANIZATION / SENDER IDENTITY:
{organization_context}

--- USER BRIEF ---
{brief}

--- TARGET SLIDE COUNT ---
EXACTLY {slide_count} slides (excluding the Sources slide).

--- TARGET STYLE & TONE ---
Formality: {formality}
Voice: {voice}
Person: {person}

--- AVAILABLE SLIDE ROLES ---
- "title": Title slide (MUST be Slide 1)
- "section_header": Topic transition header slide (use 2-3 throughout the deck)
- "title_content": Standard title + bullet list slide
- "two_content": Title + left column bullets + right column bullets (MUST compare two distinct items/approaches)
- "title_only": Single high-impact callout slide

--- EVIDENCE PACK ---
{evidence_pack_formatted}

--- MANDATORY SLIDE CONTENT RULES ---
1. You MUST return a valid JSON object matching the DeckModel schema.
2. The `slides` array MUST contain EXACTLY {slide_count} slides.
3. Slide 1 MUST have role="title".
4. DYNAMIC SLIDE STRUCTURE: Propose an appropriate, logically ordered slide narrative tailored directly to the brief, the document type framing, and evidence pack. Available layout roles are options to use where appropriate. Only include commercial, pricing, or team slides if explicitly relevant to the brief.
5. For every content slide (`title_content`, `two_content`), provide 4 to 6 detailed bullet points.
6. Each bullet MUST contain 12 to 20 words with concrete numbers, metrics, and facts from the sources.
7. `two_content` slides MUST compare two things.
8. Every slide MUST include 2 to 3 detailed sentences of speaker notes in `notes`.
9. Total words per slide must stay around 80 to 90 words.
10. You may only state a specific fact (number, date, statistic, name, claimed event) if it matches an entry in the EVIDENCE PACK below, citing that entry's exact id as source_id. General connective text needs no citation but must contain no invented specific fact. If something isn't covered by the evidence pack, omit it or state it as general knowledge without invented precision — never fabricate a number or date.
11. Never use markdown syntax (**, __, #, -) in any text field. Write plain text only. Bold/emphasis is not supported in this schema.
12. DATE CONTEXT: Today's date is {today_date}. All time-relative statements must be relative to this date. Never invent a different date.
{extra_error_context}
"""


def generate_deck_model(
    brief: str,
    profile: TemplateProfile,
    sources: list[dict[str, Any]] = [],
    evidence_pack: list[Any] | None = None,
    slide_count: int = 12,
    document_type: str = "proposal",
    llm_client: LLMClient | None = None,
) -> DeckModel:
    """Generate a structured DeckModel via Gemini structured output."""
    import datetime
    today_str = datetime.date.today().strftime("%B %Y")

    if llm_client is None:
        llm_client = get_llm_client()

    tone = profile.tone if profile else None
    formality = tone.formality if tone else "formal"
    voice = tone.voice if tone else "authoritative"
    person = tone.person if tone else "first_person_plural"

    # Derive sender identity from profile source_name if meaningful, else neutral
    org_identity = "Neutral, professional author. Do not assume or invent any specific company name or location unless explicitly provided in the brief or evidence pack."
    if profile and profile.source_name:
        clean_name = profile.source_name.replace(".docx", "").replace(".pptx", "").replace("_", " ").strip()
        if clean_name and clean_name.lower() not in ("company proposal", "company template", "template", "default"):
            org_identity = f"Organization / Brand: {clean_name}"

    if evidence_pack:
        formatted_evidence = format_evidence_pack(evidence_pack)
    else:
        formatted_evidence = _format_sources_as_evidence(sources)

    def _clean_deck(d: DeckModel) -> DeckModel:
        if document_type != "proposal":
            filtered = [
                s for s in d.slides
                if not re.search(r"\b(timeline|pricing|commercial|team|investment)\b", s.title, re.IGNORECASE)
            ]
            if filtered:
                d.slides = filtered
        adjusted = _validate_and_adjust_deck(d, slide_count)
        if adjusted.slides and adjusted.slides[0].role == "title":
            t_slide = adjusted.slides[0]
            if not t_slide.subtitle:
                t_slide.subtitle = today_str
            elif today_str not in t_slide.subtitle:
                t_slide.subtitle = f"{t_slide.subtitle} | {today_str}"
        return adjusted

    prompt = PPT_GEN_PROMPT.format(
        document_type=document_type,
        brief=brief,
        organization_context=org_identity,
        slide_count=slide_count,
        formality=formality,
        voice=voice,
        person=person,
        evidence_pack_formatted=formatted_evidence,
        today_date=today_str,
        extra_error_context="",
    )

    try:
        deck: DeckModel = llm_client.generate_json(
            prompt=prompt,
            schema=DeckModel,
            system="You generate structured PowerPoint deck models adhering strictly to the JSON schema.",
            use_cache=False,
        )
        return _clean_deck(deck)
    except Exception as exc:
        logger.warning(
            "Deck generation first attempt failed: %s. Retrying once with error context...",
            exc,
        )
        retry_prompt = PPT_GEN_PROMPT.format(
            document_type=document_type,
            brief=brief,
            organization_context=org_identity,
            slide_count=slide_count,
            formality=formality,
            voice=voice,
            person=person,
            evidence_pack_formatted=formatted_evidence,
            today_date=today_str,
            extra_error_context=f"\nCRITICAL FIX REQUIRED: Previous attempt failed validation with error: {exc}",
        )
        deck: DeckModel = llm_client.generate_json(
            prompt=retry_prompt,
            schema=DeckModel,
            system="You generate structured PowerPoint deck models adhering strictly to the JSON schema.",
            use_cache=False,
        )
        return _clean_deck(deck)


def _validate_and_adjust_deck(deck: DeckModel, target_count: int) -> DeckModel:
    """Ensure slide count matches target_count exactly."""
    if len(deck.slides) == target_count:
        return deck

    logger.info(
        "Adjusting generated slide count from %d to target %d",
        len(deck.slides),
        target_count,
    )
    if len(deck.slides) > target_count:
        deck.slides = deck.slides[:target_count]
    while len(deck.slides) < target_count:
        idx = len(deck.slides) + 1
        deck.slides.append(
            SlideModel(
                role="title_content",
                title=f"Strategic Analysis & Insights (Part {idx})",
                bullets=[
                    BulletItem(text="Detailed operational evaluation and strategic alignment factors.", level=0),
                    BulletItem(text="Implementation feasibility and value realization metrics.", level=0),
                ],
                notes="Speaker notes expanding on operational and strategic considerations.",
            )
        )
    return deck


def _format_sources_as_evidence(sources: list[dict[str, Any]]) -> str:
    """Format fallback sources as a numbered evidence pack block."""
    if not sources:
        return "(No evidence items available)"

    lines = []
    for s in sources:
        sid = s.get("id", 1)
        title = s.get("title", "Untitled Source")
        snippet = s.get("snippet", "")
        text = f"{title}: {snippet}" if snippet else title
        lines.append(f"[{sid}] {text}")

    return "\n".join(lines)
