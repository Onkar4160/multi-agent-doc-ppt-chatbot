"""Research agent – searches the web for facts using Tavily (fallback: DuckDuckGo)."""

from __future__ import annotations

import logging
import time

from langchain_core.messages import AIMessage

from app.agents.state import AgentState
from app.core.config import get_settings

logger = logging.getLogger(__name__)


def researcher_node(state: AgentState) -> dict:
    """Search the web for facts relevant to the user's request."""
    start = time.time()
    user_request = state.get("user_request", "")

    facts = []

    # Try Tavily first
    try:
        facts = _search_tavily(user_request)
    except Exception as exc:
        logger.warning("Tavily search failed: %s – falling back to DuckDuckGo", exc)
        try:
            facts = _search_duckduckgo(user_request)
        except Exception as exc2:
            logger.error("DuckDuckGo search also failed: %s", exc2)

    duration_ms = int((time.time() - start) * 1000)
    logger.info("Researcher found %d facts in %dms", len(facts), duration_ms)

    return {
        "research_results": facts,
        "messages": [AIMessage(content=f"Research complete: found {len(facts)} web results.")],
    }


def _search_tavily(query: str) -> list[dict]:
    """Search using Tavily API."""
    from tavily import TavilyClient

    settings = get_settings()
    client = TavilyClient(api_key=settings.tavily_api_key)
    response = client.search(query, search_depth="advanced", max_results=8)

    return [
        {
            "text": r.get("content", ""),
            "source_url": r.get("url", ""),
            "source_title": r.get("title", ""),
        }
        for r in response.get("results", [])
    ]


def _search_duckduckgo(query: str) -> list[dict]:
    """Fallback search using DuckDuckGo."""
    from duckduckgo_search import DDGS

    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=8))

    return [
        {
            "text": r.get("body", ""),
            "source_url": r.get("href", ""),
            "source_title": r.get("title", ""),
        }
        for r in results
    ]
