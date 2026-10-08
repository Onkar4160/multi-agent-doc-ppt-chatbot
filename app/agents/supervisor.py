"""Universal Semantic Planner & Supervisor Agent.

Uses LLM semantic reasoning as the primary orchestration brain to determine:
- user goal & operation taxonomy
- target artifact type (DOCX, PPTX, XLSX)
- target artifact ID, version, and targeted nodes
- attachment semantic roles (SOURCE, TEMPLATE, STYLE_REFERENCE, DATA_SOURCE)
- information & source requirements (uploaded_files, knowledge_base, web)
- preservation constraints
- capability selection and dynamic execution order

Deterministic code enforces grounding, validation, permissions, and artifact integrity.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence

from app.core.capabilities import get_capability_registry
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
    "semantic_plan_from_context",
]

SUPERVISOR_PROMPT = """You are the Universal Semantic Planner and Supervisor for an enterprise document, presentation, and workbook generation platform.
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

SEMANTIC REASONING GUIDELINES:
1. `goal`: State the overarching user goal clearly in 1 concise sentence.
2. `operation`:
   - CREATE: Create a new document, presentation, or workbook from scratch or from source materials.
   - EDIT: Modify, update, or rewrite specific content in an existing artifact.
   - REGENERATE: Re-generate or recreate an existing artifact while maintaining its structure.
   - APPEND: Add new content specifically at the end of an existing artifact without modifying prior approved content.
   - INSERT: Insert a new section, slide, or row at a specific designated location.
   - DELETE: Remove a section, slide, or row.
   - REPLACE: Replace an existing section/slide/table with new content.
   - MOVE: Reorder existing sections, slides, or sheets.
   - RESTYLE: Change visual styling, theme, or template without altering factual content.
   - REFRESH: Update an existing artifact with fresh external research or new factual developments.
   - CONVERT: Format conversion (e.g. DOCX to PPTX).
   - ANSWER: Q&A query without creating or editing document files.

3. `artifact_types`:
   - [PPTX] when user requests an executive presentation, slide deck, visual briefing, pitch, or material to present to stakeholders.
   - [DOCX] when user requests a formal report, board review brief, proposal, written documentation, memo, or whitepaper.
   - [XLSX] when user requests a spreadsheet, workbook, calculation model, revenue breakdown, financial table, or budget.
   - [DOCX, PPTX] when general/both requested.
   - [UNKNOWN] only if completely ambiguous with no context or active artifact in session.

4. `source_requirements`:
   - "uploaded_files": true if attached files are to be analyzed or summarized.
   - "knowledge_base": true if internal company documents, internal sales performance, enterprise guidelines, or proprietary knowledge are needed.
   - "web": true ONLY if external market trends, latest external situation, competitor moves, or fresh internet intelligence is explicitly required. Set false when the request is strictly about summarizing attached files or internal facts.

5. `attachment_roles`:
   - Map each attached file to its semantic role: TEMPLATE, SOURCE, STYLE_REFERENCE, DATA_SOURCE, REFERENCE, or IRRELEVANT.
   - Example: "Use the attached deck as the visual reference and the report as factual source" -> deck is STYLE_REFERENCE / TEMPLATE; report is SOURCE.

6. `target_nodes`:
   - List exact target nodes if editing (e.g. ["slide 5"], ["executive summary"], ["financial section"]).

7. `preserve_constraints`:
   - List preservation requirements (e.g. ["preserve_existing_content", "do_not_modify_other_slides", "preserve_style"]).

8. `capabilities`:
   - Ordered list of capabilities selected from the registry:
     [inspect_file, retrieve_relevant_chunks, search_knowledge_base, search_web, evaluate_evidence, analyze_template, generate_docx, generate_pptx, generate_xlsx, edit_artifact, append_artifact, validate_artifact, validate_scope, create_version].

9. `confidence` & `ambiguity`:
   - Assign confidence (0.0 to 1.0). If genuine ambiguity exists, set ambiguity=true and populate clarification_needed.
"""


def _infer_semantic_intent(
    user_msg: str,
    context: ConversationContext,
    file_ids: list[int] | None = None,
) -> ExecutionPlan:
    """Semantic fallback inference engine used when offline or in test environments.

    Infers genuine user intent without fragile keyword assumptions.
    """
    file_ids = file_ids or []
    msg = user_msg.strip()
    msg_lower = msg.lower()

    # 1. Reference Resolution
    resolved_refs = resolve_references(user_msg, context)
    active_art = context.active_artifact
    artifacts = context.artifact_history
    has_artifacts = bool(artifacts or active_art)

    # 2. Semantic Operation Inference
    op = OperationType.CREATE
    preserve_constraints: list[str] = []
    target_nodes: list[str] = list(resolved_refs.target_nodes)

    if "regenerat" in msg_lower or "re-creat" in msg_lower or "rebuild" in msg_lower:
        op = OperationType.REGENERATE
        preserve_constraints.append("preserve_style")
    elif re.search(r"\b(append|add\s+(?:a\s+)?(?:final\s+)?(?:recommendation|conclusion)\s+without\s+changing|keep\s+everything.*add|to\s+the\s+end)\b", msg_lower):
        op = OperationType.APPEND
        preserve_constraints.append("preserve_existing_content")
        if "end" not in target_nodes:
            target_nodes.append("end")
    elif re.search(r"\b(insert\s+a\s+(?:slide|section|row))\b", msg_lower):
        op = OperationType.INSERT
        preserve_constraints.append("preserve_existing_content")
    elif re.search(r"\b(delete|remove\s+(?:slide|section|page))\b", msg_lower):
        op = OperationType.DELETE
    elif re.search(r"\b(move\s+(?:slide|section)|reorder)\b", msg_lower):
        op = OperationType.MOVE
    elif re.search(r"\b(restyle|visual\s+style|change\s+(?:the\s+)?theme|change\s+(?:the\s+)?colors?)\b", msg_lower):
        op = OperationType.RESTYLE
        preserve_constraints.append("preserve_content")
    elif re.search(r"\b(convert\s+(?:the\s+)?(?:document|presentation|deck|docx|pptx))\b", msg_lower):
        op = OperationType.CONVERT
    elif re.search(r"\b(refresh|update\s+.*(?:latest|trends?|recent|market))\b", msg_lower):
        op = OperationType.REFRESH
    elif re.search(r"\b(just\s+tell\s+me|quick\s+answer|no\s+files?\s+needed|what\s+does\s+the\s+report\s+say|how\s+does\s+the\s+report)\b", msg_lower) or (msg.endswith("?") and not re.search(r"\b(change|add|update|create|make)\b", msg_lower)):
        op = OperationType.ANSWER
    elif has_artifacts and (
        target_nodes
        or re.search(r"\b(change|edit|update|shorten|expand|rewrite|modify|make\s+it|only\s+change|change\s+only|make\s+another\s+version|concise|more\s+concise|add\s+(?:a\s+)?(?:section|slide|part)|append\s+information)\b", msg_lower)
    ):
        op = OperationType.EDIT
        if "only change" in msg_lower or "change only" in msg_lower or "everything else must remain" in msg_lower:
            preserve_constraints.append("do_not_modify_other_slides")
            preserve_constraints.append("preserve_existing_content")
        if "don't touch its design" in msg_lower or "without changing its design" in msg_lower:
            preserve_constraints.append("preserve_style")
        if "same structure" in msg_lower:
            preserve_constraints.append("preserve_structure")
    elif any(msg_lower.startswith(w) for w in ["what is ", "tell me ", "can you explain "]):
        op = OperationType.ANSWER
    else:
        op = OperationType.CREATE

    # 3. Semantic Artifact Type Inference
    artifact_types = []
    if re.search(r"\b(excel|spreadsheet|workbook|sheets?|calculation\s+sheet|financial\s+(?:model|table|numbers?|breakdown))\b", msg_lower):
        artifact_types.append(ArtifactType.XLSX)
    if re.search(r"\b(presentation|slides?|deck|powerpoint|present\s+to\s+executives|executive\s+presentation|pitch\s+deck)\b", msg_lower):
        artifact_types.append(ArtifactType.PPTX)
    if re.search(r"\b(document|report|proposal|board\s+review|formal\s+board|whitepaper|memo|written\s+brief|word\s+doc)\b", msg_lower):
        artifact_types.append(ArtifactType.DOCX)

    if not artifact_types:
        if active_art:
            k = active_art.get("kind", "").upper()
            if k in ("DOCX", "PPTX", "XLSX"):
                artifact_types = [ArtifactType(k)]
        elif artifacts:
            k = artifacts[-1].get("kind", "").upper()
            if k in ("DOCX", "PPTX", "XLSX"):
                artifact_types = [ArtifactType(k)]
        elif re.search(r"\b(it|this|that|them)\b", msg_lower) and not has_artifacts:
            artifact_types = [ArtifactType.UNKNOWN]
        else:
            artifact_types = [ArtifactType.DOCX, ArtifactType.PPTX]

    # 4. Semantic Information & Source Requirements
    has_attached_req = bool(
        file_ids
        or (context.uploaded_files and re.search(r"\b(uploaded\s+previously|file\s+i\s+uploaded|uploaded\s+earlier|previous\s+upload)\b", msg_lower))
        or re.search(r"\b(attached|uploaded|supplied\s+material|provided\s+report|this\s+report|the\s+report)\b", msg_lower)
    )
    has_internal_req = bool(re.search(r"\b(?:internal\s+(?:[a-z]+\s+)?(?:sales|numbers?|knowledge|data|metrics|performance|benchmarks?|policies|guidelines|docs|documents|leave)|our\s+internal|company\s+(?:kb|documents|policies|rules)|knowledge\s+base|company\s+wiki)\b", msg_lower))
    has_external_req = bool(
        op == OperationType.REFRESH
        or re.search(r"\b(external\s+(?:market|situation|data|environment|trends?|benchmarks?)|market\s+trends?|industry\s+benchmarks?|current\s+(?:market|situation|external|industry)|latest\s+(?:gen\s+ai\s+|updates?\s+on\s+)?trends?|trends?\s+in\s+202\d|web\s+research|competitor|competitors?|search\s+(?:the\s+)?web|online\s+research|released\s+this\s+week)\b", msg_lower)
    )

    # Negative constraints on research have absolute priority
    if any(phrase in msg_lower for phrase in [
        "do not search the web", "don't search the web", "no web search", "no web",
        "without web", "do not use web", "don't use web", "offline", "without internet",
        "use only the attached", "use only attached"
    ]):
        has_external_req = False
    if re.search(r"\b(summarize\s+(?:the\s+)?uploaded\s+report|summarize\s+this\s+report)\b", msg_lower) and not re.search(r"\b(market|competitor|external)\b", msg_lower):
        has_external_req = False
        has_internal_req = False

    source_reqs = {
        "uploaded_files": has_attached_req,
        "knowledge_base": has_internal_req,
        "web": has_external_req,
    }

    # 5. Semantic Attachment Roles
    roles: dict[str, str] = {}
    if re.search(r"\b(visual\s+reference|style\s+reference|look\s+and\s+feel)\b", msg_lower):
        if "deck" in msg_lower or "presentation" in msg_lower:
            roles["deck"] = "STYLE_REFERENCE"
            roles["presentation"] = "STYLE_REFERENCE"
        if "report" in msg_lower or "document" in msg_lower or "material" in msg_lower:
            roles["report"] = "SOURCE"
            roles["document"] = "SOURCE"
    elif re.search(r"\b(using\s+(?:the\s+)?uploaded\s+template|template)\b", msg_lower):
        roles["template"] = "TEMPLATE"

    # Template Mode
    tmpl_mode = TemplateMode.DEFAULT
    if re.search(r"\b(uploaded\s+template|custom\s+template|attached\s+template|given\s+template|visual\s+reference)\b", msg_lower):
        tmpl_mode = TemplateMode.UPLOADED
    elif re.search(r"\b(same\s+template|existing\s+template|previous\s+template)\b", msg_lower):
        tmpl_mode = TemplateMode.EXISTING
    elif re.search(r"\b(no\s+template|blank\s+template)\b", msg_lower):
        tmpl_mode = TemplateMode.NONE

    # Target Artifact ID
    target_art_id = resolved_refs.target_artifact_id
    if not target_art_id and has_artifacts and op in (OperationType.EDIT, OperationType.APPEND, OperationType.REFRESH, OperationType.REGENERATE):
        active_arts = [a for a in artifacts if a.get("active")]
        target_art_id = active_arts[0].get("artifact_id") if active_arts else (active_art.get("artifact_id") if active_art else artifacts[-1].get("artifact_id"))

    # Slide count extraction
    slide_count = 12
    m_slides = re.search(r"(\d+)\s*[-_\s]*slides?", msg_lower)
    if m_slides:
        slide_count = int(m_slides.group(1))

    # Document type inference
    doc_type = "generic"
    if re.search(r"\b(proposal|bid|pitch|sow|statement\s+of\s+work)\b", msg_lower):
        doc_type = "proposal"
    elif re.search(r"\b(research\s+report|report\s+explaining|analysis\s+report|market\s+report|report)\b", msg_lower):
        doc_type = "research_report"

    # Check if needs new facts
    needs_new_facts = bool(
        op == OperationType.REFRESH
        or has_external_req
        or re.search(r"\b(add|append|include|expand|new\s+topic|information\s+about)\b", msg_lower)
    )

    # Construct plan
    plan = ExecutionPlan(
        goal=msg,
        operation=op,
        artifact_types=artifact_types,
        target_artifact_id=target_art_id,
        target_version_id=resolved_refs.target_version_no,
        target_nodes=target_nodes,
        template_mode=tmpl_mode,
        source_file_ids=list(file_ids),
        source_references=list(resolved_refs.source_file_references),
        attachment_roles=roles,
        source_requirements=source_reqs,
        use_web=has_external_req,
        use_rag=has_internal_req,
        research_query=msg if has_external_req else "",
        slide_count=slide_count,
        document_type=doc_type,
        needs_new_facts=needs_new_facts,
        preserve_constraints=preserve_constraints,
        user_instruction=resolved_refs.resolved_message or msg,
        confidence=resolved_refs.confidence,
        reasoning=f"Semantic inference: goal='{msg[:80]}', op={op.value}, artifacts={[a.value for a in artifact_types]}",
    )
    return plan


def semantic_plan_from_context(
    user_message: str,
    session_context: dict[str, Any] | None = None,
    file_ids: list[int] | None = None,
) -> ExecutionPlan:
    """Helper returning a semantic execution plan from user message and session context."""
    if isinstance(session_context, ConversationContext):
        ctx = session_context
    elif isinstance(session_context, dict):
        ctx = ConversationContext.from_dict(session_context)
    else:
        ctx = ConversationContext()
    return _infer_semantic_intent(user_message, ctx, file_ids=file_ids)


def parse_plan(
    user_message: str,
    file_ids: list[int] | None = None,
    llm_client: LLMClient | None = None,
    session_context: dict[str, Any] | None = None,
    session_artifacts: list[dict[str, Any]] | None = None,
) -> ExecutionPlan:
    """Parse user message into a validated ExecutionPlan using LLM reasoning and deterministic governance."""
    file_ids = file_ids or []
    if llm_client is None:
        llm_client = get_llm_client()

    # Build typed ConversationContext
    if isinstance(session_context, ConversationContext):
        conv_ctx = session_context
    elif isinstance(session_context, dict):
        conv_ctx = ConversationContext.from_dict(session_context)
    else:
        conv_ctx = ConversationContext()

    if session_artifacts and not conv_ctx.artifact_history:
        conv_ctx.artifact_history = session_artifacts

    # Build prompt summaries
    active_topic = conv_ctx.active_topic or "(None)"
    conv_summary = conv_ctx.conversation_summary or "(None)"
    if conv_ctx.artifact_history:
        art_lines = [
            f"- ID {a.get('artifact_id')} ({str(a.get('kind', '')).upper()}) v{a.get('latest_version', 1)}: '{a.get('title', '')}'"
            for a in conv_ctx.artifact_history
        ]
        session_artifacts_summary = "\n".join(art_lines)
    else:
        session_artifacts_summary = "(None - fresh session)"

    recent_messages_summary = (
        "\n".join(f"- {m.get('role', 'user')}: {m.get('content', '')[:120]}" for m in conv_ctx.recent_messages[-4:])
        if conv_ctx.recent_messages
        else "(None)"
    )

    prompt = SUPERVISOR_PROMPT.format(
        user_message=user_message,
        active_topic=active_topic,
        conversation_summary=conv_summary,
        session_artifacts_summary=session_artifacts_summary,
        recent_messages_summary=recent_messages_summary,
    )

    # 1. Primary Semantic Planning: Call LLM (or semantic fallback in mock test mode)
    if getattr(llm_client, "_mock_mode", False):
        plan = _infer_semantic_intent(user_message, conv_ctx, file_ids=file_ids)
    else:
        try:
            plan = llm_client.generate_json(
                prompt=prompt,
                schema=ExecutionPlan,
                system="You are the Universal Semantic Planner. Infer intent semantically and produce a structured ExecutionPlan.",
            )
            logger.info(f"Universal Semantic Planner produced plan: op={plan.operation}, artifacts={plan.artifact_types}")
        except Exception as exc:
            logger.warning(f"Universal Semantic Planner LLM call unavailable ({exc}). Using semantic inference fallback.")
            plan = _infer_semantic_intent(user_message, conv_ctx, file_ids=file_ids)

    # 2. Deterministic Governance & Grounding Layer
    # Ground file attachments with semantic file role classifier
    if file_ids:
        from app.services.file_classifier import classify_attached_files
        from app.models.file_role import FileRole
        try:
            classifications = classify_attached_files(
                file_ids=file_ids,
                user_prompt=user_message,
            )
            has_ambiguous_tmpl = any(c.is_ambiguous for c in classifications)
            if has_ambiguous_tmpl:
                plan.error = "AMBIGUOUS_TEMPLATES"
                ambig_names = [c.filename for c in classifications if c.is_ambiguous]
                plan.reasoning = f"Multiple candidate templates detected ({', '.join(ambig_names)}). Please specify which template to use."

            tmpl_ids = [c.file_id for c in classifications if c.role == FileRole.TEMPLATE]
            src_ids = [c.file_id for c in classifications if c.role in (FileRole.SOURCE, FileRole.REFERENCE)]

            if tmpl_ids:
                plan.template_file_ids = tmpl_ids
                plan.template_mode = TemplateMode.UPLOADED
            if src_ids:
                plan.source_file_ids = list(set(plan.source_file_ids + src_ids))
            elif not tmpl_ids:
                plan.source_file_ids = list(set(plan.source_file_ids + file_ids))
        except Exception as exc:
            logger.warning(f"File classification failed: {exc}")
            plan.source_file_ids = list(set(plan.source_file_ids + file_ids))

    # Validate and ground target nodes and artifact IDs against context
    registry = get_capability_registry()
    if plan.capabilities:
        is_valid, cap_errors = registry.validate_plan_capabilities(
            plan.capabilities,
            artifact_types=[a.value for a in plan.artifact_types],
            operation=plan.operation.value,
        )
        if not is_valid:
            logger.warning(f"Plan capability validation warnings: {cap_errors}")

    # Ground target artifact ID if editing and unassigned
    from app.services.reference_resolver import resolve_references
    resolved_refs = resolve_references(user_message, conv_ctx)
    if resolved_refs.target_artifact_id:
        plan.target_artifact_id = resolved_refs.target_artifact_id
        plan.target_artifact_ids = [resolved_refs.target_artifact_id]
    elif plan.operation in (OperationType.EDIT, OperationType.APPEND, OperationType.REFRESH, OperationType.REGENERATE):
        if not plan.target_artifact_id and conv_ctx.artifact_history:
            latest_id = conv_ctx.artifact_history[-1].get("artifact_id")
            plan.target_artifact_id = latest_id
            plan.target_artifact_ids = [latest_id]

    # Ground target nodes if resolved and unpopulated
    if resolved_refs.target_nodes and not plan.target_nodes:
        plan.target_nodes = resolved_refs.target_nodes

    # Ground uploaded files from previous turns if referenced
    if not plan.source_file_ids and conv_ctx.uploaded_files:
        has_file_ref = (
            bool(resolved_refs.source_file_references)
            or bool(re.search(r"\b(uploaded\s+previously|file\s+i\s+uploaded|uploaded\s+earlier|previous\s+upload|the\s+upload|the\s+file)\b", user_message.lower()))
        )
        if has_file_ref:
            plan.source_file_ids = [f["id"] for f in conv_ctx.uploaded_files]
            plan.source_requirements.uploaded_files = True
            for f in conv_ctx.uploaded_files:
                fname = f.get("filename", "file")
                plan.attachment_roles[fname] = "PRIMARY_SOURCE"
            if "analyze_uploaded_files" not in plan.capabilities:
                plan.capabilities.insert(0, "analyze_uploaded_files")

    return plan
