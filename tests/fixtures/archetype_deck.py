"""A deck exercising every slide archetype (Stage 8 fixture)."""

from app.models.blocks import (
    CalloutBlock, ChartBlock, ChartSeries, ComparisonBlock, ComparisonColumn, KpiBlock, KpiItem, ProcessBlock,
    ProcessStep, QuoteBlock, TimelineBlock, TimelineEvent,
)
from app.models.deck_model import BulletItem, DeckModel, SlideBulletsBlock, SlideModel
from app.models.document_model import TableBlock


def archetype_deck() -> DeckModel:
    s = [
        SlideModel(role="title", archetype="title", title="Community solar in 2026", subtitle="Briefing for councils"),
        SlideModel(archetype="bullets", title="Shared arrays let renters buy local solar power",
                   blocks=[SlideBulletsBlock(items=[BulletItem(text="Subscribers buy a share of a nearby array", source_ids=[1]),
                                                    BulletItem(text="Credits appear on the normal electricity bill", source_ids=[1]),
                                                    BulletItem(text="No rooftop or upfront purchase is needed")])]),
        SlideModel(archetype="chart", title="Installed capacity grew by 40 percent in two years",
                   blocks=[ChartBlock(chart_type="column", title="Capacity", unit="GW", categories=["2022", "2023", "2024"],
                                      series=[ChartSeries(name="GW", values=[5.6, 6.8, 7.9])], source_ids=[1, 2]),
                           CalloutBlock(text="Growth is concentrated in five states", source_ids=[2])]),
        SlideModel(archetype="kpi", title="Subscribers save money from the first month",
                   blocks=[KpiBlock(items=[KpiItem(value="10 %", label="average bill saving", source_ids=[1]),
                                           KpiItem(value="7.9 GW", label="installed capacity", source_ids=[2]),
                                           KpiItem(value="41", label="states with programmes", source_ids=[3])])]),
        SlideModel(archetype="timeline", title="Policy support arrived in three steps",
                   blocks=[TimelineBlock(events=[TimelineEvent(date="2010", label="First state programme"),
                                                 TimelineEvent(date="2018", label="Low-income carve-outs"),
                                                 TimelineEvent(date="2022", label="Federal tax credit adders"),
                                                 TimelineEvent(date="2024", label="Grid access reforms")], source_ids=[3])]),
        SlideModel(archetype="process", title="A council can launch a programme in four steps",
                   blocks=[ProcessBlock(steps=[ProcessStep(label="Assess", detail="Map roofs and land"),
                                               ProcessStep(label="Partner", detail="Select a developer"),
                                               ProcessStep(label="Enrol", detail="Recruit subscribers"),
                                               ProcessStep(label="Operate", detail="Report savings yearly")])]),
        SlideModel(archetype="comparison", role="two_content", title="Ownership and subscription suit different households",
                   blocks=[ComparisonBlock(columns=[ComparisonColumn(heading="Ownership", points=["Higher long-run savings", "Needs capital"]),
                                                    ComparisonColumn(heading="Subscription", points=["No upfront cost", "Smaller saving"])])]),
        SlideModel(archetype="table", title="Three programme models compared",
                   blocks=[TableBlock(headers=["Model", "Saving", "Risk"], rows=[["Utility-led", "5-10 %", "Low"],
                                                                                ["Developer-led", "10-15 %", "Medium"],
                                                                                ["Co-op", "15 %+", "Higher"]], source_ids=[3])]),
        SlideModel(archetype="quote", role="title_only", title="Residents value local ownership",
                   blocks=[QuoteBlock(text="People want to see where their power comes from", attribution="Programme survey", source_ids=[4])]),
        SlideModel(role="title_only", archetype="closing", title="Questions and next steps", subtitle="Thank you"),
    ]
    return DeckModel(title="Community solar in 2026", slides=s)
