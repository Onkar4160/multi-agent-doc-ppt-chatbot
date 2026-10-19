"""Typed visual content blocks shared by decks and documents (FR-PLAN-6, DATA_MODELS §7.1).

Every block carries ``source_ids`` (citation numbers = source IDs) and ``claim_ids`` (dossier claims it states), so
facts stay traceable from the rendered file back to evidence.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ChartType = Literal["bar", "column", "line", "pie", "doughnut", "area", "scatter", "stacked_bar", "stacked_column"]


class _Traced(BaseModel):
    node_id: str | None = None
    source_ids: list[int] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)


class ChartSeries(BaseModel):
    name: str
    values: list[float] = Field(default_factory=list)


class ChartBlock(_Traced):
    type: Literal["chart"] = "chart"
    chart_type: ChartType = "column"
    title: str = ""
    categories: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)
    unit: str = ""


class TimelineEvent(BaseModel):
    date: str
    label: str
    detail: str = ""


class TimelineBlock(_Traced):
    type: Literal["timeline"] = "timeline"
    events: list[TimelineEvent] = Field(default_factory=list)


class ProcessStep(BaseModel):
    label: str
    detail: str = ""


class ProcessBlock(_Traced):
    type: Literal["process"] = "process"
    steps: list[ProcessStep] = Field(default_factory=list)


class ComparisonColumn(BaseModel):
    heading: str
    points: list[str] = Field(default_factory=list)


class ComparisonBlock(_Traced):
    type: Literal["comparison"] = "comparison"
    columns: list[ComparisonColumn] = Field(default_factory=list)


class KpiItem(BaseModel):
    value: str = Field(description="The figure as shown, e.g. '42 %' or '$1.2 bn'")
    label: str
    delta: str = ""
    source_ids: list[int] = Field(default_factory=list)


class KpiBlock(_Traced):
    type: Literal["kpi"] = "kpi"
    items: list[KpiItem] = Field(default_factory=list)


class QuoteBlock(_Traced):
    type: Literal["quote"] = "quote"
    text: str
    attribution: str = ""


class ImageBlock(_Traced):
    type: Literal["image"] = "image"
    description: str = Field(description="What the picture shows; used to find a photo")
    alt_text: str = ""
    asset_path: str | None = None
    credit: str = ""


class CalloutBlock(_Traced):
    type: Literal["callout"] = "callout"
    text: str
