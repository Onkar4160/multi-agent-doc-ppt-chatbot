"""Pydantic v2 content model for structured presentation deck generation (PPTX)."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class BulletItem(BaseModel):
    """Single bullet item with nesting level and citation source IDs."""
    text: str
    level: int = Field(default=0, ge=0, le=1)
    source_ids: list[int] = Field(default_factory=list)


SlideRole = Literal["title", "section_header", "title_content", "two_content", "title_only"]


class SlideModel(BaseModel):
    """Model representing a single PowerPoint slide."""
    role: SlideRole = "title_content"
    title: str
    subtitle: str = ""
    bullets: list[BulletItem] = Field(default_factory=list)
    left: list[BulletItem] = Field(default_factory=list)
    right: list[BulletItem] = Field(default_factory=list)
    notes: str = ""
    source_ids: list[int] = Field(default_factory=list)


class DeckModel(BaseModel):
    """Root structured content model for presentation deck generation."""
    title: str
    slides: list[SlideModel] = Field(default_factory=list)
