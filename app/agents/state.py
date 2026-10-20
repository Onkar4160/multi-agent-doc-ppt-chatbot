"""GraphState definition for the LangGraph multi-agent pipeline."""

from __future__ import annotations

from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    """Shared state container passed between LangGraph nodes."""

    run_id: str
    started_at: float  # time.monotonic() when the run began (time budget for optional rounds)
    session_id: str | None
    user_message: str
    file_ids: list[int]
    plan: dict[str, Any] | None
    execution_plan: dict[str, Any] | None
    template_profiles: dict[str, Any]
    registry: dict[str, Any] | None
    findings: list[dict[str, Any]]
    doc_model: dict[str, Any] | None
    deck_model: dict[str, Any] | None
    workbook_model: dict[str, Any] | None
    current_capability: str | None
    executed_capabilities: list[str]
    capability_results: dict[str, Any]
    artifacts: list[dict[str, Any]]
    validation: dict[str, Any] | None
    retry_count: int
    reply: str
    errors: list[str]
    notes: list[str]  # non-error remarks for the reply (e.g. a template that did not fit)
    failure: str  # plain-language reply for the first failed step
    clarification_for: str  # request waiting for the user's answer to a clarification question
    kind_hint: str | None  # "retry" | "clarification_answer" decided by the chat API before the graph runs
    pending_clarification: str | None  # the request the user is answering
    raw_message: str  # the user's own words (user_message may be a merged or retried request)
    subject: str  # what the deliverable is about, resolved from the whole conversation (conversation_memory)
    interpretation: dict[str, Any] | None  # Interpretation (app/models/interpretation.py)
    task_id: int | None
    dossier: dict[str, Any] | None  # research summary: sources, coverage, conflicts, gaps (Stage 5)
    dossier_id: int | None
    research_summary: str
    session_context: dict[str, Any] | None
    evidence_pack: list[dict[str, Any]] | None
    claims: list[dict[str, Any]]  # claim digest planners and writers cite (Stage 7, services/plan_inputs.py)
    limitations: list[str]  # gaps and conflicts writers must state honestly
    questions: dict[str, str]  # research question id -> question
    content_plans: dict[str, Any]  # {'pptx': DeckPlan, 'docx': DocumentPlan, 'xlsx': WorkbookPlan} as dicts
    plan_reports: dict[str, Any]  # PlanReport per format: plan validation, revision, repairs, coverage
