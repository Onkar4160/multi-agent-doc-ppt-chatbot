"""Document Generator Agent – produces structured DocumentModel using Gemini structured JSON output."""

from __future__ import annotations

import logging
from typing import Any

from app.llm.client import get_llm_client, LLMClient
from app.models.document_model import DocumentModel
from app.models.template_profile import TemplateProfile
from app.services.evidence_pack import EvidenceItem, format_evidence_pack

logger = logging.getLogger(__name__)

import re

DOC_GEN_PROMPT = """You are an expert technical and business writer.
Generate a comprehensive, detailed, high-quality document matching the user's brief.

DOCUMENT TYPE (TONE & FRAMING): {document_type}

ORGANIZATION / SENDER IDENTITY:
{organization_context}

--- USER BRIEF ---
{brief}

--- CANDIDATE TEMPLATE OUTLINE / SECTIONS (OPTIONAL REFERENCE) ---
{template_outline}

--- TARGET STYLE & TONE ---
Formality: {formality}
Voice: {voice}
Person: {person}
Style Notes: {style_notes}

--- EVIDENCE PACK ---
{evidence_pack_formatted}

--- MANDATORY CONTENT RULES ---
1. You MUST return a valid JSON object matching the DocumentModel schema.
2. OUTLINE PROPOSAL: Propose and generate an appropriate, logical section structure directly tailored to the brief, document type framing, and evidence pack. The candidate template outline above is provided only as optional available options, not a mandatory script. Create sections that naturally fit the topic.
3. Thoroughness: Provide detailed sections (each containing substantive text paragraphs, bullet blocks, or tables as appropriate).
4. Grounding: You may only state a specific fact (number, date, statistic, name, claimed event) if it matches an entry in the EVIDENCE PACK below, citing that entry's exact id as source_id. General connective text needs no citation but must contain no invented specific fact. If something isn't covered by the evidence pack, omit it or state it as general knowledge without invented precision — never fabricate a number or date.
5. Tabular data / Commercials: Include TableBlock elements only when the topic or brief genuinely warrants them (such as explicit timelines, pricing, or structured data comparisons) and supported by facts. Do not force artificial timeline, pricing, or team sections when irrelevant to the topic.
6. Never use markdown syntax (**, __, #, -) in any text field. Write plain text only. Bold/emphasis is not supported in this schema.
7. Use clear active voice with direct, professional, short sentences.
8. LENGTH & DEPTH GUIDANCE: Unless the brief specifies brief/short/one-page, target 1,200 to 1,800 total words across 6 to 8 substantive sections (each section approximately 120-220 words, naturally mixing substantive explanatory paragraphs, structured bullet lists, and comparison tables where useful). Treat this as guidance for thorough coverage, not as a rigid template.
9. DATE CONTEXT: Today's date is {today_date}. All time-relative statements must be relative to this date. Never invent a different date.
{extra_error_context}
"""


def generate_document_model(
    brief: str,
    profile: TemplateProfile,
    sources: list[dict[str, Any]] = [],
    evidence_pack: list[Any] | None = None,
    document_type: str = "proposal",
    llm_client: LLMClient | None = None,
) -> DocumentModel:
    """Generate a structured DocumentModel via Gemini structured output."""
    import datetime
    today_str = datetime.date.today().strftime("%B %Y")

    if llm_client is None:
        llm_client = get_llm_client()

    tone = profile.tone if profile else None
    formality = tone.formality if tone else "formal"
    voice = tone.voice if tone else "authoritative"
    person = tone.person if tone else "first_person_plural"
    style_notes = ", ".join(tone.style_notes) if (tone and tone.style_notes) else "Professional & direct"

    # Derive sender identity from profile source_name if meaningful, else neutral
    org_identity = "Neutral, professional author. Do not assume or invent any specific company name or location unless explicitly provided in the brief or evidence pack."
    if profile and profile.source_name:
        clean_name = profile.source_name.replace(".docx", "").replace(".pptx", "").replace("_", " ").strip()
        if clean_name and clean_name.lower() not in ("company proposal", "company template", "template", "default"):
            org_identity = f"Organization / Brand: {clean_name}"

    # Template outline offered as available options, not mandatory
    if profile and profile.doc_style and profile.doc_style.outline:
        outline_items = [item.get("heading", "") for item in profile.doc_style.outline if item.get("heading")]
        outline_str = "\n".join(f"- {h}" for h in outline_items)
    else:
        outline_str = "(No rigid template outline. Dynamically propose the most suitable section structure.)"

    if evidence_pack:
        formatted_evidence = format_evidence_pack(evidence_pack)
    else:
        formatted_evidence = _format_sources_as_evidence(sources)

    def _clean_model(m: DocumentModel) -> DocumentModel:
        if document_type != "proposal":
            filtered = [
                s for s in m.sections
                if not re.search(r"\b(timeline|pricing|commercial|team|investment)\b", s.heading, re.IGNORECASE)
            ]
            if filtered:
                m.sections = filtered
        m.date = today_str
        return m

    prompt = DOC_GEN_PROMPT.format(
        document_type=document_type,
        brief=brief,
        organization_context=org_identity,
        template_outline=outline_str,
        formality=formality,
        voice=voice,
        person=person,
        style_notes=style_notes,
        evidence_pack_formatted=formatted_evidence,
        today_date=today_str,
        extra_error_context="",
    )

    try:
        model: DocumentModel = llm_client.generate_json(
            prompt=prompt,
            schema=DocumentModel,
            system="You generate detailed enterprise documents adhering strictly to the JSON schema.",
            use_cache=False,
        )
        return _clean_model(model)
    except Exception as exc:
        logger.warning(
            "Document generation first attempt failed: %s. Retrying once with error context...",
            exc,
        )
        retry_prompt = DOC_GEN_PROMPT.format(
            document_type=document_type,
            brief=brief,
            organization_context=org_identity,
            template_outline=outline_str,
            formality=formality,
            voice=voice,
            person=person,
            style_notes=style_notes,
            evidence_pack_formatted=formatted_evidence,
            today_date=today_str,
            extra_error_context=f"\nCRITICAL FIX REQUIRED: Previous attempt failed validation with error: {exc}",
        )
        model: DocumentModel = llm_client.generate_json(
            prompt=retry_prompt,
            schema=DocumentModel,
            system="You generate detailed enterprise documents adhering strictly to the JSON schema.",
            use_cache=False,
        )
        return _clean_model(model)


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
