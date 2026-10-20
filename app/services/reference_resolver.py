"""Lightweight conversation reference resolution service for Part 1."""

from __future__ import annotations

import re
from typing import Any
from pydantic import BaseModel, Field

from app.models.context import ConversationContext
from app.models.execution_plan import ArtifactType, TemplateMode


class ResolvedReferences(BaseModel):
    """Output of conversation reference resolution."""

    target_artifact_id: int | None = Field(
        default=None, description="Resolved target artifact ID from context."
    )
    target_artifact_type: ArtifactType | None = Field(
        default=None, description="Resolved artifact type from context."
    )
    target_version_no: int | None = Field(
        default=None, description="Resolved version number if version was explicitly referenced."
    )
    target_nodes: list[str] = Field(
        default_factory=list, description="Resolved target nodes (e.g. ['slide 5'], ['executive summary'], ['end'])."
    )
    template_mode: TemplateMode | None = Field(
        default=None, description="Resolved template mode from references."
    )
    source_file_references: list[str] = Field(
        default_factory=list, description="Extracted uploaded file references (e.g. ['uploaded PDF'])."
    )
    resolved_message: str = Field(
        default="", description="Instruction rewritten with pronouns/references resolved."
    )
    unresolved_references: list[str] = Field(
        default_factory=list, description="Ambiguous pronouns or references that could not be resolved."
    )
    confidence: float = Field(
        default=1.0, description="Resolution confidence score between 0.0 and 1.0."
    )
    reasoning: list[str] = Field(
        default_factory=list, description="Trace of resolution steps."
    )


PRONOUN_PATTERN = re.compile(r"\b(it|this|that|them|these|those)\b", re.IGNORECASE)
OBJECT_PRONOUN_PATTERN = re.compile(r"\b(it|them)\b", re.IGNORECASE)
DOC_REFERENCE_PATTERN = re.compile(
    r"\b(?:the\s+(?:first\s+|second\s+|third\s+|initial\s+|prior\s+|previous\s+|last\s+|same\s+)?(?:document|report|proposal|word\s+doc|word\s+document)|same\s+(?:document|report|proposal|word\s+doc)|first\s+(?:document|report|proposal)|second\s+(?:document|report|proposal)|initial\s+(?:document|report|proposal)|this\s+(?:document|report|proposal))\b",
    re.IGNORECASE,
)
DECK_REFERENCE_PATTERN = re.compile(
    r"\b(?:the\s+(?:first\s+|second\s+|third\s+|initial\s+|prior\s+|previous\s+|last\s+|same\s+)?(?:presentation|deck|slides?|slideshow|powerpoint)|same\s+(?:presentation|deck|slideshow)|first\s+(?:presentation|deck|slides?)|second\s+(?:presentation|deck|slides?)|initial\s+(?:presentation|deck|slides?)|this\s+(?:presentation|deck))\b",
    re.IGNORECASE,
)
SHEET_REFERENCE_PATTERN = re.compile(
    r"\b(?:the\s+(?:first\s+|second\s+|third\s+|initial\s+|prior\s+|previous\s+|last\s+|same\s+)?(?:spreadsheet|workbook|excel|sheet|table)|same\s+(?:spreadsheet|workbook|sheet)|first\s+(?:spreadsheet|workbook|sheet)|second\s+(?:spreadsheet|workbook|sheet)|initial\s+(?:spreadsheet|workbook|sheet)|this\s+(?:spreadsheet|workbook|sheet))\b",
    re.IGNORECASE,
)
PREVIOUS_VERSION_PATTERN = re.compile(
    r"\b(previous\s+version|last\s+version|prior\s+version|v(?:\s*)(\d+))\b", re.IGNORECASE
)
LATEST_VERSION_PATTERN = re.compile(
    r"\b(latest\s+version|current\s+version|newest\s+version)\b", re.IGNORECASE
)
SAME_TEMPLATE_PATTERN = re.compile(
    r"\b(same\s+template|existing\s+template|previously\s+used\s+template|original\s+template)\b", re.IGNORECASE
)
UPLOADED_TEMPLATE_PATTERN = re.compile(
    r"\b(uploaded\s+template|company\s+template|custom\s+template|my\s+template|"
    r"attached\s+template|given\s+template|in\s+(?:the\s+)?(?:given\s+)?template\s+format|"
    r"using\s+(?:the\s+)?(?:given\s+|attached\s+)?template|use\s+(?:the\s+)?(?:given\s+|attached\s+)?template|"
    r"template\s+format)\b",
    re.IGNORECASE,
)
UPLOADED_SOURCE_PATTERN = re.compile(
    r"\b(?:uploaded\s+pdf|attached\s+pdf|uploaded\s+docx?|uploaded\s+pptx?|uploaded\s+xlsx?|"
    r"uploaded\s+file|attached\s+file|attached\s+files|attached\s+report|uploaded\s+document|"
    r"attached\s+document|attached\s+documents|the\s+pdf|this\s+file|attached\s+information|"
    r"attached\s+research|research\s+documents?|research\s+document|"
    r"(?:the\s+)?file\s+(?:I\s+)?uploaded(?:\s+previously|\s+earlier)?|"
    r"(?:the\s+)?report\s+(?:I\s+)?uploaded(?:\s+previously|\s+earlier)?|"
    r"uploaded\s+previously|uploaded\s+earlier)\b",
    re.IGNORECASE,
)
ORDINAL_MAP = {
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
    "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
}
SLIDE_NODE_PATTERN = re.compile(
    r"\b((?:slide|page)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)|"
    r"(\d+)(?:st|nd|rd|th)\s+(?:slide|page)|"
    r"(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+(?:slide|page)|"
    r"(?:the\s+)?(previous|prior|penultimate|last|final)\s+(?:slide|page))\b",
    re.IGNORECASE,
)
SECTION_NODE_PATTERN = re.compile(
    r"\b(executive\s+summary|introduction|conclusion|methodology|solution|"
    r"timeline|commercials|pricing|background|overview|recommendations?|"
    r"financial\s+section|financials?|market\s+section|pricing\s+section|"
    r"end\s+of\s+(?:the\s+)?document)\b",
    re.IGNORECASE,
)


def resolve_references(
    user_message: str,
    context: ConversationContext | dict[str, Any] | None = None,
) -> ResolvedReferences:
    """Resolve anaphoric and conversational references using structured context."""
    if isinstance(context, dict):
        ctx = ConversationContext.from_dict(context)
    elif isinstance(context, ConversationContext):
        ctx = context
    else:
        ctx = ConversationContext()

    res = ResolvedReferences()
    reasoning: list[str] = []
    msg_lower = user_message.lower()

    # 1. Extract Target Nodes (e.g. "slide 5", "previous slide", "executive summary", "conclusion", "end")
    slide_matches = SLIDE_NODE_PATTERN.findall(user_message)
    for sm in slide_matches:
        raw_match = sm[0].lower()
        res.target_nodes.append(raw_match)
        # Normalize ordinals (e.g. "fifth page" -> "page 5")
        norm = raw_match
        for ord_word, num in ORDINAL_MAP.items():
            if ord_word in norm:
                norm = norm.replace(ord_word, num).strip()
        norm = re.sub(r"(\d+)(?:st|nd|rd|th)\s+(slide|page)", r"\2 \1", norm).strip()
        norm = re.sub(r"(\d+)\s+(slide|page)", r"\2 \1", norm).strip()
        if norm not in res.target_nodes:
            res.target_nodes.append(norm)
        if "page" in norm:
            alt = norm.replace("page", "slide")
            if alt not in res.target_nodes:
                res.target_nodes.append(alt)
        elif "slide" in norm:
            alt = norm.replace("slide", "page")
            if alt not in res.target_nodes:
                res.target_nodes.append(alt)
        reasoning.append(f"Target node identified: '{sm[0]}'")

    # Relative slide resolution (e.g. "previous slide" / "prior slide")
    for raw_tn in list(res.target_nodes):
        if "previous slide" in raw_tn or "prior slide" in raw_tn or "the previous slide" in raw_tn:
            prev_idx = None
            if ctx.last_target_nodes:
                for ltn in ctx.last_target_nodes:
                    m = re.search(r"\b(\d+)\b", str(ltn))
                    if m:
                        val = int(m.group(1))
                        if val > 1:
                            prev_idx = val - 1
                            break
            if prev_idx is None and ctx.active_artifact and ctx.active_artifact.get("outline"):
                prev_idx = max(1, len(ctx.active_artifact["outline"]) - 1)
            if prev_idx is not None:
                s_label = f"slide {prev_idx}"
                s_node = f"slide_{prev_idx:03d}"
                if s_label not in res.target_nodes:
                    res.target_nodes.append(s_label)
                if s_node not in res.target_nodes:
                    res.target_nodes.append(s_node)
                reasoning.append(f"Resolved '{raw_tn}' to slide {prev_idx} based on conversation history.")

    sec_matches = SECTION_NODE_PATTERN.findall(user_message)
    for sc in sec_matches:
        clean_node = sc.lower()
        if "end of" in clean_node:
            clean_node = "end"
        if clean_node not in res.target_nodes:
            res.target_nodes.append(clean_node)
            reasoning.append(f"Target node identified: '{clean_node}'")

    if "append" in msg_lower or "to the end" in msg_lower or "at the end" in msg_lower:
        if "end" not in res.target_nodes:
            res.target_nodes.append("end")
            reasoning.append("Append position identified: 'end'")

    # 2. Extract Template References
    if SAME_TEMPLATE_PATTERN.search(user_message):
        res.template_mode = TemplateMode.EXISTING
        reasoning.append("Template reference resolved to EXISTING template.")
    elif UPLOADED_TEMPLATE_PATTERN.search(user_message):
        res.template_mode = TemplateMode.UPLOADED
        reasoning.append("Template reference resolved to UPLOADED template.")
    elif "default template" in msg_lower or "default presentation" in msg_lower:
        res.template_mode = TemplateMode.DEFAULT
        reasoning.append("Template reference resolved to DEFAULT template.")

    # 3. Extract Uploaded Source References
    source_matches = UPLOADED_SOURCE_PATTERN.findall(user_message)
    for sm in source_matches:
        if sm.lower() not in res.source_file_references:
            res.source_file_references.append(sm.lower())
            reasoning.append(f"Source file reference identified: '{sm}'")

    # 4. Extract Version References
    prev_ver_match = PREVIOUS_VERSION_PATTERN.search(user_message)
    if prev_ver_match:
        explicit_v = prev_ver_match.group(2)
        if explicit_v:
            res.target_version_no = int(explicit_v)
            reasoning.append(f"Target version resolved to v{explicit_v}")
        elif ctx.active_version and ctx.active_version > 1:
            res.target_version_no = ctx.active_version - 1
            reasoning.append(f"Target version resolved to previous v{res.target_version_no}")
        else:
            reasoning.append("Referenced previous version but active_version is 1 or unknown.")

    if LATEST_VERSION_PATTERN.search(user_message) and ctx.active_version:
        res.target_version_no = ctx.active_version
        reasoning.append(f"Target version resolved to latest v{ctx.active_version}")

    # 5. Resolve Target Artifact and Pronouns
    # Retrieve available artifacts in session
    artifacts = ctx.artifact_history
    active_art = ctx.active_artifact
    if not active_art and artifacts:
        active_arts = [a for a in artifacts if a.get("active")]
        active_art = active_arts[0] if active_arts else artifacts[-1]

    # Explicit format mentions in message
    has_doc_ref = bool(DOC_REFERENCE_PATTERN.search(user_message))
    has_deck_ref = bool(DECK_REFERENCE_PATTERN.search(user_message))
    has_sheet_ref = bool(SHEET_REFERENCE_PATTERN.search(user_message))
    has_pronoun = bool(PRONOUN_PATTERN.search(user_message))

    resolved_art = None

    if has_deck_ref or any(n.startswith("slide") for n in res.target_nodes):
        pptx_candidates = [a for a in artifacts if a.get("kind") == "pptx"]
        if pptx_candidates:
            resolved_art = pptx_candidates[-1]
            res.target_artifact_type = ArtifactType.PPTX
            reasoning.append(f"Target resolved to PPTX artifact: ID {resolved_art.get('artifact_id')}")
        else:
            res.target_artifact_type = ArtifactType.PPTX

    elif has_doc_ref:
        docx_candidates = [a for a in artifacts if a.get("kind") == "docx"]
        if docx_candidates:
            if "first" in msg_lower or "initial" in msg_lower:
                resolved_art = docx_candidates[0]
            else:
                resolved_art = docx_candidates[-1]
            res.target_artifact_type = ArtifactType.DOCX
            reasoning.append(f"Target resolved to DOCX artifact: ID {resolved_art.get('artifact_id')}")
        else:
            res.target_artifact_type = ArtifactType.DOCX

    elif has_sheet_ref:
        xlsx_candidates = [a for a in artifacts if a.get("kind") == "xlsx"]
        if xlsx_candidates:
            resolved_art = xlsx_candidates[-1]
            res.target_artifact_type = ArtifactType.XLSX
            reasoning.append(f"Target resolved to XLSX artifact: ID {resolved_art.get('artifact_id')}")
        else:
            res.target_artifact_type = ArtifactType.XLSX

    elif has_pronoun or "make shorter" in msg_lower or "make more concise" in msg_lower or "make it" in msg_lower:
        # Pronoun reference resolution using active artifact
        if active_art:
            resolved_art = active_art
            kind = active_art.get("kind", "").upper()
            if kind == "PPTX":
                res.target_artifact_type = ArtifactType.PPTX
            elif kind == "DOCX":
                res.target_artifact_type = ArtifactType.DOCX
            elif kind == "XLSX":
                res.target_artifact_type = ArtifactType.XLSX
            reasoning.append(f"Pronoun resolved to active artifact: ID {active_art.get('artifact_id')} ({kind})")
        else:
            res.unresolved_references.append("pronoun")
            res.confidence = 0.4
            reasoning.append("Pronoun could not be resolved because no active artifact exists in session.")
    elif active_art:
        resolved_art = active_art
        kind = active_art.get("kind", "").upper()
        if kind == "PPTX":
            res.target_artifact_type = ArtifactType.PPTX
        elif kind == "DOCX":
            res.target_artifact_type = ArtifactType.DOCX
        elif kind == "XLSX":
            res.target_artifact_type = ArtifactType.XLSX
        reasoning.append(f"Target implicitly resolved to active artifact: ID {active_art.get('artifact_id')} ({kind})")

    if resolved_art:
        res.target_artifact_id = resolved_art.get("artifact_id")

    # 6. Build resolved_message (rewritten instruction)
    # Only object pronouns name the file ("make it shorter"); "this"/"that" are usually determiners
    # ("use this template", "on this topic") and rewriting them changes the meaning.
    resolved_msg = user_message
    if has_pronoun and resolved_art:
        noun = {"pptx": "the presentation", "docx": "the document", "xlsx": "the workbook"}.get(resolved_art.get("kind", ""))
        if noun:
            resolved_msg = OBJECT_PRONOUN_PATTERN.sub(noun, resolved_msg, count=1)
    res.resolved_message = resolved_msg
    res.reasoning = reasoning

    return res
