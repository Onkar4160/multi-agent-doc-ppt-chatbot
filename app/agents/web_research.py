"""Web research agent using Tavily with DuckDuckGo fallback, query optimization, and snippet synthesis."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.llm.client import get_llm_client, get_search_cache_dir
from app.models.trace import AgentTrace
from app.services.source_registry import SourceRegistry

logger = logging.getLogger(__name__)

_get_cache_dir = get_search_cache_dir


class SearchQueriesSchema(BaseModel):
    """Schema for LLM query generation step."""
    queries: list[str] = Field(description="Up to 3 focused search queries containing year or 'latest'.")


class FindingItem(BaseModel):
    """A single factual research finding with citation source IDs."""
    text: str = Field(description="Factual research finding statement.")
    source_ids: list[int] = Field(default_factory=list, description="List of source IDs backing this finding.")


class ResearchFindingsSchema(BaseModel):
    """Schema for LLM synthesis step."""
    findings: list[FindingItem] = Field(description="List of 5 to 8 short factual findings.")


class ResearchResult(BaseModel):
    """Unified container for web research results."""
    queries: list[str]
    findings: list[FindingItem]
    source_ids: list[int]


# ── Search API Client with Fallback & Cache ────────────────────────────────

def _search_web_single_query(
    query: str, today_str: str, max_results: int = 4
) -> list[dict[str, Any]]:
    """Execute search for a single query using Tavily, falling back to DuckDuckGo."""
    cache_dir = _get_cache_dir()
    cache_hash = hashlib.md5(f"{query.strip().lower()}_{today_str}".encode("utf-8")).hexdigest()
    cache_path = cache_dir / f"{cache_hash}.json"

    if cache_path.exists():
        try:
            cached_data = json.loads(cache_path.read_text(encoding="utf-8"))
            logger.info(f"Loaded search results from disk cache for query '{query}'.")
            return cached_data
        except Exception as exc:
            logger.warning(f"Failed to read search cache '{cache_path}': {exc}")

    results: list[dict[str, Any]] = []
    settings = get_settings()
    tavily_key = settings.tavily_api_key.strip()

    # Try Tavily first
    if tavily_key and tavily_key not in ("EX", "mock-tavily-key"):
        try:
            from tavily import TavilyClient
            tav = TavilyClient(api_key=tavily_key)
            tav_res = tav.search(query=query, search_depth="basic", max_results=max_results)
            for item in tav_res.get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                    "published_date": item.get("published_date"),
                })
        except Exception as exc:
            logger.warning(f"Tavily search failed for query '{query}': {exc}. Falling back to DuckDuckGo.")
            results = []

    # Fallback to DuckDuckGo if Tavily key is missing or failed
    if not results:
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                ddg_res = list(ddgs.text(query, max_results=max_results))
                for item in ddg_res:
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("href", ""),
                        "snippet": item.get("body", ""),
                        "published_date": None,
                    })
        except Exception as exc:
            logger.error(f"DuckDuckGo search fallback failed for query '{query}': {exc}")

    # Write to cache
    try:
        cache_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to write search cache: {exc}")

    return results


# ── Web Research Agent Main Entry ──────────────────────────────────────────

def research(
    topic: str,
    registry: SourceRegistry | None = None,
    today: str | None = None,
    db_session: Session | None = None,
    trace_id: str | None = None,
) -> ResearchResult:
    """Perform web research on a topic using LLM + Tavily/DDG.

    Uses at most 2 LLM calls:
      1. Generate search queries (up to 3).
      2. Synthesize findings (5-8 short findings with source IDs).

    Args:
        topic: Topic string or client brief.
        registry: SourceRegistry instance to register web hits.
        today: Optional date string (default current date).
        db_session: Optional DB session for AgentTrace logging.
        trace_id: Optional trace grouping ID.

    Returns:
        ResearchResult containing queries, findings, and registered source IDs.
    """
    start_time = time.time()
    today_str = today or datetime.date.today().isoformat()
    trace_uuid = trace_id or str(uuid.uuid4())
    llm = get_llm_client()

    if registry is None:
        registry = SourceRegistry(db_session=db_session)

    # ── LLM Call 1: Generate up to 3 queries ───────────────────────────────
    query_prompt = (
        f"Topic: '{topic}'\nCurrent Year: 2026\n"
        "Generate 1 to 3 concise, highly focused web search queries to find current, authoritative facts, "
        "market statistics, and case studies. Include '2026' or 'latest' where relevant."
    )
    
    try:
        q_response = llm.generate_json(
            prompt=query_prompt,
            schema=SearchQueriesSchema,
            system="You are an expert market research analyst. Output 1-3 search queries.",
        )
        queries = q_response.queries[:3] if q_response.queries else [f"{topic} latest 2026"]
    except Exception as exc:
        logger.warning(f"Query generation LLM call failed: {exc}. Using fallback query.")
        queries = [f"{topic} 2026 enterprise strategy"]

    # ── Execute Search & Register Sources ──────────────────────────────────
    registered_source_ids: list[int] = []
    registered_snippets: list[dict[str, Any]] = []

    for q in queries:
        raw_results = _search_web_single_query(q, today_str=today_str, max_results=4)
        for res in raw_results:
            url = res.get("url", "").strip()
            title = res.get("title", "Web Source").strip()
            snippet = res.get("snippet", "").strip()
            pub_date = res.get("published_date")

            if not snippet or not url:
                continue

            sid = registry.add(
                kind="web",
                title=title,
                url_or_path=url,
                snippet=snippet,
                score=0.8,
                published_date=pub_date,
            )
            if sid not in registered_source_ids:
                registered_source_ids.append(sid)
                registered_snippets.append({
                    "source_id": sid,
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                })

    # ── LLM Call 2: Synthesize findings from snippets ──────────────────────
    if registered_snippets:
        snippets_formatted = "\n---\n".join(
            f"Source ID [{item['source_id']}] Title: {item['title']}\nSnippet: {item['snippet']}"
            for item in registered_snippets
        )
        synthesis_prompt = (
            f"Research Topic: '{topic}'\n\n"
            "Source Snippets:\n"
            f"{snippets_formatted}\n\n"
            "Based ONLY on the provided snippets above, extract 5 to 8 short, factual key findings. "
            "For each finding, provide the text and the list of integer source_ids backing it. "
            "Never invent facts not present in the snippets."
        )
        try:
            f_response = llm.generate_json(
                prompt=synthesis_prompt,
                schema=ResearchFindingsSchema,
                system="You are a meticulous factual research synthesizer. Extract facts strictly from provided sources.",
            )
            findings = f_response.findings
        except Exception as exc:
            logger.warning(f"Research synthesis LLM call failed: {exc}. Creating basic findings.")
            findings = [
                FindingItem(text=item["snippet"][:150], source_ids=[item["source_id"]])
                for item in registered_snippets[:5]
            ]
    else:
        findings = [FindingItem(text=f"No external search results found for '{topic}'.", source_ids=[])]

    duration_ms = int((time.time() - start_time) * 1000)

    # Log AgentTrace row
    if db_session:
        try:
            trace_row = AgentTrace(
                trace_id=trace_uuid,
                agent_name="web_research",
                input_summary=f"topic='{topic[:100]}', queries={queries}",
                output_summary=f"Retrieved {len(registered_source_ids)} web sources, generated {len(findings)} findings",
                duration_ms=duration_ms,
                status="ok",
            )
            db_session.add(trace_row)
            db_session.flush()
        except Exception as exc:
            logger.warning(f"Failed to record AgentTrace row for web_research: {exc}")

    logger.info(f"Web research completed in {duration_ms}ms ({len(queries)} queries, {len(findings)} findings).")
    return ResearchResult(
        queries=queries,
        findings=findings,
        source_ids=registered_source_ids,
    )
