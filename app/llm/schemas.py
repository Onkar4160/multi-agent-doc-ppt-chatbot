"""Pydantic schemas for structured LLM output across all agents."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── Template Analysis ────────────────────────────────────


class SlideLayoutInfo(BaseModel):
    """Describes one slide layout from a PPTX template."""
    name: str = Field(description="Layout name, e.g. 'Title Slide'")
    index: int = Field(description="Index in the slide master")
    placeholders: list[PlaceholderInfo] = Field(default_factory=list)


class PlaceholderInfo(BaseModel):
    """A single placeholder within a slide layout."""
    idx: int = Field(description="Placeholder index")
    name: str = Field(default="", description="Placeholder name")
    type: str = Field(default="body", description="title|body|picture|chart|table")
    width_pct: float | None = Field(default=None, description="Width as % of slide")
    height_pct: float | None = Field(default=None, description="Height as % of slide")


class TemplateProfileSchema(BaseModel):
    """Extracted style profile from a template document."""
    fonts: list[str] = Field(default_factory=list, description="Font families used")
    font_sizes: dict[str, float] = Field(default_factory=dict, description="Role→pt mapping, e.g. heading1→28")
    colors: dict[str, str] = Field(default_factory=dict, description="Role→hex color, e.g. primary→#1A2B3C")
    heading_styles: list[str] = Field(default_factory=list, description="Heading style names or levels")
    margins: dict[str, float] = Field(default_factory=dict, description="Margin name→inches")
    slide_layouts: list[SlideLayoutInfo] = Field(default_factory=list, description="PPTX layouts")
    tone: str = Field(default="professional", description="Detected tone/voice")
    section_patterns: list[str] = Field(default_factory=list, description="Ordered section types, e.g. intro,body,conclusion")
    bullet_style: str = Field(default="•", description="Bullet character or style")
    line_spacing: float = Field(default=1.15, description="Line spacing multiplier")


# ── Document Model ───────────────────────────────────────


class CitedFact(BaseModel):
    """A single fact with its source reference."""
    text: str = Field(description="The factual statement")
    source_id: int | None = Field(default=None, description="FK to Source table")


class DocumentSection(BaseModel):
    """One section of a generated document."""
    heading: str = Field(description="Section heading text")
    heading_level: int = Field(default=1, description="Heading level 1-4")
    body: str = Field(description="Section body with [n] citation markers")
    citations: list[CitedFact] = Field(default_factory=list)


class DocumentModel(BaseModel):
    """Full structured model for a DOCX document."""
    title: str = Field(description="Document title")
    sections: list[DocumentSection] = Field(default_factory=list)
    sources_section: list[dict] = Field(default_factory=list, description="[{id, label, url}]")


# ── Deck (PPTX) Model ───────────────────────────────────


class SlideContent(BaseModel):
    """Content for a single slide."""
    layout_name: str = Field(description="Name of the slide layout to use")
    placeholders: dict[str, str] = Field(default_factory=dict, description="placeholder_name→content")
    speaker_notes: str = Field(default="", description="Speaker notes for this slide")
    citations: list[CitedFact] = Field(default_factory=list)


class DeckModel(BaseModel):
    """Full structured model for a PPTX presentation."""
    title: str = Field(description="Presentation title")
    slides: list[SlideContent] = Field(default_factory=list)
    sources_slide: list[dict] = Field(default_factory=list, description="[{id, label, url}]")


# ── Research ─────────────────────────────────────────────


class ResearchFact(BaseModel):
    """A researched fact with source tracking."""
    text: str
    source_url: str = ""
    source_title: str = ""


class ResearchResult(BaseModel):
    """Output of the research agent."""
    query: str
    facts: list[ResearchFact] = Field(default_factory=list)


# ── Editing ──────────────────────────────────────────────


class EditInstruction(BaseModel):
    """A single edit operation on a document/deck model."""
    target: str = Field(description="Section heading or slide index to modify")
    operation: str = Field(description="replace|insert_after|delete|append")
    new_content: str = Field(default="", description="New text content")


# ── Supervisor routing ───────────────────────────────────


class SupervisorDecision(BaseModel):
    """Supervisor's routing decision."""
    next_step: str = Field(description="researcher|kb_retriever|generator|editor|FINISH")
    reasoning: str = Field(default="", description="Brief explanation of the routing decision")


# Rebuild forward references for SlideLayoutInfo
SlideLayoutInfo.model_rebuild()
