"""Pydantic v2 content model for structured document generation (DOCX)."""

from __future__ import annotations

from typing import Literal, Union
from pydantic import BaseModel, Field

from app.models.blocks import CalloutBlock, ChartBlock, ImageBlock, QuoteBlock
from app.models.style import StyleOverrides


class ParagraphBlock(BaseModel):
    """A standard text paragraph block."""
    type: Literal["paragraph"] = "paragraph"
    node_id: str | None = None
    text: str
    source_ids: list[int] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)


class BulletsBlock(BaseModel):
    """A bulleted or numbered list block."""
    type: Literal["bullets"] = "bullets"
    node_id: str | None = None
    items: list[str] = Field(default_factory=list)
    ordered: bool = False
    source_ids: list[int] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)


class TableBlock(BaseModel):
    """A structured table block."""
    type: Literal["table"] = "table"
    node_id: str | None = None
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    source_ids: list[int] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)


class FigureBlock(BaseModel):
    """A chart or picture with a caption (rendered natively in Stage 9; until then as a data table)."""
    type: Literal["figure"] = "figure"
    node_id: str | None = None
    caption: str = ""
    chart: ChartBlock | None = None
    image: ImageBlock | None = None
    source_ids: list[int] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)


Block = Union[ParagraphBlock, BulletsBlock, TableBlock, QuoteBlock, CalloutBlock, FigureBlock]


class Section(BaseModel):
    """A document section containing a heading and content blocks."""
    node_id: str | None = None
    heading: str
    level: int = Field(default=1, ge=1, le=3)
    purpose: str = Field(default="", description="What the section must achieve (from the document plan)")
    blocks: list[Block] = Field(default_factory=list)


class DocumentModel(BaseModel):
    """Root structured content model for document generation."""
    schema_version: int = 1
    node_id: str | None = None
    title: str
    subtitle: str = ""
    client_name: str = ""
    date: str = ""
    executive_summary: str = ""
    table_of_contents: bool = False
    citation_style: Literal["numeric", "apa", "harvard", "none"] = "numeric"
    style: StyleOverrides | None = None
    sections: list[Section] = Field(default_factory=list)
