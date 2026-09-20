"""Shared agent state definition for the LangGraph supervisor graph."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """State shared across all agent nodes in the supervisor graph."""

    # ── Conversation ─────────────────────────────────────
    messages: Annotated[list[BaseMessage], add_messages]

    # ── User request context ─────────────────────────────
    user_request: str
    project_id: int
    output_type: str  # "docx" | "pptx"

    # ── Template ─────────────────────────────────────────
    template_profile: dict[str, Any] | None
    template_path: str | None

    # ── Agent results ────────────────────────────────────
    research_results: list[dict[str, Any]]
    kb_results: list[dict[str, Any]]
    document_model: dict[str, Any] | None
    edit_instructions: list[dict[str, Any]] | None
    sources: list[dict[str, Any]]

    # ── Versioning ───────────────────────────────────────
    artifact_version_id: int | None

    # ── Routing ──────────────────────────────────────────
    next_step: str

    # ── Tracing ──────────────────────────────────────────
    trace_id: str
