"""Fixes from live testing: encyclopedia sources, follow-up searches, honest conflicts, templates not used as
evidence, clean topics, varied slide types, retries limited to the failing format, request numbers not flagged."""

from __future__ import annotations

from app.agents.planner import offline_deck_plan, topic_of
from app.agents.research_pipeline import figure_conflicts, run_research
from app.models.content_plan import LengthTarget
from app.models.evidence import Claim, EvidenceItem
from app.services.encyclopedia import relevant
from app.services.plan_inputs import ClaimRef
from app.services.source_registry import SourceRegistry


def test_topic_of_strips_requests_and_politeness():
    assert topic_of("can you  make the presentation and report on the Attack 26/11") == "Attack 26/11"
    assert topic_of("can you also give me report in detail on Sachin Tendulkar") == "Sachin Tendulkar"
    assert topic_of('create a  report on the "Semiconductor in 2026"') == "Semiconductor in 2026"
    assert topic_of("Make a deck for councils on solar power") == "solar power"


def test_encyclopedia_relevance_rejects_wrong_or_creative_works():
    assert relevant("Sachin Tendulkar", "Sachin Tendulkar", "Sachin Ramesh Tendulkar is an Indian former cricketer")
    assert not relevant("Gen AI in 2026", "Mistral AI", "Mistral AI is a company")
    assert not relevant("Attack 26/11", "The Attacks of 26/11", "The Attacks of 26/11 is a 2013 Indian Hindi-language action thriller film")
    assert not relevant("Semiconductor in 2026", "Semiconductor industry", "The semiconductor industry is ...")


def _search(query, today, n):
    return [{"url": f"https://news{abs(hash(query)) % 97}.example.org/a", "title": "News", "snippet": f"Coverage of {query} with detail."}]


def test_encyclopedia_article_is_read_deeply_and_follow_up_searches_fill_gaps():
    calls = []

    def search(query, today, n):
        calls.append(query)
        return _search(query, today, n)

    article = {"title": "Sachin Tendulkar", "url": "https://en.wikipedia.org/wiki/Sachin_Tendulkar",
               "text": "Sachin Tendulkar is an Indian former cricketer. " * 40 + "He scored 100 international centuries in 2012."}
    d = run_research("Sachin Tendulkar", "standard", SourceRegistry(), search=search, scholarly=lambda q, t: [],
                     fetch=None, encyclopedia=lambda s: article)
    assert "encyclopedia" in d.providers_used
    assert any("follow-up" in p for p in d.providers_used) or len(calls) >= 4


def test_conflicts_need_the_same_metric():
    ev = [EvidenceItem(id="e1", source_id=1, statement="Tendulkar scored 15921 runs in Test cricket", value=15921, unit="runs"),
          EvidenceItem(id="e2", source_id=2, statement="Tendulkar scored 18426 runs in ODI cricket over his long career", value=18426, unit="runs"),
          EvidenceItem(id="e3", source_id=3, statement="Tendulkar scored 15921 runs in Test cricket matches", value=10000, unit="runs")]
    claims = [Claim(id=f"c{i}", statement="x", status="supported", evidence_ids=[e.id]) for i, e in enumerate(ev, 1)]
    notes = [c.note for c in figure_conflicts(ev, claims)]
    assert len(notes) == 1 and "source 1" in notes[0] and "source 3" in notes[0]  # Test vs Test only


def test_template_files_are_not_research_sources(monkeypatch):
    from app.agents import graph

    seen = {}

    def fake_build_context(topic, **kw):
        seen["file_ids"] = kw.get("file_ids")
        raise RuntimeError("stop")

    monkeypatch.setattr(graph, "build_context", fake_build_context)
    state = {"plan": {"topic": "t", "source_file_ids": [3, 3, 7], "template_file_ids": [3], "ppt_template_file_id": 3}}
    try:
        graph.node_gather_context.__wrapped__(state) if hasattr(graph.node_gather_context, "__wrapped__") else graph.node_gather_context(state)
    except Exception:
        pass
    assert seen["file_ids"] == [7]


def test_one_visual_type_cannot_take_over_the_deck():
    claims = [ClaimRef(id=f"C{i}", statement=f"Metric {i} reached {i * 3} units", source_ids=[1], question_ids=[f"Q{i}"],
                       value=float(i * 3), unit=f"u{i}") for i in range(1, 7)]
    plan = offline_deck_plan("deck on metrics", claims, LengthTarget(unit="slides", value=9, explicit=True))
    kinds = [s.archetype for s in plan.slides]
    assert kinds.count("kpi") <= 2, kinds


def test_numbers_from_the_request_are_not_uncited_facts():
    from app.agents.validator import _uncited_figure

    assert not _uncited_figure("The 26/11 attacks shook Mumbai", "report on the Attack 26/11")
    assert _uncited_figure("166 people died", "report on the Attack 26/11")


def test_retry_regenerates_only_the_failing_format():
    from app.agents.graph import _keep_existing, should_retry

    state = {"plan": {"outputs": ["docx", "pptx"]}, "retry_count": 0, "doc_model": {"x": 1}, "deck_model": {"y": 1},
             "validation": {"passed": False, "issues": [{"where": "pptx:slide_2", "severity": "error", "message": "m"}]}}
    assert should_retry(state) == "generate_deck_node"
    assert _keep_existing(state, "docx", "doc_model") and not _keep_existing(state, "pptx", "deck_model")
