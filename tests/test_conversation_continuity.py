"""Follow-up requests keep the conversation's subject and attachments sensibly; edits stay inside their scope.

Regression for a live session: after a deck on Sachin Tendulkar (with a PPTX template attached), "can you also give
me report in detail on him" produced no file ("feature coming in the next step") because the PPTX was forced as the
Word template, and "him" was not resolved to the earlier subject.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.agents.interpreter import resolve_subject
from app.models.deck_model import BulletItem, DeckModel, SlideModel
from app.services.artifact_nodes import ensure_node_ids
from app.services.edit_guard import restore_out_of_scope


def test_pronouns_resolve_to_the_earlier_subject():
    prior = "Sachin Tendulkar: International Cricket Career and Statistical Records"
    assert resolve_subject("can you also give me report in detail on him", prior) == \
        "can you also give me report in detail on Sachin Tendulkar"
    assert resolve_subject("write about his early life", prior) == "write about Sachin Tendulkar's early life"
    assert resolve_subject("make a deck on solar power", prior) is None
    assert resolve_subject("report on him", "") is None


def _deck() -> DeckModel:
    return ensure_node_ids(DeckModel(title="T", slides=[
        SlideModel(role="title", title="Sachin Tendulkar"),
        SlideModel(title="Early career", bullets=[BulletItem(text="Debut in 1989", source_ids=[1])]),
        SlideModel(title="Records", bullets=[BulletItem(text="100 international centuries", source_ids=[1])]),
    ]))


def test_out_of_scope_changes_are_restored_from_the_previous_version():
    old = _deck()
    new = old.model_copy(deep=True)
    new.slides[1].title = "Early career and first tours"  # requested: slide 2
    new.slides[2].bullets[0].text = "Rewritten by the model without being asked"  # not requested: slide 3
    restored = restore_out_of_scope(old, new, [old.slides[1].node_id])
    assert restored == [old.slides[2].node_id]
    assert new.slides[1].title == "Early career and first tours"
    assert new.slides[2] == old.slides[2]
    # element-level target keeps its own slide editable
    new2 = old.model_copy(deep=True)
    new2.slides[2].bullets[0].text = "Changed bullet"
    assert restore_out_of_scope(old, new2, [old.slides[2].bullets[0].node_id]) == []


def test_follow_up_report_on_him_uses_the_deck_subject_and_default_word_template():
    from app.main import app

    with TestClient(app) as api:
        token = api.post("/auth/login", json={"username": "demo", "password": "demo123"}).json()["access_token"]
        api.headers.update({"Authorization": f"Bearer {token}"})
        tpl = Path("data/sample_templates/presentation_Template.pptx")
        up = api.post("/files/upload", files={"file": (tpl.name, tpl.read_bytes())}, data={"purpose": "TEMPLATE"}).json()
        fid = up.get("file_id") or up.get("id")
        first = api.post("/chat", json={"message": "can you generate an presentation on Sachin Tendulkar using the uploaded template",
                                        "file_ids": [fid]}).json()
        sid = first["session_id"]
        res = api.post("/chat", json={"message": "can you also give me report in detail on him", "session_id": sid,
                                      "file_ids": [fid]}).json()
        from app.agents.graph import _get_sync_session
        from app.models.run import AgentRun

        db = _get_sync_session()
        run = db.query(AgentRun).filter(AgentRun.run_id == res["run_id"]).first()
        result, plan = json.loads(run.result_json), json.loads(run.plan_json)
        db.close()
    assert "coming in the next step" not in result["reply"]
    assert [a["kind"] for a in result["artifacts"]] == ["docx"]
    assert "Sachin Tendulkar" in plan["topic"] and " him" not in plan["topic"]
    assert "default template" in result["reply"]
