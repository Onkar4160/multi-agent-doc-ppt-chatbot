"""Supervisor Agent – parses user messages into structured execution Plans."""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.llm.client import get_llm_client, LLMClient

logger = logging.getLogger(__name__)


class Plan(BaseModel):
    """Structured execution plan parsed from user prompt."""

    action: Literal["generate", "edit", "convert", "answer"] = Field(
        default="generate", description="Primary action requested by the user."
    )
    outputs: list[Literal["docx", "pptx"]] = Field(
        default_factory=lambda: ["docx", "pptx"],
        description="Target output formats to create ('docx', 'pptx', or both).",
    )
    topic: str = Field(
        default="", description="Cleaned topic or user prompt context."
    )
    slide_count: int = Field(
        default=12, description="Target slide count for presentation deck."
    )
    use_web: bool = Field(
        default=True, description="Whether to include web research."
    )
    use_kb: bool = Field(
        default=True, description="Whether to include enterprise KB RAG retrieval."
    )
    doc_template_file_id: int | None = Field(
        default=None, description="Uploaded DOCX template file ID if available."
    )
    ppt_template_file_id: int | None = Field(
        default=None, description="Uploaded PPTX template file ID if available."
    )


SUPERVISOR_PROMPT = """You are an intent parser and supervisor planner for an enterprise document & presentation generator chatbot.
Analyze the user's message and output a structured JSON plan matching the Plan schema.

USER MESSAGE:
"{user_message}"

RULES:
1. `action`: Determine if the user wants to "generate" new files, "edit" existing files, "convert" formats, or simply "answer" a question.
2. `outputs`: Select ["docx"], ["pptx"], or ["docx", "pptx"] depending on what the user requested. If both proposal and slides/presentation are requested, output both.
3. `topic`: Extract the core subject/brief to research and write about.
4. `slide_count`: Number of slides requested (default 12).
5. `use_web`: Set true if web research is useful for current facts.
6. `use_kb`: Set true if company KB/context is relevant.
"""


def parse_plan(
    user_message: str,
    file_ids: list[int] | None = None,
    llm_client: LLMClient | None = None,
) -> Plan:
    """Parse user message into a Plan using regex + ONE LLM call.

    Args:
        user_message: User chat message text.
        file_ids: Optional list of uploaded file IDs.
        llm_client: Optional LLMClient instance.

    Returns:
        Structured Plan object.
    """
    file_ids = file_ids or []
    if llm_client is None:
        llm_client = get_llm_client()

    # 1. Regex check for slide count
    extracted_slides = None
    slide_match = re.search(r"(\d+)\s*[-_\s]*slides?", user_message, re.IGNORECASE)
    if slide_match:
        extracted_slides = int(slide_match.group(1))

    # 2. LLM Call to parse plan schema
    try:
        plan = llm_client.generate_json(
            prompt=SUPERVISOR_PROMPT.format(user_message=user_message),
            schema=Plan,
            system="You are a precise intent classification agent.",
        )
    except Exception as exc:
        logger.warning(f"Supervisor LLM call failed: {exc}. Falling back to default plan.")
        # Infer outputs from message keywords
        msg_lower = user_message.lower()
        outputs: list[Literal["docx", "pptx"]] = []
        if "doc" in msg_lower or "proposal" in msg_lower:
            outputs.append("docx")
        if "ppt" in msg_lower or "slide" in msg_lower or "presentation" in msg_lower:
            outputs.append("pptx")
        if not outputs:
            outputs = ["docx", "pptx"]

        action: Literal["generate", "edit", "convert", "answer"] = "generate"
        if "edit" in msg_lower or "modify" in msg_lower:
            action = "edit"
        elif "convert" in msg_lower:
            action = "convert"
        elif "?" in user_message and "create" not in msg_lower and "make" not in msg_lower:
            action = "answer"

        plan = Plan(
            action=action,
            outputs=outputs,
            topic=user_message,
            slide_count=extracted_slides or 12,
        )

    # 3. Override slide count if regex explicitly matched
    if extracted_slides is not None:
        plan.slide_count = extracted_slides

    # 4. Template rule: map file_ids to doc/ppt template file IDs
    # (caller or template analyzer node will resolve file_ids if specific types are uploaded)
    return plan
