"""Editor agent – applies edit instructions to an existing DocumentModel or DeckModel."""

from __future__ import annotations

import json
import logging
import time

from langchain_core.messages import AIMessage

from app.agents.state import AgentState
from app.llm.client import get_llm_client
from app.llm.schemas import DeckModel, DocumentModel

logger = logging.getLogger(__name__)


def editor_node(state: AgentState) -> dict:
    """Edit an existing document model based on user instructions."""
    start = time.time()

    model_dict = state.get("document_model")
    edit_instructions = state.get("edit_instructions", [])
    output_type = state.get("output_type", "docx")

    if not model_dict:
        return {
            "messages": [AIMessage(content="No existing document to edit.")],
        }

    llm = get_llm_client()
    edits_text = json.dumps(edit_instructions, indent=2) if edit_instructions else "No specific edits"

    prompt = f"""You are editing an existing {output_type.upper()} document.

CURRENT DOCUMENT MODEL:
{json.dumps(model_dict, indent=2)}

EDIT INSTRUCTIONS:
{edits_text}

Instructions:
1. Apply the requested edits to the document model.
2. Preserve all unchanged sections/slides exactly as they are.
3. Maintain [n] citation markers and add new ones as needed.
4. Return the complete updated model with all sections/slides.
"""

    if output_type == "pptx":
        updated = llm.generate_json(prompt, DeckModel, use_cache=False, temperature=0.3)
    else:
        updated = llm.generate_json(prompt, DocumentModel, use_cache=False, temperature=0.3)

    duration_ms = int((time.time() - start) * 1000)
    logger.info("Editor applied edits in %dms", duration_ms)

    return {
        "document_model": updated.model_dump(),
        "messages": [AIMessage(content="Document edited successfully.")],
    }
