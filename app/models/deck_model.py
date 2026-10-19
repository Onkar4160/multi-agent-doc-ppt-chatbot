"""Pydantic v2 content model for structured presentation deck generation (PPTX)."""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field

from app.models.blocks import (
    CalloutBlock, ChartBlock, ComparisonBlock, ImageBlock, KpiBlock, ProcessBlock, QuoteBlock, TimelineBlock,
)
from app.models.document_model import TableBlock
from app.models.style import StyleOverrides


class BulletItem(BaseModel):
    """Single bullet item with nesting level and citation source IDs."""
    node_id: str | None = None
    text: str
    level: int = Field(default=0, ge=0, le=1)
    source_ids: list[int] = Field(default_factory=list)


SlideRole = Literal["title", "section_header", "title_content", "two_content", "title_only"]

Archetype = Literal[
    "title", "agenda", "section", "bullets", "two_column", "comparison", "timeline", "process", "chart", "table",
    "kpi", "quote", "image_text", "summary", "closing", "references",
]


class SlideBulletsBlock(BaseModel):
    type: Literal["bullets"] = "bullets"
    node_id: str | None = None
    items: list[BulletItem] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)


SlideBlock = Union[
    SlideBulletsBlock, TableBlock, ChartBlock, TimelineBlock, ProcessBlock, ComparisonBlock, KpiBlock, QuoteBlock,
    ImageBlock, CalloutBlock,
]


class SlideModel(BaseModel):
    """Model representing a single PowerPoint slide."""
    node_id: str | None = None
    role: SlideRole = "title_content"
    title: str
    subtitle: str = ""
    bullets: list[BulletItem] = Field(default_factory=list)
    left: list[BulletItem] = Field(default_factory=list)
    right: list[BulletItem] = Field(default_factory=list)
    notes: str = ""
    source_ids: list[int] = Field(default_factory=list)
    # Stage 7 (DATA_MODELS §7.1): the plan's intent and typed content; bullets/left/right stay for compatibility.
    archetype: Archetype | None = None
    key_message: str = Field(default="", description="The one sentence the slide must land")
    blocks: list[SlideBlock] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    layout_hint: str | None = None


class DeckModel(BaseModel):
    """Root structured content model for presentation deck generation."""
    schema_version: int = 1
    node_id: str | None = None
    title: str
    subtitle: str = ""
    style: StyleOverrides | None = None
    slides: list[SlideModel] = Field(default_factory=list)
