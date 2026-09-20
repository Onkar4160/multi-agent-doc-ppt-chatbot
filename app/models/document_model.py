"""Pydantic v2 content model for structured document generation (DOCX)."""

from __future__ import annotations

from typing import Literal, Union
from pydantic import BaseModel, Field


class ParagraphBlock(BaseModel):
    """A standard text paragraph block."""
    type: Literal["paragraph"] = "paragraph"
    text: str
    source_ids: list[int] = Field(default_factory=list)


class BulletsBlock(BaseModel):
    """A bulleted or numbered list block."""
    type: Literal["bullets"] = "bullets"
    items: list[str] = Field(default_factory=list)
    ordered: bool = False
    source_ids: list[int] = Field(default_factory=list)


class TableBlock(BaseModel):
    """A structured table block."""
    type: Literal["table"] = "table"
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    source_ids: list[int] = Field(default_factory=list)


Block = Union[ParagraphBlock, BulletsBlock, TableBlock]


class Section(BaseModel):
    """A document section containing a heading and content blocks."""
    heading: str
    level: int = Field(default=1, ge=1, le=3)
    blocks: list[Block] = Field(default_factory=list)


class DocumentModel(BaseModel):
    """Root structured content model for document generation."""
    title: str
    subtitle: str = ""
    client_name: str = ""
    date: str = ""
    sections: list[Section] = Field(default_factory=list)
