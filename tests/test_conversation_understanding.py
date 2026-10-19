"""Stage 4: request understanding and conversation context (PRD FR-CONV-2…7, AC-9).

The labelled evaluation set (eval/conversation) is scored with the offline planner; the acceptance bar in PLAN
Stage 4 is ≥ 90 % request-kind and target accuracy and ≥ 90 % clarification recall on every split.
"""

import json
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.agents.interpreter import interpret, parse_style_changes, resolve_targets
from app.models.context import ConversationContext
from app.models.deck_model import DeckModel, SlideModel


@pytest.mark.parametrize("split", ["dev", "test", "test2"])
def test_labelled_set_meets_the_stage_4_bar(split):
    from app.eval.conversation import evaluate

    r = evaluate(split)
    assert r["cases"] >= 15
    assert r["kind_accuracy"] >= 90, r["failures"]
    assert r["target_accuracy"] is None or r["target_accuracy"] >= 90, r["failures"]
    assert r["clarification_recall"] is None or r["clarification_recall"] >= 90, r["failures"]
    assert r["clarification_accuracy"] >= 90, r["failures"]  # no needless questions either


def test_evaluation_set_has_enough_labelled_messages():
    from app.eval.conversation import load_cases

    cases = load_cases()
    assert len(cases) >= 60
    kinds = {c["expect"]["kind"] for c in cases}
    assert kinds == {"new_task", "continue_task", "modify_artifact", "regenerate", "change_objective",
                     "clarification_answer", "question", "retry"}


def _deck(n=4) -> DeckModel:
    return DeckModel(title="T", slides=[SlideModel(title=f"S{i}") for i in range(1, n + 1)])


def test_targets_resolve_to_node_ids_and_out_of_range_is_reported():
    deck = _deck()
    targets, missing = resolve_targets("swap slides 2 and 4, and fix slide 9", deck)
    assert [t.node_id for t in targets] == [deck.slides[1].node_id, deck.slides[3].node_id]
    assert missing == ["slide 9"]


def test_style_changes_are_parsed_deterministically():
    assert [(c.path, c.value) for c in parse_style_changes("Make the heading font size 20pt")] == [("heading_1.size_pt", 20)]
    assert [(c.path, c.value) for c in parse_style_changes("Change the font of the body text to Calibri 11")] == [
        ("body.font", "Calibri"), ("body.size_pt", 11)]


def test_vague_restyle_asks_which_property():
    interp = interpret("Change the font", context=_ctx(), active_model=_deck())
    assert interp.needs_clarification and "formatting" in interp.clarification_questions[0].question


def test_edit_without_any_file_asks_first():
    interp = interpret("Make slide 2 shorter")
    assert interp.kind == "modify_artifact" and interp.needs_clarification
    assert "no file in this chat" in interp.clarification_questions[0].question


def test_inferred_format_is_recorded_as_an_assumption():
    interp = interpret("Explain our onboarding process to new employees in 5 slides")
    assert interp.output_spec.format == "pptx"
    assert interp.assumptions == [] or "inferred" in interp.assumptions[0]
    interp = interpret("Something on remote work for managers")
    assert any("inferred" in a for a in interp.assumptions)


def _ctx() -> ConversationContext:
    art = {"artifact_id": 1, "kind": "pptx", "title": "T", "latest_version": 1, "active": True}
    return ConversationContext(active_artifact=art, artifact_history=[art])


# ── Integration with the graph and task state ──────────────────────────────


@pytest.fixture(name="api")
def api_fixture():
    from app.main import app

    with TestClient(app) as client:
        token = client.post("/auth/login", json={"username": "demo", "password": "demo123"}).json()["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client


def _seed_deck_session(slides: int = 4) -> str:
    """A chat session that already has a deck (as if generated earlier)."""
    from app.agents.graph import _get_sync_session
    from app.models.artifact import Artifact
    from app.models.chat import ChatMessage, ChatSession
    from app.services.versioning import create_version_sync

    sid = f"sess_{uuid.uuid4().hex[:8]}"
    db = _get_sync_session()
    db.add(ChatSession(session_id=sid, title="EV deck", owner="demo"))
    db.add(ChatMessage(session_id=sid, role="user", content="Make a deck on EV charging"))
    art = Artifact(artifact_type="pptx", title="EV deck", session_id=sid, run_id="run_seed", owner="demo")
    db.add(art)
    db.flush()
    import tempfile
    from pathlib import Path

    f = Path(tempfile.mkdtemp()) / "deck.pptx"
    f.write_bytes(b"x")
    create_version_sync(db=db, artifact_id=art.id, model_json=_deck(slides).model_dump_json(), source_file_path=f)
    db.commit()
    db.close()
    return sid


def _run_result(run_id: str) -> dict:
    from app.agents.graph import _get_sync_session
    from app.models.run import AgentRun

    db = _get_sync_session()
    run = db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
    db.close()
    return json.loads(run.result_json)


def test_out_of_range_edit_gets_a_clarification_listing_the_slides(api):
    sid = _seed_deck_session(slides=4)
    res = api.post("/chat", json={"message": "Change slide 15 to talk about the team", "session_id": sid}).json()
    result = _run_result(res["run_id"])
    assert result["interpretation"]["kind"] == "modify_artifact"
    assert "slide 15" in result["reply"] and "Slide 4: S4" in result["reply"]
    assert result["artifacts"] == []
    from app.agents.graph import _get_sync_session
    from app.services.task_state import active_task

    db = _get_sync_session()
    assert active_task(db, sid).status == "awaiting_clarification"
    db.close()


def test_question_about_the_deck_is_answered_not_generated(api):
    sid = _seed_deck_session()
    with patch("app.agents.graph.generate_deck_model") as gen:
        res = api.post("/chat", json={"message": "What does slide 3 say about pricing?", "session_id": sid}).json()
    gen.assert_not_called()
    result = _run_result(res["run_id"])
    assert result["interpretation"]["kind"] == "question"
    assert result["artifacts"] == []


def test_new_task_opens_a_task_with_its_output_spec_and_word_numbers_set_slide_count():
    from app.agents.graph import _get_sync_session, _interpret_and_track
    from app.agents.supervisor import parse_plan
    from app.models.chat import ChatSession
    from app.services.task_state import active_task

    sid = f"sess_{uuid.uuid4().hex[:8]}"
    db = _get_sync_session()
    db.add(ChatSession(session_id=sid, title="t", owner="demo"))
    db.commit()
    db.close()
    message = "Create a five-slide deck on heat pumps for homeowners, friendly tone"
    plan = parse_plan(message)
    state = {"session_id": sid, "user_message": message}
    _interpret_and_track(state, plan, ConversationContext())
    assert plan.slide_count == 5
    assert state["interpretation"]["kind"] == "new_task"
    db = _get_sync_session()
    task = active_task(db, sid)
    db.close()
    spec = json.loads(task.output_spec_json)
    assert task.id == state["task_id"] and task.status == "active"
    assert spec["format"] == "pptx" and spec["audience"] == "homeowners" and spec["tone"] == "friendly"
    assert spec["length"] == {"unit": "slides", "value": 5, "explicit": True}


def test_clarification_answer_completes_the_waiting_task():
    from app.agents.graph import _get_sync_session, _interpret_and_track
    from app.agents.supervisor import parse_plan
    from app.models.chat import ChatSession
    from app.services.task_state import active_task

    sid = f"sess_{uuid.uuid4().hex[:8]}"
    db = _get_sync_session()
    db.add(ChatSession(session_id=sid, title="t", owner="demo"))
    db.commit()
    db.close()
    first = {"session_id": sid, "user_message": "Make a deck on CJP"}
    _interpret_and_track(first, parse_plan(first["user_message"]), ConversationContext())
    merged = "Make a deck on CJP (meaning: Crime and Justice Programme)"
    second = {"session_id": sid, "user_message": merged, "raw_message": "Crime and Justice Programme",
              "pending_clarification": "Make a deck on CJP", "kind_hint": "clarification_answer"}
    _interpret_and_track(second, parse_plan(merged), ConversationContext())
    db = _get_sync_session()
    task = active_task(db, sid)
    db.close()
    assert second["interpretation"]["kind"] == "clarification_answer"
    assert task.id == first["task_id"] == second["task_id"]
    assert task.status == "active" and task.objective == merged


def test_cli_eval_reports_metrics(capsys):
    from app.cli import main

    assert main(["eval", "conversation", "--split", "test"]) == 0
    out = capsys.readouterr().out
    report = json.loads(out[out.index("{"):])
    assert report["cases"] == 25 and report["kind_accuracy"] >= 90


def test_question_overrides_a_planner_that_wanted_to_generate():
    """Regression: the override referenced an unimported name, so a disagreeing planner silently won."""
    from app.agents.graph import _interpret_and_track
    from app.agents.supervisor import parse_plan
    from app.models.execution_plan import OperationType

    plan = parse_plan("What does slide 3 say about pricing?")
    plan.operation, plan.action = OperationType.CREATE, "generate"  # what an LLM planner might return
    state = {"session_id": "", "user_message": "What does slide 3 say about pricing?"}
    _interpret_and_track(state, plan, _ctx())
    assert state["interpretation"]["kind"] == "question"
    assert plan.action == "answer" and plan.operation == OperationType.ANSWER
