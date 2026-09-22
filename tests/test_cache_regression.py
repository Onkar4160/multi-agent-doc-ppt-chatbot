"""Regression test for cache directory resolution across services and agents."""

from pathlib import Path
import pytest

from app.models.template_profile import TemplateProfile
from app.models.document_model import DocumentModel, Section, ParagraphBlock
from app.models.deck_model import DeckModel, SlideModel


DOCX_TEMPLATE = Path("data/sample_templates/Company_Proposal.docx")
PPTX_TEMPLATE = Path("data/sample_templates/Company_Template.pptx")


def test_cache_regression_no_name_error(tmp_path: Path):
    """Import every module that previously failed and invoke cache-touching functions, asserting no NameError."""
    try:
        # 1. app/llm/client.py
        import app.llm.client as llm_client
        cache_dir = llm_client.get_cache_dir()
        assert cache_dir.exists()
        search_dir = llm_client.get_search_cache_dir()
        assert search_dir.exists()
        client = llm_client.get_llm_client()
        assert client is not None

        # 2. app/services/context_builder.py
        import app.services.context_builder as context_builder
        ctx = context_builder.build_context("Test brief for regression", use_web=True, use_kb=False)
        assert ctx is not None

        # 3. app/agents/supervisor.py
        import app.agents.supervisor as supervisor
        plan = supervisor.parse_plan("Create a 5-slide deck on AI")
        assert plan is not None

        # 4. app/agents/editor.py
        import app.agents.editor as editor
        editor_client = editor.get_llm_client()
        assert editor_client is not None

        # 5. app/agents/converter.py
        import app.agents.converter as converter
        converter_client = converter.get_llm_client()
        assert converter_client is not None

        # 6. app/services/docx_renderer.py
        import app.services.docx_renderer as docx_renderer
        doc_model = DocumentModel(
            title="Regression Test Doc",
            sections=[Section(heading="Heading", level=1, blocks=[ParagraphBlock(text="Body")])],
        )
        out_docx = tmp_path / "regression.docx"
        docx_renderer.render_docx(doc_model, DOCX_TEMPLATE, TemplateProfile(), out_docx)
        assert out_docx.exists()

        # 7. app/services/pptx_renderer.py
        import app.services.pptx_renderer as pptx_renderer
        deck_model = DeckModel(
            title="Regression Test Deck",
            slides=[SlideModel(title="Intro", bullet_points=[])],
        )
        out_pptx = tmp_path / "regression.pptx"
        pptx_renderer.render_pptx(deck_model, PPTX_TEMPLATE, TemplateProfile(), out_pptx)
        assert out_pptx.exists()

    except NameError as exc:
        pytest.fail(f"Regression test failed with NameError: {exc}")
