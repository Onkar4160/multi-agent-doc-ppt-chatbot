"""Generator agent – produces DocumentModel or DeckModel from research + KB data."""

from __future__ import annotations

import json
import logging
import time

from langchain_core.messages import AIMessage

from app.agents.state import AgentState
from app.llm.client import get_llm_client
from app.llm.schemas import DeckModel, DocumentModel

logger = logging.getLogger(__name__)


def generator_node(state: AgentState) -> dict:
    """Generate a structured document or deck model from gathered research and KB data."""
    start = time.time()

    user_request = state.get("user_request", "")
    output_type = state.get("output_type", "docx")
    research = state.get("research_results", [])
    kb_data = state.get("kb_results", [])
    profile = state.get("template_profile", {})

    # Format research context
    research_text = "\n".join(
        f"- {r.get('text', '')} (Source: {r.get('source_title', 'N/A')})" for r in research[:10]
    )

    # Format KB context
    kb_text = "\n".join(
        f"- {r.get('text', '')} (From: {r.get('source_filename', 'N/A')})" for r in kb_data[:5]
    )

    # Template style guidance
    tone = profile.get("tone", "professional") if profile else "professional"
    sections = profile.get("section_patterns", []) if profile else []
    section_hint = f"Follow these section patterns: {', '.join(sections)}" if sections else ""

    llm = get_llm_client()

    if output_type == "pptx":
        model = _generate_deck(llm, user_request, research_text, kb_text, tone, section_hint, profile)
        model_dict = model.model_dump()
    else:
        model = _generate_document(llm, user_request, research_text, kb_text, tone, section_hint)
        model_dict = model.model_dump()

    duration_ms = int((time.time() - start) * 1000)
    logger.info("Generator produced %s model in %dms", output_type, duration_ms)

    return {
        "document_model": model_dict,
        "messages": [AIMessage(content=f"Generated {output_type.upper()} content with {_count_sections(model_dict)} sections/slides.")],
    }


def _generate_document(llm, request, research, kb, tone, section_hint) -> DocumentModel:
    """Generate a DocumentModel via Gemini structured output."""
    prompt = f"""Create a professional document based on this request:

REQUEST: {request}

TONE: {tone}
{section_hint}

WEB RESEARCH:
{research}

INTERNAL KNOWLEDGE:
{kb}

Instructions:
1. Create a well-structured document with clear sections and headings.
2. Use [n] citation markers for facts (n = sequential number starting from 1).
3. Include a sources_section listing all cited sources with id, label, and url.
4. Match the tone: {tone}
5. Be thorough, detailed, and well-organized.
6. Each section should have a heading, heading_level (1-3), and body text.
"""
    return llm.generate_json(prompt, DocumentModel, use_cache=False, temperature=0.4)


def _generate_deck(llm, request, research, kb, tone, section_hint, profile) -> DeckModel:
    """Generate a DeckModel via Gemini structured output."""
    # Get available layouts from profile
    layouts = profile.get("slide_layouts", []) if profile else []
    layout_info = ""
    if layouts:
        layout_names = [l.get("name", f"Layout {l.get('index', '?')}") for l in layouts]
        layout_info = f"Available slide layouts: {', '.join(layout_names)}"

    prompt = f"""Create a professional presentation based on this request:

REQUEST: {request}

TONE: {tone}
{section_hint}
{layout_info}

WEB RESEARCH:
{research}

INTERNAL KNOWLEDGE:
{kb}

Instructions:
1. Create 8-12 slides with clear, concise content.
2. First slide should be a title slide.
3. Use [n] citation markers for facts.
4. Include speaker notes for each slide.
5. For placeholders, use keys like "0" for title and "1" for body content.
6. Include a sources_slide listing all cited sources.
7. Each slide needs: layout_name, placeholders (dict), speaker_notes, citations.
"""
    return llm.generate_json(prompt, DeckModel, use_cache=False, temperature=0.4)


def _count_sections(model_dict: dict) -> int:
    """Count sections or slides in a model dict."""
    return len(model_dict.get("sections", model_dict.get("slides", [])))
