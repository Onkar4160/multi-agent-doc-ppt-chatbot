"""Model fallback chain, plain-language failure replies, "try again" and file-only messages (Step 2 of the port)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.llm import client as llm_client_mod
from app.llm.client import LLMCallStats, LLMClient
from app.llm.errors import LLMModelUnavailable, LLMRateLimited, friendly_error


class _ApiError(Exception):
    """Shape of google.genai.errors.APIError: an HTTP code and the response JSON in ``details``."""

    def __init__(self, code: int, details: list | None = None) -> None:
        super().__init__(f"{code} error")
        self.code = code
        self.details = {"error": {"code": code, "details": details or []}}


def _daily_quota() -> _ApiError:
    return _ApiError(429, [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3600s"},
    ])


def _client(script: dict[str, list]) -> tuple[LLMClient, list[str], list[float]]:
    """Real LLMClient retry logic over a scripted transport: each model pops its next outcome."""
    c = LLMClient.__new__(LLMClient)
    c._primary = "gemini-3.8-flash"
    c._fallbacks = ["gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"]
    c._max_retries = 3
    c._stats = LLMCallStats()
    c._last_record = None
    calls: list[str] = []
    sleeps: list[float] = []
    c._sleep = sleeps.append

    def single_call(model, prompt, **kwargs):
        calls.append(model)
        outcome = script.get(model, ["ok"]).pop(0) if script.get(model) else "ok"
        if isinstance(outcome, Exception):
            raise outcome
        return f"{outcome} from {model}"

    c._single_call = single_call
    return c, calls, sleeps


@pytest.fixture(autouse=True)
def _fresh_availability():
    llm_client_mod._unavailable.clear()
    yield
    llm_client_mod._unavailable.clear()


def test_retired_model_is_skipped_for_a_day():
    c, calls, _ = _client({"gemini-3.8-flash": [_ApiError(404)]})
    assert c._call_with_retry("hi") == "ok from gemini-3.7-flash"
    assert c._call_with_retry("hi again") == "ok from gemini-3.7-flash"
    assert calls == ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.7-flash"]


def test_daily_quota_moves_to_next_model_without_waiting():
    c, calls, sleeps = _client({"gemini-3.8-flash": [_daily_quota()]})
    assert c._call_with_retry("hi") == "ok from gemini-3.7-flash"
    assert sleeps == []
    assert llm_client_mod._unavailable["gemini-3.8-flash"] > 0


def test_short_rate_limit_waits_advised_delay_then_retries_same_model():
    short = _ApiError(429, [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3s"}])
    c, calls, sleeps = _client({"gemini-3.8-flash": [short, "ok"]})
    assert c._call_with_retry("hi") == "ok from gemini-3.8-flash"
    assert sleeps == [3.0]
    assert calls == ["gemini-3.8-flash", "gemini-3.8-flash"]


def test_every_model_exhausted_gives_quota_error_and_friendly_text():
    c, _, _ = _client({m: [_daily_quota()] for m in ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite")})
    with pytest.raises(LLMRateLimited) as info:
        c._call_with_retry("hi")
    assert info.value.quota
    text = friendly_error(info.value, "build the presentation")
    assert text.startswith("I couldn't build the presentation:") and "usage limit" in text and "Nothing was delivered" in text


def test_fallback_chain_comes_from_comma_separated_setting(monkeypatch):
    from app.core.config import Settings

    s = Settings(GEMINI_FALLBACK_MODEL="gemini-3.7-flash, gemini-3.5-flash ,,gemini-3.1-flash-lite")
    assert s.fallback_models == ["gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"]


def test_friendly_error_variants():
    assert "not available to this API key" in friendly_error(LLMModelUnavailable("x"), "answer that")
    assert "busy" in friendly_error(LLMRateLimited("x"), "answer that")
    assert "unexpected" in friendly_error(KeyError("x"), "answer that")


def test_failed_node_records_plain_language_failure_and_reply():
    from app.agents.graph import ensure_reply, traced

    @traced("generate_deck_node")
    def boom(state):
        raise LLMRateLimited("all out", quota=True)

    state = boom({"run_id": "run_test_fail", "user_message": "deck", "errors": []})
    assert state["failure"].startswith("I couldn't build the presentation:")
    assert ensure_reply(state) == state["failure"]
    assert "try again" in ensure_reply({}).lower()


def test_template_profile_cache_is_versioned():
    from app.models.template_profile import ANALYZER_VERSION, TemplateProfile, is_current_profile

    assert not is_current_profile(TemplateProfile().model_dump_json())  # cached before versioning
    assert not is_current_profile(None)
    assert is_current_profile(TemplateProfile(analyzer_version=ANALYZER_VERSION).model_dump_json())


# ── Chat API ────────────────────────────────────────────────────────────────


@pytest.fixture(name="api")
def api_fixture():
    from app.main import app

    with TestClient(app) as client:
        token = client.post("/auth/login", json={"username": "demo", "password": "demo123"}).json()["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client


def _messages(api, session_id):
    return api.get(f"/sessions/{session_id}/messages").json()


def test_failed_run_stores_reply_and_try_again_retries_the_request(api):
    with patch("app.api.chat.graph_app.invoke", side_effect=LLMRateLimited("out", quota=True)):
        first = api.post("/chat", json={"message": "Make a 6-slide deck on EV charging in India"}).json()
    session_id = first["session_id"]
    msgs = _messages(api, session_id)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert "usage limit" in msgs[-1]["content"]
    assert api.get(f"/runs/{first['run_id']}").json()["status"] == "failed"

    seen = {}

    def fake_invoke(state):
        seen["message"] = state["user_message"]
        return {**state, "reply": "Done."}

    with patch("app.api.chat.graph_app.invoke", side_effect=fake_invoke):
        api.post("/chat", json={"message": "try again", "session_id": session_id})
    assert seen["message"] == "Make a 6-slide deck on EV charging in India"
    msgs = _messages(api, session_id)
    assert msgs[-2]["content"] == "try again" and msgs[-1]["content"] == "Done."


def test_empty_graph_reply_still_answers(api):
    with patch("app.api.chat.graph_app.invoke", side_effect=lambda state: {**state, "reply": ""}):
        res = api.post("/chat", json={"message": "what is a heat pump?"}).json()
    reply = _messages(api, res["session_id"])[-1]
    assert reply["role"] == "assistant" and reply["content"].strip()


def test_attached_file_without_text_gets_a_reply(api):
    import io
    import uuid

    import docx

    doc = docx.Document()
    doc.add_paragraph(f"Brief notes {uuid.uuid4()}")
    buf = io.BytesIO()
    doc.save(buf)
    up = api.post(
        "/files/upload",
        files={"file": ("brief_notes.docx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()
    with patch("app.api.chat.graph_app.invoke") as invoke:
        res = api.post("/chat", json={"message": "", "file_ids": [up["id"]]}).json()
    invoke.assert_not_called()
    assert res["status"] == "done" and "brief_notes.docx" in res["reply"]
    msgs = _messages(api, res["session_id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert api.post("/chat", json={"message": "  "}).status_code == 422
