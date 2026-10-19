"""Chat history, slide thumbnails, attachment roles and the Streamlit client (Step 5 of the port)."""

import base64
import io
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.models.deck_model import BulletItem, DeckModel, SlideModel
from app.services.pptx_renderer import render_pptx

COMPANY = Path("data/sample_templates/Company_Template.pptx")


def _deck() -> DeckModel:
    return DeckModel(title="Heat pumps", slides=[
        SlideModel(role="title", title="Heat pumps in cold climates", subtitle="What works"),
        SlideModel(role="title_content", title="Performance", bullets=[BulletItem(text="Useful output at -15 C")]),
    ])


def test_previews_show_inherited_dark_background(tmp_path):
    from app.services.renderers.pptx_preview import render_previews

    out = tmp_path / "deck.pptx"
    render_pptx(_deck(), COMPANY, None, out)
    paths = render_previews(out, tmp_path / "prev", width_px=320, mark_overflow=False)
    assert len(paths) == 2
    cover = Image.open(paths[0]).convert("RGB")
    r, g, b = cover.getpixel((cover.width - 5, cover.height // 2))
    assert r + g + b < 200  # the layout's dark theme background, not the default white


def test_user_chosen_role_beats_inference():
    from app.models.file_role import FileRole
    from app.services.file_classifier import classify_attached_files

    files = [
        {"id": 1, "filename": "brand.pptx", "file_type": "pptx", "purpose": "SOURCE"},
        {"id": 2, "filename": "notes.docx", "file_type": "docx", "purpose": "TEMPLATE"},
    ]
    roles = {c.file_id: c.role for c in classify_attached_files(files, "make a deck from my notes using the brand template")}
    assert roles == {1: FileRole.SOURCE, 2: FileRole.TEMPLATE}


@pytest.fixture(name="api")
def api_fixture():
    from app.main import app

    with TestClient(app) as client:
        token = client.post("/auth/login", json={"username": "demo", "password": "demo123"}).json()["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client


def test_sessions_list_most_recent_first(api):
    with patch("app.api.chat.graph_app.invoke", side_effect=lambda s: {**s, "reply": "ok"}):
        first = api.post("/chat", json={"message": "First chat about solar"}).json()
        second = api.post("/chat", json={"message": "Second chat about wind"}).json()
    sessions = api.get("/sessions").json()
    ids = [s["session_id"] for s in sessions]
    assert ids.index(second["session_id"]) < ids.index(first["session_id"])
    entry = next(s for s in sessions if s["session_id"] == first["session_id"])
    assert entry["title"] == "First chat about solar" and entry["message_count"] == 2


def test_artifact_previews_endpoint(api, tmp_path):
    from app.agents.graph import _get_sync_session
    from app.models.artifact import Artifact
    from app.services.versioning import create_version_sync

    out = tmp_path / "deck.pptx"
    render_pptx(_deck(), COMPANY, None, out)
    db = _get_sync_session()
    art = Artifact(artifact_type="pptx", title="Heat pumps", session_id=f"sess_{uuid.uuid4().hex[:8]}", run_id="run_x")
    db.add(art)
    db.flush()
    create_version_sync(db=db, artifact_id=art.id, model_json=_deck().model_dump_json(), source_file_path=out)
    db.commit()
    art_id = art.id
    db.close()

    res = api.get(f"/artifacts/{art_id}/previews", params={"width": 320})
    assert res.status_code == 200
    slides = res.json()["slides"]
    assert len(slides) == 2
    assert Image.open(io.BytesIO(base64.b64decode(slides[0]))).width == 320
    assert api.get(f"/artifacts/{art_id}/previews", params={"width": 320}).json()["slides"] == slides  # cached


def test_reupload_takes_the_newly_chosen_role(api):
    import docx

    doc = docx.Document()
    doc.add_paragraph(f"Brand guide {uuid.uuid4()}")
    buf = io.BytesIO()
    doc.save(buf)
    files = {"file": ("brand.docx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    first = api.post("/files/upload", files=files).json()
    again = api.post("/files/upload", files=files, params={"purpose": "TEMPLATE"}).json()
    assert again["id"] == first["id"] and again["purpose"] == "TEMPLATE"


# ── Streamlit client smoke test (fake backend, no server) ───────────────────


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.text = data, status, ""

    def json(self):
        return self._data


def _fake_get(url, **kwargs):
    png = io.BytesIO()
    Image.new("RGB", (48, 27), (30, 30, 40)).save(png, format="PNG")
    routes = {
        "/sessions": [{"session_id": "sess_1", "title": "EV deck", "message_count": 2, "last_activity": "2026-10-18T10:00:00"}],
        "/sessions/sess_1/messages": [
            {"role": "user", "content": "Make a deck", "run_id": "run_1"},
            {"role": "assistant", "content": "Created your deck ([Download](/artifacts/7/download?version=1))", "run_id": "run_1"},
        ],
        "/runs/run_1": {
            "run_id": "run_1", "status": "done", "reply": "Created", "citations": {"1": {"title": "NITI", "url": "https://niti.gov.in"}},
            "artifacts": [{"artifact_id": 7, "version": 1, "kind": "pptx", "title": "EV deck"}],
            "validation_report": {"passed": False, "score": 75, "issues": [
                {"severity": "warning", "where": "pptx:slide_3", "message": "Slide 3: 'content:body' uses 12pt."}]},
            "trace_steps": [{"agent_name": "plan_node", "status": "ok", "duration_ms": 1200}],
        },
        "/runs/run_1/trace": [{"agent_name": "plan_node", "status": "ok", "duration_ms": 1200, "output_summary": "ok"}],
        "/sessions/sess_1/artifacts": [{"id": 7, "title": "EV deck", "artifact_type": "pptx", "latest_version": 1, "versions": [{"version_no": 1}]}],
        "/artifacts/7/previews": {"slides": [base64.b64encode(png.getvalue()).decode()]},
    }
    path = url.split("://", 1)[-1].split("/", 1)[-1]
    data = routes.get("/" + path)
    return _Resp(data) if data is not None else _Resp({"detail": "not found"}, 404)


def test_streamlit_client_renders_history_files_and_quality():
    """Runs in a fresh interpreter: another test imports the app in bare mode, which leaves Streamlit state behind."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-c", "import tests.test_ui_support as t; t._app_checks()"],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]


def _app_checks():
    from streamlit.testing.v1 import AppTest

    with patch("httpx.get", side_effect=_fake_get):
        at = AppTest.from_file(str(Path("ui/streamlit_app.py").resolve()), default_timeout=30)
        at.run()
        assert not at.exception
        assert at.button and any(b.label == "Sign in" for b in at.button)  # login screen first

        at.session_state["jwt"] = "token"
        at.session_state["username"] = "demo"
        at.query_params["session"] = "sess_1"
        at.run()
        assert not at.exception
        labels = [b.label for b in at.button]
        assert "＋ New chat" in labels and any("EV deck" in label for label in labels)
        markdown = " ".join(m.value for m in at.markdown)
        assert "http://127.0.0.1:8000/artifacts/7/download?version=1&token=token" in markdown  # absolute link with JWT
        assert "PPTX" in markdown and "EV deck" in markdown  # download card
        assert any("Quality report" in e.label for e in at.expander)
