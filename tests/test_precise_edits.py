"""An edit changes only the part the user named; the next version keeps V1's template, photos and every other
unit exactly (FR-EDIT-2/3, ADR-036)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.edit_content import apply_precise_ops, normalize_targets, outline_for_editor
from app.agents.editor import edit_artifact
from app.core.database import Base
from app.models.artifact import Artifact, ArtifactVersion
from app.models.deck_model import BulletItem, DeckModel, SlideBulletsBlock, SlideModel
from app.models.edit_ops import EditPlan, ReplaceTextOp, UpdatePointOp, UpdateSlideOp
from app.services.artifact_nodes import ensure_node_ids
from app.services.edit_guard import file_units
from app.services.images import PhotoAsset
from app.services.pptx_renderer import render_pptx
from app.services.render_context import RenderContext, deck_photos_by_node

TEMPLATE = Path("data/sample_templates/Company_Template.pptx")


def _deck() -> DeckModel:
    pts = lambda *t: [SlideBulletsBlock(items=[BulletItem(text=x, source_ids=[1]) for x in t])]  # noqa: E731
    return ensure_node_ids(DeckModel(title="Gen AI in 2026", slides=[
        SlideModel(role="title", archetype="title", title="Gen AI in 2026", subtitle="An outlook | September 2026"),
        SlideModel(archetype="bullets", title="Adoption is broad", blocks=pts("79% of firms use it", "Agents automate tasks")),
        SlideModel(archetype="bullets", title="Risks remain", blocks=pts("Hallucination is the top concern", "Costs are rising")),
        SlideModel(archetype="summary", title="Takeaways", blocks=pts("Scale what works", "Govern early")),
    ]))


def test_precise_ops_change_one_point_and_one_phrase():
    deck = _deck()
    touched = apply_precise_ops(deck, [
        UpdatePointOp(slide_index=2, point_index=2, new_text="Agentic workflows automate multi-step tasks"),
        ReplaceTextOp(node_id=deck.slides[2].node_id, find="top concern", replace="leading concern"),
    ])
    assert touched == [deck.slides[1].node_id, deck.slides[2].node_id]
    assert deck.slides[1].blocks[0].items[1].text == "Agentic workflows automate multi-step tasks"
    assert deck.slides[1].blocks[0].items[0].text == "79% of firms use it"
    assert deck.slides[2].blocks[0].items[0].text == "Hallucination is the leading concern"


def test_targets_given_as_labels_become_node_ids():
    deck = _deck()
    assert normalize_targets(deck, ["slide 2", "slide_004", "slide 9", "Risks remain"]) == [
        deck.slides[1].node_id, deck.slides[3].node_id, deck.slides[2].node_id]


def test_editor_sees_numbered_points_with_node_ids():
    text = outline_for_editor(_deck())
    assert "node_id=slide_002" in text and "point 2: Agents automate tasks" in text


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _v1(db, tmp_path: Path) -> tuple[Artifact, DeckModel, Path]:
    deck = _deck()
    photo_path = tmp_path / "photo.jpg"
    Image.new("RGB", (1600, 1000), (90, 120, 160)).save(photo_path)
    photos = {1: PhotoAsset(path=photo_path, credit="Photo: test", landing_url="https://example.org", alt_text="test")}
    out = tmp_path / "v1.pptx"
    render_pptx(deck, TEMPLATE, None, out, photos=photos)
    art = Artifact(title=deck.title, artifact_type="pptx")
    db.add(art)
    db.flush()
    ver = ArtifactVersion(artifact_id=art.id, version_no=1, model_json=deck.model_dump_json(), file_path=str(out),
                          file_type="pptx", change_summary="Initial generation")
    ver.render_json = RenderContext(template_path=str(TEMPLATE), photos=deck_photos_by_node(deck, photos, art.id)).to_json()
    db.add(ver)
    db.commit()
    return art, deck, out


def test_edit_changes_only_the_named_point_and_keeps_template_and_photos(db, tmp_path):
    art, v1, v1_file = _v1(db, tmp_path)
    # The model also rewrites slide 3 although only slide 2 was asked for: the guard must restore it.
    plan = EditPlan(target="pptx", summary="Changed one point on slide 2", scope_node_ids=["slide_002"], ops=[
        UpdatePointOp(slide_index=2, point_index=1, new_text="Four in five firms now use generative AI"),
        UpdateSlideOp(index=3, slide=SlideModel(title="Rewritten without being asked")),
    ])
    with patch("app.agents.editor._generate_edit_plan", return_value=plan), patch("app.agents.editor.validate_outputs"):
        res = edit_artifact(artifact_id=art.id, instruction="On slide 2 change the first point to say four in five firms",
                            db_session=db, target_nodes=["slide 2"])
    v2 = db.query(ArtifactVersion).filter_by(artifact_id=art.id, version_no=2).one()
    new = DeckModel.model_validate_json(v2.model_json)
    assert new.slides[1].blocks[0].items[0].text == "Four in five firms now use generative AI"
    assert new.slides[1].blocks[0].items[1] == v1.slides[1].blocks[0].items[1]
    for i in (0, 2, 3):  # untouched slides identical, cover date not re-stamped
        assert new.slides[i] == v1.slides[i]
    ctx = json.loads(v2.render_json)
    assert ctx["template_path"] == str(TEMPLATE)
    assert set(ctx["photos"]) == {"slide_002"} and Path(ctx["photos"]["slide_002"]["path"]).exists()
    a, b = file_units(v1_file, "pptx"), file_units(v2.file_path, "pptx")
    assert len(a) == len(b) and a[0] == b[0] and a[2] == b[2] and a[3] == b[3] and a[1] != b[1]
    assert res.diff["regression"]["out_of_scope_changed"] == []
