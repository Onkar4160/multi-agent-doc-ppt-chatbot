"""Automated unit and integration tests for RAG, vector store, chunking, source registry, and web research."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.agents.rag_agent import retrieve
from app.agents.web_research import research, FindingItem, ResearchResult, _search_web_single_query
from app.core.config import get_settings
from app.services.context_builder import build_context
from app.services.ingestion import chunk_parsed_blocks, chunk_raw_text
from app.services.source_registry import SourceRegistry
from app.services.vector_store import InMemoryStore, PineconeStore, get_vector_store, set_vector_store_override

SAMPLE_DIR = Path("data/sample_kb")


@pytest.fixture(autouse=True)
def use_in_memory_store():
    """Ensure tests use InMemoryStore by default."""
    store = InMemoryStore()
    set_vector_store_override(store)
    yield store
    set_vector_store_override(None)


# 1. Chunker Unit Test
def test_chunking():
    """Test chunking of raw text and parsed blocks into ~200-300 word segments with overlap."""
    text = " ".join([f"word{i}" for i in range(700)])
    chunks = chunk_raw_text(text, default_heading="TestSection")

    assert len(chunks) >= 3
    assert chunks[0]["heading"] == "TestSection"
    assert len(chunks[0]["text"].split()) <= 300

    # Test overlap: check if last words of chunk 0 appear in chunk 1
    c0_words = chunks[0]["text"].split()
    c1_words = chunks[1]["text"].split()
    assert c0_words[-1] in c1_words


# 2. SourceRegistry Deduplication Test
def test_source_registry_dedupe():
    """Test SourceRegistry assigns sequential IDs starting at 1 and dedupes web URLs and KB items."""
    registry = SourceRegistry()

    sid1 = registry.add("web", "Source 1", "https://example.com/page1", "Snippet 1", score=0.8)
    sid2 = registry.add("web", "Source 2", "https://example.com/page2", "Snippet 2", score=0.7)
    sid3 = registry.add("web", "Source 1 Duplicate", "https://example.com/page1", "Updated Snippet", score=0.9)

    assert sid1 == 1
    assert sid2 == 2
    assert sid3 == 1  # Deduplicated!
    assert len(registry) == 2
    assert registry.get(1)["score"] == 0.9

    exported = registry.export_sources()
    assert 1 in exported
    assert exported[1]["url"] == "https://example.com/page1"


# 3. InMemoryStore Query Test
def test_in_memory_vector_store():
    """Test InMemoryStore vector upsert and cosine similarity query."""
    store = InMemoryStore()

    vec1 = [1.0] + [0.0] * 383
    vec2 = [0.0, 1.0] + [0.0] * 382
    vec3 = [0.8, 0.6] + [0.0] * 382

    store.upsert([
        {"id": "doc1", "values": vec1, "metadata": {"title": "Doc One"}},
        {"id": "doc2", "values": vec2, "metadata": {"title": "Doc Two"}},
        {"id": "doc3", "values": vec3, "metadata": {"title": "Doc Three"}},
    ], namespace="test-ns")

    # Query vector close to vec1
    query_vec = [0.95, 0.05] + [0.0] * 382
    results = store.query(query_vec, top_k=2, namespace="test-ns")

    assert len(results) == 2
    assert results[0]["id"] == "doc1"
    assert results[0]["score"] > 0.9
    assert results[1]["id"] == "doc3"

    stats = store.stats(namespace="test-ns")
    assert stats["namespace_vector_count"] == 3


# 4. Web Research Unit Test with Fake Search Client & Mock LLM
def test_web_research_agent_mock(tmp_path: Path):
    """Test web research query generation, search, deduplication, and snippet synthesis."""
    fake_results = [
        {
            "title": "Agentic AI Trends 2026",
            "url": "https://fake-research.org/agentic-2026",
            "snippet": "72% of Indian mid-size enterprises adopt agentic workflows by Q4 2026.",
            "published_date": "2026-01-15",
        }
    ]

    registry = SourceRegistry()

    with patch("app.agents.web_research._search_web_single_query", return_value=fake_results):
        res = research("GenAI in India", registry=registry, today="2026-09-20")

    assert len(res.queries) >= 1
    assert len(res.findings) >= 1
    assert len(registry) >= 1
    assert registry.get(1)["url"] == "https://fake-research.org/agentic-2026"


# 5. Tavily to DuckDuckGo Fallback Test
def test_tavily_to_ddg_fallback():
    """Test fallback from Tavily to DuckDuckGo when Tavily API key is missing or fails."""
    unique_q = f"test fallback query {uuid.uuid4()}"
    with patch("app.agents.web_research.get_settings") as mock_settings:
        s_inst = MagicMock()
        s_inst.tavily_api_key = ""
        mock_settings.return_value = s_inst

        mock_ddgs_inst = MagicMock()
        mock_ddgs_inst.__enter__.return_value = mock_ddgs_inst
        mock_ddgs_inst.text.return_value = [{"title": "DDG Result", "href": "https://ddg.com/1", "body": "DDG Snippet"}]

        with patch("duckduckgo_search.DDGS", return_value=mock_ddgs_inst):
            res = _search_web_single_query(unique_q, today_str="2026-09-20")

    assert len(res) >= 1
    assert res[0]["title"] == "DDG Result"
    assert res[0]["url"] == "https://ddg.com/1"


# 6. Optional Live Integration Test (Skipped if API keys are missing)
@pytest.mark.skipif(
    not os.environ.get("PINECONE_API_KEY") or os.environ.get("MOCK_LLM") == "true",
    reason="Requires live Pinecone key and live Gemini API key."
)
def test_rag_and_research_integration():
    """Integration test verifying end-to-end context building with live services."""
    ctx = build_context("Generative AI consulting services in Pune", use_web=True, use_kb=True)

    assert isinstance(ctx.sources_map, dict)
    assert len(ctx.sources_map) > 0
    assert len(ctx.findings) > 0
