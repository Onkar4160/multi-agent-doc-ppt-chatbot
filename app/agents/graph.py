"""Build and compile the LangGraph supervisor graph."""

from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph

from app.agents.editor import editor_node
from app.agents.generator import generator_node
from app.agents.kb_retriever import kb_retriever_node
from app.agents.researcher import researcher_node
from app.agents.state import AgentState
from app.agents.supervisor import supervisor_node

logger = logging.getLogger(__name__)


def _route_supervisor(state: AgentState) -> str:
    """Route from supervisor to the chosen next step."""
    next_step = state.get("next_step", "FINISH")
    if next_step == "FINISH":
        return END
    if next_step in ("researcher", "kb_retriever", "generator", "editor"):
        return next_step
    logger.warning("Unknown next_step '%s', finishing", next_step)
    return END


def build_graph() -> StateGraph:
    """Construct the multi-agent supervisor graph."""
    graph = StateGraph(AgentState)

    # ── Add nodes ────────────────────────────────────────
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("researcher", researcher_node)
    graph.add_node("kb_retriever", kb_retriever_node)
    graph.add_node("generator", generator_node)
    graph.add_node("editor", editor_node)

    # ── Entry point ──────────────────────────────────────
    graph.set_entry_point("supervisor")

    # ── Conditional routing from supervisor ──────────────
    graph.add_conditional_edges(
        "supervisor",
        _route_supervisor,
        {
            "researcher": "researcher",
            "kb_retriever": "kb_retriever",
            "generator": "generator",
            "editor": "editor",
            END: END,
        },
    )

    # ── All workers return to supervisor ─────────────────
    graph.add_edge("researcher", "supervisor")
    graph.add_edge("kb_retriever", "supervisor")
    graph.add_edge("generator", "supervisor")
    graph.add_edge("editor", "supervisor")

    return graph


def compile_graph():
    """Build and compile the graph into a runnable."""
    graph = build_graph()
    return graph.compile()
