"""Pydantic v2 schemas and ORM models for template analysis & document style profiling."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field
from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


# ──────── Pydantic v2 Schemas ──────────────────────────────────────────────

class FontInfo(BaseModel):
    """Font metadata for headings and body text."""
    name: str | None = None
    size_pt: float | None = None
    bold: bool | None = None
    italic: bool | None = None
    color_hex: str | None = None


class PlaceholderInfo(BaseModel):
    """Placeholder shape information in a slide layout."""
    idx: int
    type: str
    name: str
    left: float | None = None
    top: float | None = None
    width: float | None = None
    height: float | None = None
    font_size_pt: float | None = None


SlideRole = Literal["title", "section_header", "title_content", "two_content", "title_only", "blank", "other"]


class SlideLayoutInfo(BaseModel):
    """Metadata for a PowerPoint slide layout master."""
    index: int
    name: str
    role: SlideRole = "other"
    placeholders: list[PlaceholderInfo] = Field(default_factory=list)
    decoration_score: int = 0
    role_candidates: list[str] = Field(default_factory=list)


class ToneProfile(BaseModel):
    """Linguistic and stylistic tone analysis of template text."""
    formality: str = "formal"
    voice: str = "authoritative"
    person: str = "first_person_plural"
    avg_sentence_length: float = 15.0
    style_notes: list[str] = Field(default_factory=list)
    typical_openings: list[str] = Field(default_factory=list)


class DocStyleProfile(BaseModel):
    """Visual style metadata extracted from DOCX, PDF, or image documents."""
    page_size: str = "A4"
    margins: dict[str, float] = Field(default_factory=dict)
    heading_styles: dict[str, FontInfo] = Field(default_factory=dict)
    body_font: FontInfo = Field(default_factory=FontInfo)
    line_spacing: float = 1.15
    list_styles: list[str] = Field(default_factory=list)
    table_style_summary: str = ""
    header_text: str | None = None
    footer_text: str | None = None
    palette: list[str] = Field(default_factory=list)
    outline: list[dict[str, Any]] = Field(default_factory=list)


class PptStyleProfile(BaseModel):
    """Theme and layout metadata extracted from PPTX presentations."""
    slide_width: float = 10.0
    slide_height: float = 5.625
    theme_fonts: dict[str, str] = Field(default_factory=dict)  # {"major": "Calibri", "minor": "Calibri"}
    theme_colors: dict[str, str] = Field(default_factory=dict)
    layouts: list[SlideLayoutInfo] = Field(default_factory=list)
    layout_roles: dict[str, int] = Field(default_factory=dict)  # {"title": 0, "title_content": 1, ...}
    existing_slides: list[dict[str, Any]] = Field(default_factory=list)
    avg_words_per_slide: float = 0.0


class TemplateProfile(BaseModel):
    """Comprehensive template analysis result."""
    file_id: int | str | None = None
    file_type: str = ""
    source_name: str = ""
    doc_style: DocStyleProfile | None = None
    ppt_style: PptStyleProfile | None = None
    tone: ToneProfile = Field(default_factory=ToneProfile)
    content_summary: str = ""
    text_preview: str = ""


# ──────── SQLAlchemy ORM Model ─────────────────────────────────────────────

class TemplateProfileRecord(Base, TimestampMixin):
    """Database record persisting a TemplateProfile JSON string for an uploaded file."""

    __tablename__ = "template_profile_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_json: Mapped[str] = mapped_column(Text, nullable=False)
