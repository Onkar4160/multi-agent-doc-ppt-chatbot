"""Follow-ups take their subject from the whole conversation (ADR-035).

Regressions from live sessions: after "Generate the presentation on Gen AI 2026", the request "can you generate the
document as well on this same" produced a document about "This"; "use this template and give me presentation as
same" produced a deck titled "Use the presentation template and give me presentation as same".
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.services.conversation_memory import content_words, resolve_request, subjects_from_runs

CTX = SimpleNamespace(subject_history=["Gen AI in 2026"], current_topic="Gen AI in 2026")


@pytest.mark.parametrize("message", [
    "can you generate the document as well on this same",
    "use this template and give me presentation as same",
    "can you also give me report in detail on him",
    "make a report on that",
    "do the same in Excel",
    "now turn it into a word document",
])
def test_referring_follow_ups_take_the_earlier_subject(message):
    rr = resolve_request(message, CTX, deliverable="document")
    assert rr.subject == "Gen AI in 2026"
    assert rr.referential and not rr.missing
    assert "Gen AI in 2026" in rr.standalone
    assert not content_words(rr.standalone.replace("Gen AI in 2026", "")) or "this same" not in rr.standalone


@pytest.mark.parametrize("message, subject", [
    ("Generate the presentation on Gen AI 2026", "Gen AI 2026"),
    ("make the presentation and report on the Attack 26/11", "Attack 26/11"),
    ("now create a deck on quantum computing for investors", "Quantum computing for investors"),
])
def test_a_named_subject_wins_over_the_conversation(message, subject):
    rr = resolve_request(message, CTX)
    assert rr.subject == subject and not rr.referential


def test_possessives_attach_to_the_earlier_subject():
    ctx = SimpleNamespace(subject_history=["Sachin Tendulkar"], current_topic="")
    rr = resolve_request("write a report on his early life", ctx)
    assert rr.subject == "Sachin Tendulkar's early life"


def test_no_subject_anywhere_is_reported_as_missing():
    rr = resolve_request("make a deck on this", SimpleNamespace(subject_history=[], current_topic=""))
    assert rr.missing and rr.subject == ""


def test_planner_subject_is_used_unless_it_merely_echoes_the_message():
    msg = "can you generate the document as well on this same"
    assert resolve_request(msg, CTX, planner_subject="Generative AI trends in 2026").subject == "Generative AI trends in 2026"
    assert resolve_request(msg, CTX, planner_subject=msg).subject == "Gen AI in 2026"
    assert resolve_request(msg, CTX, planner_subject="this same").subject == "Gen AI in 2026"


def test_subjects_from_runs_skip_unresolved_requests():
    plans = [{"topic": "use the presentation template and give me presentation as same", "action": "generate"},
             {"subject": "Gen AI in 2026", "topic": "Create a presentation on Gen AI in 2026"}]
    assert subjects_from_runs(plans) == ["Gen AI in 2026"]


def _login(api: TestClient) -> None:
    token = api.post("/auth/login", json={"username": "demo", "password": "demo123"}).json()["access_token"]
    api.headers.update({"Authorization": f"Bearer {token}"})


def _run(run_id: str) -> tuple[dict, dict]:
    from app.agents.graph import _get_sync_session
    from app.models.run import AgentRun

    db = _get_sync_session()
    run = db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
    result, plan = json.loads(run.result_json), json.loads(run.plan_json or "{}")
    db.close()
    return result, plan


def test_document_as_well_on_this_same_keeps_the_deck_subject():
    from app.main import app

    with TestClient(app) as api:
        _login(api)
        first = api.post("/chat", json={"message": "Generate the presentation on Gen AI 2026"}).json()
        sid = first["session_id"]
        res = api.post("/chat", json={"message": "can you generate the document as well on this same", "session_id": sid}).json()
        result, plan = _run(res["run_id"])
    assert [a["kind"] for a in result["artifacts"]] == ["docx"]
    assert plan["subject"] == "Gen AI 2026"
    title = result["artifacts"][0]["title"]
    assert "Gen AI 2026" in title and "this" not in title.lower().split()
    assert "Gen AI 2026" in result["reply"]


def test_use_this_template_as_same_rebuilds_the_deck_on_the_same_subject():
    from app.main import app

    with TestClient(app) as api:
        _login(api)
        first = api.post("/chat", json={"message": "can you generate the presentation on Gen AI in 2026"}).json()
        sid = first["session_id"]
        tpl = Path("data/sample_templates/presentation_Template.pptx")
        up = api.post("/files/upload", files={"file": (tpl.name, tpl.read_bytes())}, data={"purpose": "TEMPLATE"}).json()
        fid = up.get("file_id") or up.get("id")
        res = api.post("/chat", json={"message": "use this template and give me presentation as same", "session_id": sid,
                                      "file_ids": [fid]}).json()
        result, plan = _run(res["run_id"])
    assert [a["kind"] for a in result["artifacts"]] == ["pptx"]
    assert plan["subject"] == "Gen AI in 2026"
    assert "template" not in result["artifacts"][0]["title"].lower()


def test_a_deck_on_this_in_a_new_chat_asks_what_it_should_be_about():
    from app.main import app

    with TestClient(app) as api:
        _login(api)
        res = api.post("/chat", json={"message": "make a presentation on this"}).json()
        result, _ = _run(res["run_id"])
    assert result["artifacts"] == []
    assert "what should it be about" in result["reply"].lower()
