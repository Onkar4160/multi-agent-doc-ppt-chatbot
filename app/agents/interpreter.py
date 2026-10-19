"""Interpreter: what the user wants from this message, given the conversation (PRD FR-CONV-2…7, Stage 4).

Builds a typed ``Interpretation`` from the message, the planner's ``ExecutionPlan`` (LLM or offline inference),
the conversation context and the active artifact's content model. It adds no LLM call: the planner already
reasoned about the message; this layer classifies the request kind, extracts the output specification,
represents the requested change with resolved targets, and applies the clarification policy (ADR-013).
"""

from __future__ import annotations

import re
from typing import Any

from app.models.context import ConversationContext
from app.models.deck_model import DeckModel
from app.models.document_model import DocumentModel, TableBlock
from app.models.execution_plan import ExecutionPlan, OperationType
from app.models.interpretation import (
    ChangeRequest,
    ChangeScope,
    ClarificationQuestion,
    Interpretation,
    LengthSpec,
    OutputSpec,
    ResearchNeed,
    StyleChange,
    TargetRef,
)
from app.models.workbook_model import WorkbookModel
from app.services.artifact_nodes import ensure_node_ids

RETRY = re.compile(
    r"^\W*(please\s+)?(try\s+(it\s+|that\s+)?again|retry|do\s+it\s+again|one\s+more\s+time)(\s+please)?\W*$", re.I
)
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
              "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
             "ninth": 9, "tenth": 10}
_FORMAT_WORDS = (
    ("pptx", r"\b(deck|slides?|presentation|powerpoint|pptx|pitch\s+deck)\b"),
    ("xlsx", r"\b(excel|spreadsheet|workbook|xlsx|(?:revenue|financial|cost|pricing|break-?even)\s+model|model\s+the)\b"),
    ("docx", r"\b(report|document|word|docx|proposal|whitepaper|white\s+paper|memo|brief|article|paper|handout|essay|summary)\b"),
)
_LANGUAGES = {"hindi": "hi", "french": "fr", "german": "de", "spanish": "es", "italian": "it", "portuguese": "pt",
              "japanese": "ja", "chinese": "zh", "arabic": "ar", "marathi": "mr", "tamil": "ta", "dutch": "nl"}
_AUDIENCE = re.compile(
    r"\b(?:for|to)\s+(?:a\s+|an\s+|the\s+|our\s+|my\s+)?((?:non-technical\s+|technical\s+|senior\s+|executive\s+|new\s+)?"
    r"(?:board(?:\s+members)?|investors?|executives?|high\s+school\s+students|students|engineers|employees|staff|"
    r"hr\s+team|sales\s+team|customers|clients|managers|leadership(?:\s+team)?|developers|beginners|team|"
    r"stakeholders|partners|policy\s*makers|doctors|nurses|teachers|parents|homeowners|residents|citizens|volunteers|"
    r"members|patients|users|buyers|retailers|farmers|founders|analysts|researchers|journalists|children|kids|seniors|"
    r"the\s+public|general\s+public|government\s+officials))\b",
    re.I,
)
_TONE = re.compile(r"(?<!non-)\b(formal|casual|persuasive|technical|friendly|informal|professional|academic|conversational|playful)\b", re.I)
_BRIEF = re.compile(r"\b(short|brief|quick|concise|one-page|one\s+page|summary)\b", re.I)
_DETAILED = re.compile(r"\b(detailed|in-depth|in\s+depth|comprehensive|thorough|exhaustive)\b", re.I)
_CITATION = (("apa", r"\bapa\b"), ("harvard", r"\bharvard\b"), ("numeric", r"\b(numeric|ieee|numbered)\s+(citations?|references?)\b"),
             ("none", r"\b(no|without)\s+(citations?|references?|sources)\b"))
_CREATE = re.compile(r"\b(create|make|write|build|draft|prepare|put\s+together|generate|produce|give\s+me|model|i\s+need|i\s+want)\b", re.I)
_EDIT = re.compile(
    r"\b(change|edit|update|rewrite|re-write|delete|remove|get\s+rid\s+of|drop|add|insert|include|append|move|swap|"
    r"reorder|rename|replace|expand|elaborate|shorten|condense|trim|tighten|fix|improve|increase|decrease|make\s+(?:it|this|the|slide|section))\b",
    re.I,
)
_QUESTION_START = re.compile(r"^\s*(what|which|why|how|who|when|where|is|are|does|do|did|explain|tell\s+me|can\s+you\s+explain)\b", re.I)
_REGENERATE = re.compile(r"\b(regenerate|from\s+scratch|start\s+over|redo|re-do|rebuild|recreate|re-create|completely\s+new\s+structure)\b", re.I)
_ALSO = re.compile(r"\b(also|as\s+well|too|additionally|matching|same\s+(?:deck|report|presentation|numbers|content)|from\s+(?:this|the)\s+(?:deck|report|presentation|document|workbook|slides)|numbers\s+from\s+the)\b", re.I)
_CONVERT = re.compile(
    r"\b(?:turn|convert|transform|change|make)\b.*\b(?:into|to|out\s+of)\b|\binstead\b.*$|\bout\s+of\s+this\b", re.I
)
_OBJECTIVE_CHANGE = re.compile(
    r"\b(instead\s+of|change\s+direction|forget\s+(?:the|about)|rather\s+than|switch\s+(?:the\s+)?topic|should\s+be\s+about|"
    r"focus\s+on\s+.+\s+instead|new\s+topic|about\s+.+\s+instead)\b",
    re.I,
)
_VAGUE = re.compile(
    r"^\W*(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?(?:make\s+(?:it|this)\s+(?:better|nicer|good|great|look\s+better)|"
    r"improve\s+(?:it|this)|polish\s+(?:it|this)|fix\s+(?:it|this)|make\s+it\s+pop|enhance\s+(?:it|this))\W*(?:please)?\W*$",
    re.I,
)
_STYLE_WORDS = re.compile(r"\b(font|typeface|font\s+size|heading\s+size|point\s+size|\d+\s*pt|colou?r|bold|italic|line\s+spacing|margins?)\b", re.I)

BUDGET_FREE_DEPTH = {"question": "none", "retry": "none", "clarification_answer": "standard"}


# ── Output specification ──────────────────────────────────────────────────────


def _formats_in(text: str) -> list[str]:
    """Formats named in ``text``, in the order they are first mentioned ("a Word summary of the deck" → docx first)."""
    hits = []
    for fmt, pattern in _FORMAT_WORDS:
        m = re.search(pattern, text, re.I)
        if m:
            hits.append((m.start(), fmt))
    return [fmt for _, fmt in sorted(hits)]


def _number(token: str) -> int | None:
    token = token.lower()
    if token.isdigit():
        return int(token)
    return _NUM_WORDS.get(token)


def extract_length(text: str) -> LengthSpec:
    m = re.search(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty)[\s-]*"
                  r"(slides?|pages?|words?|sheets?|tabs?)\b", text, re.I)
    if not m:
        return LengthSpec()
    unit = m.group(2).lower().rstrip("s")
    unit = {"slide": "slides", "page": "pages", "word": "words", "sheet": "sheets", "tab": "sheets"}[unit]
    value = _number(m.group(1))
    return LengthSpec(unit=unit, value=value, explicit=True) if value else LengthSpec()


def extract_output_spec(text: str, default_format: str | None, assumptions: list[str]) -> OutputSpec | None:
    formats = _formats_in(text)
    fmt = formats[0] if formats else default_format
    if fmt is None:
        return None
    if not formats:
        assumptions.append(f"Format inferred as {fmt.upper()} (not stated in the message).")
    audience = _AUDIENCE.search(text)
    tone = _TONE.search(text)
    language = next((code for name, code in _LANGUAGES.items() if re.search(rf"\bin\s+{name}\b", text, re.I)), "en")
    citation = next((style for style, pattern in _CITATION if re.search(pattern, text, re.I)), "numeric")
    detail = "detailed" if _DETAILED.search(text) else "brief" if _BRIEF.search(text) else "standard"
    return OutputSpec(
        format=fmt,
        audience=re.sub(r"\s+", " ", audience.group(1).lower()) if audience else "",
        tone=tone.group(1).lower() if tone else "",
        language=language,
        detail_level=detail,
        length=extract_length(text),
        citation_style=citation,
    )


# ── Targets ───────────────────────────────────────────────────────────────────


def _model_kind(model: Any) -> str | None:
    if isinstance(model, DeckModel):
        return "pptx"
    if isinstance(model, DocumentModel):
        return "docx"
    if isinstance(model, WorkbookModel):
        return "xlsx"
    return None


def _slide_numbers(text: str, count: int) -> list[int]:
    found: list[int] = []
    for m in re.finditer(r"\bslides?\s+(\d+)((?:\s*(?:,|and|&)\s*\d+)*)", text, re.I):
        found.append(int(m.group(1)))
        found += [int(n) for n in re.findall(r"\d+", m.group(2) or "")]
    for m in re.finditer(r"\b(\d+)(?:st|nd|rd|th)\s+slide\b", text, re.I):
        found.append(int(m.group(1)))
    for word, n in _ORDINALS.items():
        if re.search(rf"\b{word}\s+slide\b", text, re.I):
            found.append(n)
    if re.search(r"\b(last|final)\s+slide\b", text, re.I) and count:
        found.append(count)
    return list(dict.fromkeys(found))


def resolve_targets(text: str, model: Any) -> tuple[list[TargetRef], list[str]]:
    """Targets named in ``text`` resolved against the active model; second value lists out-of-range references."""
    if model is None:
        return [], []
    ensure_node_ids(model)
    targets: list[TargetRef] = []
    missing: list[str] = []
    if isinstance(model, DeckModel):
        count = len(model.slides)
        for n in _slide_numbers(text, count):
            if 1 <= n <= count:
                targets.append(TargetRef(kind="slide", locator=f"slide {n}", node_id=model.slides[n - 1].node_id))
            else:
                missing.append(f"slide {n}")
    elif isinstance(model, DocumentModel):
        for m in re.finditer(r"\bsection\s+(\d+)\b", text, re.I):
            n = int(m.group(1))
            if 1 <= n <= len(model.sections):
                targets.append(TargetRef(kind="section", locator=f"section {n}", node_id=model.sections[n - 1].node_id))
            else:
                missing.append(f"section {n}")
        tables = [(sec, blk) for sec in model.sections for blk in sec.blocks if isinstance(blk, TableBlock)]
        named_table = re.search(r"\b([\w-]+)\s+table\b", text, re.I)
        table_hit = None
        if named_table:
            word = named_table.group(1).lower()
            table_hit = next((blk for sec, blk in tables if word in sec.heading.lower()), None)
            if table_hit is None and word in ("the", "this", "that") and len(tables) == 1:
                table_hit = tables[0][1]
        if table_hit is not None:
            targets.append(TargetRef(kind="table", locator=named_table.group(0), node_id=table_hit.node_id))
        for sec in model.sections:
            heading = sec.heading.lower()
            if table_hit is not None and any(blk is table_hit for blk in sec.blocks):
                continue  # "the costs table" names the table, not the whole Costs section
            if re.search(rf"\b{re.escape(heading)}\b", text, re.I):
                targets.append(TargetRef(kind="section", locator=sec.heading, node_id=sec.node_id))
    elif isinstance(model, WorkbookModel):
        for sheet in model.sheets:
            if re.search(rf"\b{re.escape(sheet.name)}\b", text, re.I):
                targets.append(TargetRef(kind="sheet", locator=sheet.name, node_id=sheet.node_id))
    unique = {t.node_id: t for t in targets}
    return list(unique.values()), missing


# ── Change requests ───────────────────────────────────────────────────────────


def parse_style_changes(text: str) -> list[StyleChange]:
    changes: list[StyleChange] = []
    heading = re.search(r"\b(?:heading|headings|title)s?\b[^.\d]*?(\d{1,2})\s*(?:pt|points?)?\b", text, re.I)
    if heading and re.search(r"\b(size|pt|points?|font)\b", text, re.I):
        changes.append(StyleChange(path="heading_1.size_pt", value=int(heading.group(1))))
    body = re.search(r"\b(?:body|text|paragraph)s?\b.*?\bfont\b.*?\bto\s+([A-Z][\w ]*?)(?:\s+(\d{1,2}))?(?:\s*pt)?\s*$"
                     r"|\bfont\s+of\s+the\s+body(?:\s+text)?\s+to\s+([A-Z][\w ]*?)(?:\s+(\d{1,2}))?(?:\s*pt)?\s*$", text, re.I)
    if body:
        name = (body.group(1) or body.group(3) or "").strip()
        size = body.group(2) or body.group(4)
        if name:
            changes.append(StyleChange(path="body.font", value=name))
        if size:
            changes.append(StyleChange(path="body.size_pt", value=int(size)))
    colour = re.search(r"\b(?:accent|theme|main)\s+colou?r\s+(?:to\s+)?(#[0-9a-f]{6}|[a-z]+)\b", text, re.I)
    if colour:
        changes.append(StyleChange(path="accent_color", value=colour.group(1)))
    return changes


def change_operation(text: str) -> str:
    if parse_style_changes(text) or (_STYLE_WORDS.search(text) and not re.search(r"\b(slide|section|sheet)\s+\d", text, re.I)):
        return "restyle"
    if re.search(r"\b(delete|remove|get\s+rid\s+of|drop)\b", text, re.I):
        return "remove"
    if re.search(r"\b(move|swap|reorder)\b", text, re.I):
        return "reorder"
    if re.search(r"\b(update|replace|refresh)\b.*\b(numbers|figures|data|values|statistics)\b", text, re.I):
        return "replace_data"
    if re.search(r"\b(add|insert|include|append)\b", text, re.I):
        return "add"
    if re.search(r"\b(shorter|shorten|concise|condense|trim|tighten|punchier|cut\s+down|fewer\s+words)\b", text, re.I):
        return "condense"
    if re.search(r"\b(expand|elaborate|more\s+detail|longer|deeper|flesh\s+out)\b", text, re.I):
        return "expand"
    return "rewrite"


# ── Classification ────────────────────────────────────────────────────────────


def classify(text: str, *, has_artifact: bool, active_kind: str | None, plan: ExecutionPlan | None,
             pending_clarification: str | None, is_retry: bool) -> tuple[str, str | None]:
    """Request kind plus, for conversions, the target format."""
    if is_retry or RETRY.match(text):
        return "retry", None
    if pending_clarification:
        return "clarification_answer", None
    ends_question = text.rstrip().endswith("?")
    if ends_question and _QUESTION_START.search(text):
        return "question", None  # "How do I write…?" asks about writing; it does not request a document
    deliverable = bool(_formats_in(text) or extract_length(text).explicit)  # "Explain X in 5 slides" asks for slides
    if (ends_question or (_QUESTION_START.search(text) and not deliverable)) and not (_CREATE.search(text) or _EDIT.search(text)):
        return "question", None
    if plan is not None and plan.operation == OperationType.ANSWER and not _EDIT.search(text) and not _CREATE.search(text):
        return "question", None
    if has_artifact:
        formats = [f for f in _formats_in(text) if f != active_kind]
        if formats and _ALSO.search(text) and _CREATE.search(text):
            return "continue_task", formats[0]
        if formats and _CONVERT.search(text):
            return "regenerate", formats[0]
        if _REGENERATE.search(text):
            return "regenerate", None
        if _OBJECTIVE_CHANGE.search(text):
            return "change_objective", None
        if _EDIT.search(text) or _VAGUE.match(text) or (plan is not None and plan.operation in (
                OperationType.EDIT, OperationType.APPEND, OperationType.INSERT, OperationType.DELETE,
                OperationType.MOVE, OperationType.RESTYLE, OperationType.REPLACE)):
            return "modify_artifact", None
        if _CREATE.search(text) and _formats_in(text):
            return "new_task", None
        return "modify_artifact", None
    names_a_part = re.search(r"\b(?:slide|section|sheet)\s+\d+\b|\b(?:make|change|fix|improve|shorten)\s+(?:it|this)\b", text, re.I)
    if _EDIT.search(text) and names_a_part:
        return "modify_artifact", None  # nothing to modify yet: clarified below
    return "new_task", None


# ── Entry point ───────────────────────────────────────────────────────────────


def interpret(
    message: str,
    *,
    plan: ExecutionPlan | None = None,
    context: ConversationContext | None = None,
    active_model: Any = None,
    pending_clarification: str | None = None,
    is_retry: bool = False,
) -> Interpretation:
    """Typed understanding of ``message`` in its conversation (ADR-013 clarification policy applied)."""
    text = message.strip()
    context = context or ConversationContext()
    active_kind = _model_kind(active_model) or ((context.active_artifact or {}).get("kind") or None)
    has_artifact = active_model is not None or bool(context.active_artifact or context.artifact_history)
    kind, convert_to = classify(text, has_artifact=has_artifact, active_kind=active_kind, plan=plan,
                                pending_clarification=pending_clarification, is_retry=is_retry)
    assumptions: list[str] = []
    questions: list[ClarificationQuestion] = []
    confidence = 0.9

    objective = text if kind not in ("clarification_answer",) else f"{pending_clarification} (meaning: {text})"
    spec: OutputSpec | None = None
    change: ChangeRequest | None = None
    targets, missing = resolve_targets(text, active_model)

    if kind in ("new_task", "continue_task", "change_objective", "clarification_answer"):
        plan_format = None
        if plan is not None and plan.artifact_types:
            first = str(plan.artifact_types[0].value).lower()
            plan_format = first if first in ("pptx", "docx", "xlsx") else None
        default = convert_to or plan_format or active_kind or "pptx"
        spec = extract_output_spec(objective, default, assumptions)
    elif kind == "regenerate":
        fmt = convert_to or active_kind or "pptx"
        spec = extract_output_spec(text, fmt, assumptions)
        if spec is not None and convert_to:
            spec.format = convert_to
        change = ChangeRequest(operation="convert" if convert_to else "regenerate",
                               scope=ChangeScope(level="artifact"), instruction=text)
    elif kind == "modify_artifact":
        op = change_operation(text)
        style_changes = parse_style_changes(text) if op == "restyle" else []
        if op == "restyle":
            scope = ChangeScope(level="style", targets=targets)
            if not style_changes:
                questions.append(ClarificationQuestion(
                    question="Which formatting should change, and to what?",
                    options=["Heading size (e.g. 20 pt)", "Body font (e.g. Calibri 11)", "Accent colour"],
                ))
                op = "rewrite"
        elif targets:
            level = "element" if all(t.kind in ("table", "chart", "paragraph") for t in targets) else "unit"
            scope = ChangeScope(level=level, targets=targets)
        else:
            scope = ChangeScope(level="artifact")
        change = ChangeRequest(operation=op if op != "restyle" or style_changes else "rewrite", scope=scope,
                               instruction=text, style_changes=style_changes)

    # ── Clarification policy (ADR-013) ──
    if plan is not None and plan.error == "CLARIFICATION_NEEDED" and plan.clarification_needed:
        questions.append(ClarificationQuestion(question=plan.clarification_needed))
    if kind == "modify_artifact" and not has_artifact:
        questions.append(ClarificationQuestion(
            question="There is no file in this chat yet to change. Should I create one first? Tell me what it should be about.",
        ))
    if missing:
        available = _unit_labels(active_model)
        questions.append(ClarificationQuestion(
            question=f"I can't find {', '.join(missing)} — the {active_kind or 'file'} has {len(available)} parts. Which one did you mean?",
            options=available[:10],
        ))
    if kind == "modify_artifact" and _VAGUE.match(text) and not targets:
        questions.append(ClarificationQuestion(
            question="What should I improve?",
            options=["Shorter, clearer text", "Stronger visuals and layout", "More evidence and figures", "A different tone"],
        ))
    if questions:
        confidence = 0.4
    research = _research_need(kind, text, change)
    return Interpretation(
        kind=kind,
        confidence=confidence,
        objective=objective,
        output_spec=spec,
        change=change,
        research=research,
        clarification_questions=questions,
        assumptions=assumptions,
    )


_ABOUT_ARTIFACT = re.compile(
    r"\b(did\s+you|you\s+used|your|this|the\s+(?:deck|report|document|presentation|workbook|file|draft|slides?|sections?|"
    r"sheets?|table|chart|figures?|numbers?|sources?|references?|citations?|conclusion|introduction|summary))\b|"
    r"\b(?:slide|section|sheet)\s+\d+\b",
    re.I,
)


def refers_to_artifact(text: str) -> bool:
    """A question about the file itself ("which sources did you use?") rather than about the world."""
    return bool(_ABOUT_ARTIFACT.search(text))


def _unit_labels(model: Any) -> list[str]:
    if isinstance(model, DeckModel):
        return [f"Slide {i}: {s.title}" for i, s in enumerate(model.slides, 1)]
    if isinstance(model, DocumentModel):
        return [f"Section {i}: {s.heading}" for i, s in enumerate(model.sections, 1)]
    if isinstance(model, WorkbookModel):
        return [f"Sheet: {s.name}" for s in model.sheets]
    return []


def _research_need(kind: str, text: str, change: ChangeRequest | None) -> ResearchNeed:
    if re.search(r"\b(do\s+not|don't|without|no)\s+(search|web|internet|research)\b|\boffline\b|\bonly\s+(the\s+)?attached\b", text, re.I):
        return ResearchNeed(depth="none", rationale="the user ruled out web research")
    if kind in ("question", "retry"):
        return ResearchNeed(depth="none", rationale="answered from the conversation and existing sources")
    if kind == "modify_artifact":
        if change is not None and change.operation in ("add", "expand", "replace_data"):
            return ResearchNeed(depth="light", rationale="new content may need supporting facts")
        return ResearchNeed(depth="none", rationale="edit of existing content")
    if kind == "regenerate":
        return ResearchNeed(depth="none", rationale="reuses the task's research")
    if _DETAILED.search(text) or re.search(r"\b(research|literature|evidence-based|white\s*paper)\b", text, re.I):
        return ResearchNeed(depth="deep", rationale="the deliverable asks for depth")
    if _BRIEF.search(text):
        return ResearchNeed(depth="light", rationale="a short deliverable")
    return ResearchNeed(depth="standard", rationale="new deliverable")


def interpretation_summary(interp: Interpretation) -> dict[str, Any]:
    """Compact form stored with runs and shown in traces."""
    data: dict[str, Any] = {"kind": interp.kind, "confidence": interp.confidence}
    if interp.output_spec:
        spec = interp.output_spec
        data["output_spec"] = {k: v for k, v in spec.model_dump().items() if v not in ("", None, [], "standard", "en", "numeric")}
    if interp.change:
        data["change"] = {"operation": interp.change.operation, "level": interp.change.scope.level,
                          "targets": [t.node_id or t.locator for t in interp.change.scope.targets],
                          "style_changes": [c.model_dump() for c in interp.change.style_changes]}
    if interp.clarification_questions:
        data["clarification"] = [q.question for q in interp.clarification_questions]
    if interp.assumptions:
        data["assumptions"] = interp.assumptions
    data["research"] = interp.research.depth
    return data


_REFERRING = re.compile(r"\b(him|her|them|it|his|its|their|this\s+(?:topic|subject|person|company)|that\s+(?:topic|subject|person|company)"
                        r"|the\s+same\s+(?:topic|subject)|same\s+topic)\b", re.I)
_POSSESSIVE = re.compile(r"\b(his|its|their)\b", re.I)


def resolve_subject(message: str, prior_topic: str) -> str | None:
    """'a detailed report on him' after a deck on Sachin Tendulkar -> 'a detailed report on Sachin Tendulkar'.

    Returns None when the message names no earlier subject (no referring word) or there is no prior topic.
    """
    from app.agents.planner import topic_of

    prior = (prior_topic or "").strip()
    if not prior or not _REFERRING.search(message):
        return None
    subject = prior.split(":")[0].strip() if ":" in prior else topic_of(prior)
    if not subject:
        return None
    text = _POSSESSIVE.sub(f"{subject}'s", message)
    return _REFERRING.sub(subject, text)
