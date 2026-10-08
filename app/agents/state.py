"""GraphState definition for the LangGraph multi-agent pipeline."""

from __future__ import annotations

from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    """Shared state container passed between LangGraph nodes."""

    run_id: str
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
    session_context: dict[str, Any] | None
    evidence_pack: list[dict[str, Any]] | None
