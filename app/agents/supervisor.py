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
    document_type: Literal[
        "proposal", "research_report", "company_profile", "market_analysis", "generic"
    ] = Field(
        default="generic",
        description="Classified document type ('proposal', 'research_report', 'company_profile', 'market_analysis', or 'generic').",
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
    resolved_message: str = Field(
        default="",
        description="User message rewritten as a standalone instruction (e.g. 'make it shorter' -> 'make the presentation shorter').",
    )
    target_artifact_ids: list[int] = Field(
        default_factory=list,
        description="Target artifact IDs to edit or query.",
    )
    answer_scope: Literal["document", "fresh"] | None = Field(
        default=None,
        description="Scope for Q&A answers: 'document' (from existing session artifact) or 'fresh' (web/KB search).",
    )
    needs_new_facts: bool = Field(
        default=False,
        description="True for edits that add or expand a topic or require new external/KB facts. False for shortening/rewording.",
    )


SUPERVISOR_PROMPT = """You are an intent parser and supervisor planner for an enterprise document & presentation generator chatbot.
Analyze the user's message and session context, then output a structured JSON plan matching the Plan schema.

USER MESSAGE:
"{user_message}"

SESSION CONTEXT:
Active Topic: {active_topic}
Existing Artifacts in Session:
{session_artifacts_summary}
Recent Messages:
{recent_messages_summary}

ROUTING RULES:
1. `action`:
   - SESSION HAS NO ARTIFACTS:
     - Default to "generate" for any topic, research, or content request, even if phrased as a question (e.g., "how is X developed", "tell me about Y"). Set outputs to ["docx", "pptx"] unless a specific format is requested.
     - Use "answer" ONLY when the user explicitly signals they want a quick reply with no file generated (e.g., "just tell me", "quick answer", "no need for a file", "don't generate a file", "just answer", "in short", "briefly"). Set answer_scope="fresh".
   - SESSION HAS EXISTING ARTIFACTS:
     - Default to "edit" for any message that modifies, appends, extends, refines, shortens, expands, removes or reorders content, or refers to the existing report/deck/section/slide (e.g. "add X", "also include Y", "make section 2 longer", "make it shorter", "update pricing").
     - Use "generate" ONLY when the user clearly asks for a NEW, different report/deck (e.g., phrases like "new report", "another report", "start over", "create a new one on...", or a completely new topic unrelated to the existing document).
     - Use "answer" when:
       * The user asks a question about existing report/deck content (e.g. "what does the report say about X?"). In this case, set answer_scope="document".
       * The user explicitly wants a quick answer / no file generated. Set answer_scope="fresh".
     - Use "convert" if converting between formats (e.g., "convert this report to presentation").

2. `resolved_message`:
   Rewrite the user's message as a clear, standalone instruction with pronouns and references resolved.
   For example:
   - "make it shorter" -> "make the presentation shorter"
   - "add pricing" -> "add a pricing section to the report"
   - "do the same for the deck" -> "add pricing slides to the presentation deck"
   - "what does it say about revenue?" -> "what does the report say about revenue?"
   If the instruction is already standalone, keep it as is.

3. `target_artifact_ids`:
   - If the user names a specific artifact (e.g. "in the first report", title mention, or specific ID), select that artifact's ID.
   - Otherwise, target the active artifact(s) matching the requested format (docx, pptx, or both).

4. `outputs`: Select ["docx"], ["pptx"], or ["docx", "pptx"].
   - keywords like "slide", "deck", "presentation" -> ["pptx"]
   - keywords like "doc", "document", "report", "proposal" -> ["docx"]
   - both or general -> ["docx", "pptx"]

5. `document_type`: "proposal", "research_report", "company_profile", "market_analysis", or "generic".
6. `slide_count`: Number of slides requested (default 12).
7. `use_web`: Set true if web research is useful.
8. `use_kb`: Set true if company KB/context is relevant.
9. `needs_new_facts`: Set true if the action is 'edit' and the edit adds or expands a topic (e.g. 'append information about Python', 'add a section on security', 'include pricing facts'), or asks for new factual coverage. Set false for pure shortening, rewording, formatting, condensing, or deleting.
"""

QUICK_ANSWER_PATTERN = re.compile(
    r"\b("
    r"just\s+tell\s+me|"
    r"quick\s+answer|"
    r"no\s+need\s+for\s+(?:a\s+)?files?|"
    r"no\s+files?\s+needed|"
    r"don'?t\s+(?:generate|create|make)\s+(?:a\s+)?files?|"
    r"do\s+not\s+(?:generate|create|make)\s+(?:a\s+)?files?|"
    r"just\s+answer|"
    r"in\s+short|"
    r"briefly"
    r")\b",
    re.IGNORECASE,
)

EDIT_PATTERN = re.compile(
    r"\b("
    r"add|append|include|also|update|change|remove|delete|shorten|expand|rewrite|replace|fix|"
    r"make\s+it|modify|condense|more\s+concise|longer|shorter|edit"
    r")\b|\b(?:section\s+\d+|slide\s+\d+)\b",
    re.IGNORECASE,
)

NEW_REPORT_PATTERN = re.compile(
    r"\b("
    r"new\s+report|another\s+report|start\s+over|create\s+a\s+new\s+one(?:\s+on)?|"
    r"fresh\s+report|new\s+deck|new\s+presentation|start\s+fresh|create\s+new"
    r")\b",
    re.IGNORECASE,
)


def parse_plan(
    user_message: str,
    file_ids: list[int] | None = None,
    llm_client: LLMClient | None = None,
    session_context: dict[str, Any] | None = None,
    session_artifacts: list[dict[str, Any]] | None = None,
) -> Plan:
    """Parse user message into a Plan using regex + ONE LLM call.

    Args:
        user_message: User chat message text.
        file_ids: Optional list of uploaded file IDs.
        llm_client: Optional LLMClient instance.
        session_context: Optional session context dict (recent_messages, artifacts, active_topic).
        session_artifacts: Optional list of artifacts for current session.

    Returns:
        Structured Plan object.
    """
    file_ids = file_ids or []
    if llm_client is None:
        llm_client = get_llm_client()

    # Extract session artifacts and context
    artifacts = session_artifacts or []
    if not artifacts and session_context:
        artifacts = session_context.get("artifacts", [])
    has_artifacts = len(artifacts) > 0

    recent_messages = session_context.get("recent_messages", []) if session_context else []
    active_topic = session_context.get("active_topic", "") if session_context else ""

    # Format session context summaries
    if artifacts:
        art_summaries = []
        for a in artifacts:
            outline_str = ", ".join(a.get("outline", [])[:6]) if a.get("outline") else "No outline"
            active_str = " [ACTIVE]" if a.get("active") else ""
            art_summaries.append(
                f"- ID {a['artifact_id']} ({a['kind'].upper()}) v{a['latest_version']}{active_str}: '{a['title']}' | Outline: {outline_str}"
            )
        session_artifacts_summary = "\n".join(art_summaries)
    else:
        session_artifacts_summary = "(None - this is a fresh session with no generated documents)"

    if recent_messages:
        recent_messages_summary = "\n".join(
            f"- {m.get('role', 'user')}: {m.get('content', '')[:150]}"
            for m in recent_messages
        )
    else:
        recent_messages_summary = "(None)"

    # 1. Regex fast-path checks BEFORE LLM call
    extracted_slides = None
    slide_match = re.search(r"(\d+)\s*[-_\s]*slides?", user_message, re.IGNORECASE)
    if slide_match:
        extracted_slides = int(slide_match.group(1))

    has_quick_answer = bool(QUICK_ANSWER_PATTERN.search(user_message))
    has_edit_verb = bool(EDIT_PATTERN.search(user_message))
    has_new_report = bool(NEW_REPORT_PATTERN.search(user_message))

    # Active artifact IDs fallback
    active_art_ids = [a["artifact_id"] for a in artifacts if a.get("active")]
    if not active_art_ids and artifacts:
        active_art_ids = [artifacts[-1]["artifact_id"]]

    # 2. LLM Call to parse plan schema
    prompt = SUPERVISOR_PROMPT.format(
        user_message=user_message,
        active_topic=active_topic or "(None)",
        session_artifacts_summary=session_artifacts_summary,
        recent_messages_summary=recent_messages_summary,
    )

    try:
        plan = llm_client.generate_json(
            prompt=prompt,
            schema=Plan,
            system="You are a precise intent classification agent. Route accurately based on whether the session has existing artifacts.",
        )
    except Exception as exc:
        logger.warning(f"Supervisor LLM call failed: {exc}. Falling back to rule-based plan.")
        msg_lower = user_message.lower()
        outputs: list[Literal["docx", "pptx"]] = []
        if any(w in msg_lower for w in ["doc", "document", "proposal", "report"]):
            outputs.append("docx")
        if any(w in msg_lower for w in ["ppt", "slide", "presentation", "deck"]):
            outputs.append("pptx")
        if not outputs:
            outputs = ["docx", "pptx"]

        resolved_msg = user_message
        target_ids = []
        ans_scope = None

        if has_artifacts:
            if has_new_report:
                action: Literal["generate", "edit", "convert", "answer"] = "generate"
            elif has_quick_answer:
                action = "answer"
                ans_scope = "fresh"
            elif any(w in msg_lower for w in ["what does", "how does", "tell me about the report", "according to"]) or (msg_lower.endswith("?") and not has_edit_verb):
                action = "answer"
                ans_scope = "document"
                target_ids = active_art_ids
            elif "convert" in msg_lower:
                action = "convert"
            else:
                action = "edit"
                target_ids = active_art_ids
                if "shorter" in msg_lower or "concise" in msg_lower:
                    resolved_msg = "make the presentation shorter" if "pptx" in outputs else "make the document shorter"
                elif has_edit_verb:
                    resolved_msg = f"{user_message} in the existing document"
        else:
            if "convert" in msg_lower:
                action = "convert"
            elif has_quick_answer:
                action = "answer"
                ans_scope = "fresh"
            else:
                action = "generate"

        doc_type: Literal["proposal", "research_report", "company_profile", "market_analysis", "generic"] = "generic"
        if "proposal" in msg_lower or "pitch" in msg_lower or "rfp" in msg_lower:
            doc_type = "proposal"
        elif "report" in msg_lower or "research" in msg_lower or "development" in msg_lower or "develop" in msg_lower:
            doc_type = "research_report"
        elif "profile" in msg_lower or "company" in msg_lower:
            doc_type = "company_profile"
        elif "market" in msg_lower or "competitor" in msg_lower or "industry" in msg_lower:
            doc_type = "market_analysis"

        plan = Plan(
            action=action,
            document_type=doc_type,
            outputs=outputs,
            topic=user_message,
            slide_count=extracted_slides or 12,
            resolved_message=resolved_msg,
            target_artifact_ids=target_ids,
            answer_scope=ans_scope,
        )

    # 3. Post-process and enforce intent rules:
    if extracted_slides is not None:
        plan.slide_count = extracted_slides

    if not plan.resolved_message:
        plan.resolved_message = user_message

    # Check for specific target mentions like "first report"
    msg_low = user_message.lower()

    # Enforce routing guarantees
    if has_artifacts:
        if has_new_report:
            plan.action = "generate"
        elif has_edit_verb and plan.action != "convert":
            plan.action = "edit"
        elif has_quick_answer:
            plan.action = "answer"
            if not plan.answer_scope:
                plan.answer_scope = "fresh"
        elif plan.action == "answer":
            if not plan.answer_scope:
                plan.answer_scope = "document" if (not has_quick_answer) else "fresh"

    # Enforce needs_new_facts on edits
    if plan.action == "edit":
        is_shortening_or_condensing = bool(
            re.search(r"\b(shorten|condense|briefer|concise|summarize|delete|remove|fix\s+typo)\b", user_message, re.IGNORECASE)
        )
        is_topic_addition = bool(
            re.search(r"\b(add|append|include|also|expand|new\s+topic|more\s+about|extend|facts|information\s+about)\b", user_message, re.IGNORECASE)
        )
        if is_topic_addition and not is_shortening_or_condensing:
            plan.needs_new_facts = True
        elif is_shortening_or_condensing:
            plan.needs_new_facts = False
    else:
        # No artifacts: cannot edit or answer document
        if plan.action == "edit":
            # If session has no artifacts, plan can stay edit so node_edit returns a friendly message,
            # or default to generate if it's a content request
            pass
        elif has_quick_answer:
            plan.action = "answer"
            plan.answer_scope = "fresh"
        elif plan.action == "answer" and not has_quick_answer:
            plan.action = "generate"
            if not plan.outputs:
                plan.outputs = ["docx", "pptx"]

    # Target artifact IDs resolution
    if "first report" in msg_low or "first document" in msg_low:
        docx_arts = [a for a in artifacts if a.get("kind") == "docx"]
        if docx_arts:
            plan.target_artifact_ids = [docx_arts[0]["artifact_id"]]
    elif not plan.target_artifact_ids and plan.action in ("edit", "answer") and has_artifacts:
        if any(w in msg_low for w in ["ppt", "slide", "presentation", "deck"]):
            pptx_targets = [a["artifact_id"] for a in artifacts if a.get("kind") == "pptx"]
            plan.target_artifact_ids = pptx_targets[:1] if pptx_targets else active_art_ids
        elif any(w in msg_low for w in ["doc", "document", "proposal", "report"]):
            docx_targets = [a["artifact_id"] for a in artifacts if a.get("kind") == "docx"]
            plan.target_artifact_ids = docx_targets[:1] if docx_targets else active_art_ids
        else:
            plan.target_artifact_ids = active_art_ids

    # If resolved_message is just "make it shorter" and deck is target, make it specific
    if "make it shorter" in msg_low or "make shorter" in msg_low:
        if any(a.get("kind") == "pptx" for a in artifacts if a["artifact_id"] in plan.target_artifact_ids):
            plan.resolved_message = "make the presentation shorter"
        elif any(a.get("kind") == "docx" for a in artifacts if a["artifact_id"] in plan.target_artifact_ids):
            plan.resolved_message = "make the document shorter"
        else:
            plan.resolved_message = "make the document and presentation shorter"

    return plan
