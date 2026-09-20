"""Supervisor agent – routes to specialized workers based on the current state."""

from __future__ import annotations

import logging
import time

from langchain_core.messages import AIMessage

from app.agents.state import AgentState
from app.llm.client import get_llm_client
from app.llm.schemas import SupervisorDecision

logger = logging.getLogger(__name__)


def supervisor_node(state: AgentState) -> dict:
    """Decide which agent should act next based on current state."""
    start = time.time()

    llm = get_llm_client()

    # Build context summary for the supervisor
    context_parts = [
        f"User request: {state.get('user_request', '')}",
        f"Output type: {state.get('output_type', 'docx')}",
        f"Has template: {state.get('template_profile') is not None}",
        f"Research results: {len(state.get('research_results', []))} facts",
        f"KB results: {len(state.get('kb_results', []))} chunks",
        f"Has document model: {state.get('document_model') is not None}",
        f"Has edit instructions: {state.get('edit_instructions') is not None}",
    ]
    context = "\n".join(context_parts)

    prompt = f"""You are a supervisor agent orchestrating a document generation pipeline.
Based on the current state, decide which agent should act next.

Current state:
{context}

Available agents:
- researcher: Search the web for facts and data relevant to the user's request. Use when more information is needed.
- kb_retriever: Search the enterprise knowledge base for relevant internal documents. Use when internal context is needed.
- generator: Generate the document/presentation content as structured JSON. Use when research and KB data are ready.
- editor: Edit an existing document model based on edit instructions. Use when the user wants to modify a previously generated document.
- FINISH: The task is complete. Use when the document model has been generated or edited.

Rules:
1. If no research has been done yet, route to researcher first.
2. If KB retrieval hasn't been done, route to kb_retriever after researcher.
3. If research and KB data are ready but no document model exists, route to generator.
4. If edit instructions exist and a document model exists, route to editor.
5. If the document model exists and no edits are needed, route to FINISH.

Return the next_step and your reasoning."""

    decision = llm.generate_json(prompt, SupervisorDecision, use_cache=False, temperature=0.1)

    duration_ms = int((time.time() - start) * 1000)
    logger.info("Supervisor → %s (reason: %s, %dms)", decision.next_step, decision.reasoning, duration_ms)

    return {
        "next_step": decision.next_step,
        "messages": [AIMessage(content=f"Supervisor routing to: {decision.next_step}. {decision.reasoning}")],
    }
