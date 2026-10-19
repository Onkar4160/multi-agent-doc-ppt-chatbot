"""Live smoke tests against the real providers (ADR-020). Opt-in: ``python -m pytest -m live tests/live``.

Each test spends a tiny amount of quota: one Gemini call, one scholarly search, one photo search.
"""

import pytest

pytestmark = pytest.mark.live


@pytest.fixture
def real_llm(monkeypatch):
    """A real LLM client using the keys in .env (the test suite otherwise forces MOCK_LLM)."""
    from app.core.config import get_settings, reset_settings
    from app.llm.client import LLMClient, reset_llm_client

    monkeypatch.setenv("MOCK_LLM", "false")
    reset_settings()
    if not get_settings().gemini_api_key:
        pytest.skip("GEMINI_API_KEY not set")
    client = LLMClient()
    yield client
    monkeypatch.setenv("MOCK_LLM", "true")
    reset_settings()
    reset_llm_client()


def test_gemini_answers_through_the_model_chain(real_llm):
    from app.llm.usage import track_run

    with track_run(budget=3) as usage:
        text = real_llm.generate_text("Reply with exactly the word READY and nothing else.", use_cache=False, temperature=0)
    assert "ready" in text.lower()
    assert usage.calls >= 1 and usage.input_tokens > 0


def test_scholarly_indexes_return_papers():
    from app.services.academic_search import search_scholarly

    papers = search_scholarly("heat pump performance in cold climates")
    assert papers, "no scholarly index returned results"
    assert all(p["title"] and p["url"] for p in papers)


def test_openverse_returns_a_photo(tmp_path):
    from app.services.images import OpenverseProvider, resolve_photo

    provider = OpenverseProvider()
    try:
        photo = resolve_photo("electric vehicle charging", provider, tmp_path, used=set(), cache={})
    finally:
        provider.close()
    assert photo is not None and photo.path.exists() and photo.credit.startswith("Photo:")
