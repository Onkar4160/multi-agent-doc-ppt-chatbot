"""Supervisor Agent – parses user messages into strongly typed ExecutionPlans."""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from app.llm.client import get_llm_client, LLMClient
from app.models.context import ConversationContext
from app.models.execution_plan import (
    ArtifactType,
    ExecutionPlan,
    OperationType,
    Plan,
    TemplateMode,
)
from app.services.reference_resolver import resolve_references, ResolvedReferences

logger = logging.getLogger(__name__)

__all__ = [
    "Plan",
    "ExecutionPlan",
    "OperationType",
    "ArtifactType",
    "TemplateMode",
    "parse_plan",
]

SUPERVISOR_PROMPT = """You are an intent parser and supervisor planner for an enterprise document, presentation & workbook platform.
Analyze the user's message, conversation context, and active artifacts to produce a structured ExecutionPlan matching the schema.

USER MESSAGE:
"{user_message}"

CONVERSATION CONTEXT:
Active Topic: {active_topic}
Conversation Summary: {conversation_summary}
Existing Artifacts in Session:
{session_artifacts_summary}
Recent Messages:
{recent_messages_summary}

TAXONOMY & ROUTING RULES:
1. `operation`:
   - CREATE: Create new document/presentation/workbook from scratch.
   - EDIT: Modify, shorten, expand, or adjust content in an existing artifact.
   - REGENERATE: Re-generate or recreate an existing artifact (e.g. "regenerate the presentation using the same template").
   - APPEND: Add new content specifically to the end (e.g. "append a conclusion to the existing proposal").
   - INSERT: Insert a new section or slide at a specific position (e.g. "insert a slide after slide 3").
   - DELETE: Remove a section or slide (e.g. "remove slide 5").
   - REPLACE: Replace an existing section/slide/component with new content.
   - MOVE: Reorder or move a section or slide (e.g. "move slide 7 before slide 4").
   - RESTYLE: Change visual styling, layout, theme, or formatting.
   - REFRESH: Update existing artifact content with fresh/latest information or web research.
   - CONVERT: Format conversion (e.g. DOCX to PPTX).
   - ANSWER: Q&A without creating or editing files.

2. `artifact_types`:
   - [PPTX] for "presentation", "slides", "deck", "powerpoint".
   - [DOCX] for "document", "report", "proposal", "Word document".
   - [XLSX] for "Excel", "spreadsheet", "workbook", "calculation sheet".
   - [DOCX, PPTX] when general/both requested.
   - [UNKNOWN] if ambiguous or unspecified with no active artifact in session.

3. `template_mode`:
   - UPLOADED: user references uploaded template.
   - EXISTING: reusing the same template as the active artifact.
   - DEFAULT: system default template.
   - NONE: blank or no template.

4. `use_web`:
   - Set true ONLY when the user asks for "latest", "recent", "today", "current", "trends", "search the web", or REFRESH operations.
   - Set false if explicitly asked not to search or if standard factual content.

5. `use_rag`:
   - Set true ONLY when user asks for "company documents", "internal knowledge base", "internal policies".
   - Directly uploaded files should be marked in source_file_ids/source_references, not use_rag.

6. `user_instruction`:
   Rewrite instruction resolving pronouns ("make it shorter" -> "make the presentation shorter").

7. `slide_count`: Number of slides requested (default 12).
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


def _detect_deterministic_operation(
    user_msg: str,
    has_artifacts: bool,
    has_new_report: bool,
    has_quick_answer: bool,
) -> OperationType:
    """Classify operation taxonomy deterministically from user prompt."""
    msg = user_msg.lower()

    if "regenerat" in msg:
        return OperationType.REGENERATE
    if "append" in msg or "to the end" in msg or "at the end" in msg:
        return OperationType.APPEND
    if "insert" in msg:
        return OperationType.INSERT
    if "delete" in msg or "remove" in msg:
        return OperationType.DELETE
    if "replace" in msg or "substitute" in msg:
        return OperationType.REPLACE
    if "move" in msg or "reorder" in msg:
        return OperationType.MOVE
    if "restyle" in msg or "visual style" in msg:
        return OperationType.RESTYLE
    if "convert" in msg:
        return OperationType.CONVERT
    if "refresh" in msg or (("latest" in msg or "recent" in msg or "trend" in msg) and ("update" in msg or "modify" in msg)):
        return OperationType.REFRESH
    if has_quick_answer:
        return OperationType.ANSWER

    if has_artifacts:
        if has_new_report:
            return OperationType.CREATE
        if any(w in msg for w in ["what does", "how does", "tell me about the report", "according to"]) or (msg.endswith("?") and not EDIT_PATTERN.search(msg)):
            return OperationType.ANSWER
        if EDIT_PATTERN.search(msg):
            return OperationType.EDIT
        # If user message starts with create/make/generate without referring to new report
        if any(msg.startswith(w) for w in ["create", "make", "generate", "build", "draft", "write"]):
            return OperationType.CREATE
        return OperationType.EDIT
    else:
        if any(w in msg for w in ["what does", "how does"]) and not any(msg.startswith(w) for w in ["create", "make", "generate"]):
            return OperationType.ANSWER
        return OperationType.CREATE


def _detect_deterministic_artifacts(
    user_msg: str,
    active_art: dict[str, Any] | None,
    artifacts: list[dict[str, Any]],
) -> tuple[list[ArtifactType], float]:
    """Infer target artifact types and confidence."""
    msg = user_msg.lower()
    types: list[ArtifactType] = []

    has_deck = any(w in msg for w in ["ppt", "slide", "presentation", "deck", "powerpoint"])
    has_doc = any(w in msg for w in ["doc", "document", "proposal", "report", "word"])
    has_sheet = any(w in msg for w in ["excel", "spreadsheet", "workbook", "sheets", "sheet", "table/calculation sheet"])

    if has_sheet:
        types.append(ArtifactType.XLSX)
    if has_deck:
        types.append(ArtifactType.PPTX)
    if has_doc:
        types.append(ArtifactType.DOCX)

    if types:
        return types, 1.0

    # No explicit format mentioned: fallback to active artifact context
    if active_art:
        kind = active_art.get("kind", "").upper()
        if kind == "PPTX":
            return [ArtifactType.PPTX], 0.9
        elif kind == "DOCX":
            return [ArtifactType.DOCX], 0.9
        elif kind == "XLSX":
            return [ArtifactType.XLSX], 0.9

    if artifacts:
        latest = artifacts[-1]
        kind = latest.get("kind", "").upper()
        if kind in ("PPTX", "DOCX", "XLSX"):
            return [ArtifactType(kind)], 0.8

    has_pronoun_or_ambig = bool(re.search(r"\b(it|this|that|better|improve|fix|shorter|longer)\b", msg))
    if has_pronoun_or_ambig and not any(msg.startswith(w) for w in ["create", "generate", "make a", "draft a", "write a"]):
        return [ArtifactType.UNKNOWN], 0.4

    if any(msg.startswith(w) for w in ["how", "what", "tell", "explain", "research", "overview", "develop"]):
        return [ArtifactType.DOCX, ArtifactType.PPTX], 0.7

    # Ambiguous request without format or active artifact
    return [ArtifactType.UNKNOWN], 0.4


def _detect_deterministic_web_search(user_msg: str, operation: OperationType) -> bool:
    """Infer whether external web research is needed with strict negative instruction priority."""
    msg = user_msg.lower()
    if any(phrase in msg for phrase in [
        "don't search the web", "do not search the web", "no web search", "no web",
        "offline", "without web", "do not use web", "don't use web",
        "do not search online", "don't search online",
        "use only the attached", "use only attached", "only from the attached",
        "using only the attached", "using only attached", "use only the attached document",
        "use only the attached documents", "use only the attached file", "use only the attached files"
    ]):
        return False
    if operation == OperationType.REFRESH:
        return True
    if any(w in msg for w in [
        "latest", "recent", "today", "current", "2026", "trends",
        "search the web", "research online", "market trends", "latest news",
        "search online", "web research"
    ]):
        return True
    return False


def _detect_deterministic_rag(user_msg: str) -> bool:
    """Infer whether enterprise knowledge retrieval is needed."""
    msg = user_msg.lower()
    if any(phrase in msg for phrase in [
        "do not use rag", "don't use rag", "no rag",
        "do not use knowledge base", "without knowledge base", "without kb"
    ]):
        return False
    if any(phrase in msg for phrase in [
        "use only the attached", "use only attached", "only from the attached",
        "using only the attached", "using only attached"
    ]) and not any(kb_phrase in msg for kb_phrase in [
        "knowledge base", "company kb", "internal knowledge base", "rag"
    ]):
        return False
    return any(phrase in msg for phrase in [
        "company documents",
        "knowledge base",
        "company knowledge base",
        "internal policies",
        "internal documents",
        "from our documents",
        "company wiki",
        "enterprise kb",
        "company kb",
        "our knowledge base",
        "internal product information",
        "internal product info",
        "use rag",
    ])


def _detect_deterministic_template(
    user_msg: str,
    operation: OperationType,
    resolved_refs: ResolvedReferences,
    has_artifacts: bool,
) -> TemplateMode:
    """Infer template handling mode."""
    if resolved_refs.template_mode:
        return resolved_refs.template_mode

    msg = user_msg.lower()
    if any(phrase in msg for phrase in [
        "uploaded template", "company template", "uploaded company template",
        "attached template", "given template", "in the given template format",
        "using the attached template", "use the attached template",
        "attached docx", "attached pptx", "given template format"
    ]):
        return TemplateMode.UPLOADED
    if "same template" in msg or "existing template" in msg or "previous template" in msg:
        return TemplateMode.EXISTING
    if "default template" in msg or "default presentation" in msg or "default" in msg:
        return TemplateMode.DEFAULT
    if "no template" in msg or "blank template" in msg:
        return TemplateMode.NONE

    if operation in (OperationType.EDIT, OperationType.REGENERATE, OperationType.APPEND, OperationType.REFRESH) and has_artifacts:
        return TemplateMode.EXISTING

    return TemplateMode.DEFAULT



def parse_plan(
    user_message: str,
    file_ids: list[int] | None = None,
    llm_client: LLMClient | None = None,
    session_context: dict[str, Any] | None = None,
    session_artifacts: list[dict[str, Any]] | None = None,
) -> ExecutionPlan:
    """Parse user message into a strongly typed ExecutionPlan.

    Uses conversation reference resolution + priority-based extraction + LLM parsing with deterministic fallback.
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
    active_art = session_context.get("active_artifact") if session_context else None
    conv_summary = session_context.get("conversation_summary", "") if session_context else ""

    # 1. Reference Resolution Layer
    resolved_refs = resolve_references(user_message, session_context)

    # 2. Priority #1: Explicit current user instruction extraction
    extracted_slides = None
    slide_match = re.search(r"(\d+)\s*[-_\s]*slides?", user_message, re.IGNORECASE)
    if slide_match:
        extracted_slides = int(slide_match.group(1))

    has_quick_answer = bool(QUICK_ANSWER_PATTERN.search(user_message))
    has_edit_verb = bool(EDIT_PATTERN.search(user_message))
    has_new_report = bool(NEW_REPORT_PATTERN.search(user_message))

    # Deterministic signals
    op_type = _detect_deterministic_operation(user_message, has_artifacts, has_new_report, has_quick_answer)
    art_types, art_confidence = _detect_deterministic_artifacts(user_message, active_art, artifacts)
    use_web = _detect_deterministic_web_search(user_message, op_type)
    use_rag = _detect_deterministic_rag(user_message)
    tmpl_mode = _detect_deterministic_template(user_message, op_type, resolved_refs, has_artifacts)

    # Target artifact resolution
    target_art_id = resolved_refs.target_artifact_id
    if not target_art_id and has_artifacts and op_type in (OperationType.EDIT, OperationType.ANSWER, OperationType.APPEND, OperationType.REGENERATE, OperationType.REFRESH):
        active_art_ids = [a["artifact_id"] for a in artifacts if a.get("active")]
        target_art_id = active_art_ids[0] if active_art_ids else artifacts[-1]["artifact_id"]

    # Preserve constraints
    preserve_constraints: list[str] = []
    if op_type in (OperationType.APPEND, OperationType.INSERT):
        preserve_constraints.append("preserve_existing_content")
    if op_type == OperationType.REGENERATE:
        preserve_constraints.append("preserve_style")
    if "preserve" in user_message.lower():
        preserve_constraints.append("preserve_user_constraints")

    # Source & Template files resolution via File Intelligence
    template_file_ids: list[int] = []
    source_file_ids: list[int] = []
    ambiguity_error: str | None = None

    if file_ids:
        from app.models.file_role import FileRole
        from app.services.file_classifier import classify_attached_files
        classifications = classify_attached_files(
            file_ids=file_ids,
            user_prompt=user_message,
            context=session_context,
        )
        ambiguous_templates = []
        for c in classifications:
            if c.is_ambiguous:
                ambiguous_templates.append(c.filename)
            if c.role == FileRole.TEMPLATE:
                if c.file_id:
                    template_file_ids.append(int(c.file_id))
            elif c.role in (FileRole.SOURCE, FileRole.REFERENCE, FileRole.INPUT_DOCUMENT, FileRole.RESEARCH_MATERIAL):
                if c.file_id:
                    source_file_ids.append(int(c.file_id))

        if ambiguous_templates:
            ambiguity_error = f"I found multiple candidate templates: {', '.join(ambiguous_templates)}. Which one should I use?"

        if template_file_ids and tmpl_mode == TemplateMode.DEFAULT:
            tmpl_mode = TemplateMode.UPLOADED
    else:
        template_file_ids = []
        source_file_ids = []

    source_refs = list(resolved_refs.source_file_references)
    if not source_refs and source_file_ids:
        source_refs.append(f"uploaded_files_{source_file_ids}")

    # Document type inference
    doc_type = "generic"
    msg_lower = user_message.lower()
    if "proposal" in msg_lower or "pitch" in msg_lower or "rfp" in msg_lower:
        doc_type = "proposal"
    elif "report" in msg_lower or "research" in msg_lower or "development" in msg_lower or "develop" in msg_lower:
        doc_type = "research_report"
    elif "profile" in msg_lower or "company" in msg_lower:
        doc_type = "company_profile"
    elif "market" in msg_lower or "competitor" in msg_lower or "industry" in msg_lower:
        doc_type = "market_analysis"

    # Resolved instruction
    resolved_msg = resolved_refs.resolved_message or user_message
    if op_type == OperationType.EDIT and "shorter" in msg_lower:
        if ArtifactType.PPTX in art_types:
            resolved_msg = "make the presentation shorter"
        elif ArtifactType.DOCX in art_types:
            resolved_msg = "make the document shorter"

    confidence = min(resolved_refs.confidence, art_confidence)
    reasoning_str = "; ".join(resolved_refs.reasoning) if resolved_refs.reasoning else f"Operation classified as {op_type.value}"

    # Format session context summaries for LLM prompt
    if artifacts:
        art_summaries = []
        for a in artifacts:
            outline_str = ", ".join(a.get("outline", [])[:6]) if a.get("outline") else "No outline"
            active_str = " [ACTIVE]" if a.get("active") else ""
            art_title = a.get("title", f"Artifact {a.get('artifact_id', '')}")
            art_summaries.append(
                f"- ID {a.get('artifact_id')} ({a.get('kind', '').upper()}) v{a.get('latest_version', 1)}{active_str}: '{art_title}' | Outline: {outline_str}"
            )

        session_artifacts_summary = "\n".join(art_summaries)
    else:
        session_artifacts_summary = "(None - this is a fresh session with no generated documents)"

    recent_messages_summary = (
        "\n".join(f"- {m.get('role', 'user')}: {m.get('content', '')[:150]}" for m in recent_messages)
        if recent_messages
        else "(None)"
    )

    prompt = SUPERVISOR_PROMPT.format(
        user_message=user_message,
        active_topic=active_topic or "(None)",
        conversation_summary=conv_summary or "(None)",
        session_artifacts_summary=session_artifacts_summary,
        recent_messages_summary=recent_messages_summary,
    )

    try:
        plan = llm_client.generate_json(
            prompt=prompt,
            schema=ExecutionPlan,
            system="You are a precise intent classification agent. Route accurately based on session context and user instructions.",
        )
    except Exception as exc:
        logger.warning(f"Supervisor LLM call failed or mock: {exc}. Using deterministic execution plan.")
        plan = ExecutionPlan(
            operation=op_type,
            artifact_types=art_types,
            target_artifact_id=target_art_id,
            target_version_id=resolved_refs.target_version_no,
            target_nodes=resolved_refs.target_nodes,
            template_mode=tmpl_mode,
            template_file_ids=[],
            source_file_ids=source_file_ids,
            source_references=source_refs,
            use_web=use_web,
            use_rag=use_rag,
            research_query=user_message if use_web else "",
            document_type=doc_type,
            slide_count=extracted_slides or 12,
            output_requirements={},
            edit_scope=", ".join(resolved_refs.target_nodes) if resolved_refs.target_nodes else "",
            preserve_constraints=preserve_constraints,
            user_instruction=resolved_msg,
            confidence=confidence,
            reasoning=reasoning_str,
        )

    # 3. Post-Process & Deterministic Enforcement
    # Priority #1: Explicit current user instruction always overrides older/default context
    if extracted_slides is not None:
        plan.slide_count = extracted_slides

    # Explicit format overrides
    if art_types == [ArtifactType.UNKNOWN]:
        plan.artifact_types = [ArtifactType.UNKNOWN]
        plan.confidence = min(plan.confidence, 0.4)
    elif art_types:
        plan.artifact_types = art_types

    # Ensure operation accuracy
    if op_type != OperationType.CREATE and plan.operation == OperationType.CREATE and has_artifacts:
        plan.operation = op_type
    elif op_type in (OperationType.REGENERATE, OperationType.APPEND, OperationType.REFRESH, OperationType.CONVERT, OperationType.ANSWER):
        plan.operation = op_type

    # Ensure template mode accuracy
    if tmpl_mode != TemplateMode.DEFAULT or "template" in user_message.lower():
        plan.template_mode = tmpl_mode

    # Ensure web search & RAG accuracy
    plan.use_web = use_web
    plan.use_rag = use_rag

    # Target nodes & source references
    if resolved_refs.target_nodes and not plan.target_nodes:
        plan.target_nodes = resolved_refs.target_nodes
    if source_refs and not plan.source_references:
        plan.source_references = source_refs
    plan.template_file_ids = template_file_ids
    plan.source_file_ids = source_file_ids
    if ambiguity_error:
        plan.error = "AMBIGUOUS_TEMPLATES"
        plan.reasoning = ambiguity_error

    # Target artifact ID resolution
    msg_low = user_message.lower()
    if "first report" in msg_low or "first document" in msg_low:
        docx_arts = [a for a in artifacts if a.get("kind") == "docx"]
        if docx_arts:
            plan.target_artifact_id = docx_arts[0]["artifact_id"]
            plan.target_artifact_ids = [docx_arts[0]["artifact_id"]]
    elif has_artifacts and plan.action in ("edit", "answer"):
        active_art_ids = [a["artifact_id"] for a in artifacts if a.get("active")]
        if any(w in msg_low for w in ["ppt", "slide", "presentation", "deck"]):
            pptx_targets = [a["artifact_id"] for a in artifacts if a.get("kind") == "pptx" and a.get("active")] or [a["artifact_id"] for a in artifacts if a.get("kind") == "pptx"]
            plan.target_artifact_ids = pptx_targets[-1:] if pptx_targets else active_art_ids
        elif any(w in msg_low for w in ["doc", "document", "proposal", "report", "section"]):
            docx_targets = [a["artifact_id"] for a in artifacts if a.get("kind") == "docx" and a.get("active")] or [a["artifact_id"] for a in artifacts if a.get("kind") == "docx"]
            plan.target_artifact_ids = docx_targets[-1:] if docx_targets else active_art_ids
        elif target_art_id:
            plan.target_artifact_id = target_art_id
            plan.target_artifact_ids = [target_art_id]
        else:
            plan.target_artifact_ids = active_art_ids

        if plan.target_artifact_ids:
            plan.target_artifact_id = plan.target_artifact_ids[0]
    elif target_art_id:
        plan.target_artifact_id = target_art_id
        plan.target_artifact_ids = [target_art_id]

    # Preserve constraints
    for pc in preserve_constraints:
        if pc not in plan.preserve_constraints:
            plan.preserve_constraints.append(pc)

    # Instruction & reasoning
    if not plan.user_instruction:
        plan.user_instruction = resolved_msg
    if not plan.reasoning:
        plan.reasoning = reasoning_str
    plan.confidence = confidence

    # Edit-specific logic for backward compatibility
    if plan.operation in (OperationType.EDIT, OperationType.APPEND, OperationType.REFRESH):
        is_shortening = bool(re.search(r"\b(shorten|condense|briefer|concise|summarize|delete|remove|fix\s+typo)\b", user_message, re.IGNORECASE))
        is_topic_addition = bool(re.search(r"\b(add|append|include|also|expand|new\s+topic|more\s+about|extend|facts|information\s+about)\b", user_message, re.IGNORECASE))
        if is_topic_addition and not is_shortening:
            plan.needs_new_facts = True
        elif is_shortening:
            plan.needs_new_facts = False

    if plan.operation == OperationType.ANSWER:
        plan.answer_scope = "document" if (has_artifacts and not has_quick_answer) else "fresh"

    return plan
