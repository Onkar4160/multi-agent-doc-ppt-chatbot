"""Pydantic v2 schemas and ORM models for template analysis & document style profiling."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


# Bump when analysis output changes so cached profiles are recomputed (2: designed sample-slide bases).
ANALYZER_VERSION = 3  # 3: element inventory, layout archetypes, DOCX/XLSX manifests, design tokens


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
    archetype: LayoutArchetype = "other"


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
    manifest: DocxManifest | None = None


class Box(BaseModel):
    """Axis-aligned box in EMU."""
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def area(self) -> int:
        return max(self.w, 0) * max(self.h, 0)

    def intersection_area(self, other: Box) -> int:
        dx = min(self.right, other.right) - max(self.x, other.x)
        dy = min(self.bottom, other.bottom) - max(self.y, other.y)
        return dx * dy if dx > 0 and dy > 0 else 0


class SlideBase(BaseModel):
    """A sample slide reusable as a designed base (template design mode "slides")."""
    index: int
    kind: str = Field(description="cover | section | content | closing | diagram")
    title_shape: int | None = Field(None, description="index in slide.shapes of the title text box")
    subtitle_shape: int | None = None
    remove_shapes: list[int] = Field(default_factory=list, description="sample text/content to drop")
    photo_shapes: list[int] = Field(default_factory=list, description="empty picture placeholders")
    frame_shapes: list[int] = Field(default_factory=list, description="plain light boxes that frame a photo")
    region: Box | None = Field(None, description="content area freed by the removed sample text")
    score: float = 0.0
    title_text: str = ""
    region_color: str | None = Field(None, description="colour behind the content region")


class SlideDesign(BaseModel):
    """Where a PPTX template keeps its design: on layouts/masters, or on the sample slides (Canva style)."""
    slide_width: int = 0  # EMU
    slide_height: int = 0  # EMU
    design_mode: str = Field("layouts", description="layouts | slides (artwork lives on sample slides)")
    slide_bases: list[SlideBase] = Field(default_factory=list)
    slide_fonts: dict[str, str] = Field(default_factory=dict, description="heading/body fonts used on slides")
    slide_palette: list[str] = Field(default_factory=list, description="accent colours taken from the artwork")
    slide_text_color: str | None = None
    slide_background: str | None = None
    theme_colors: dict[str, str] = Field(default_factory=dict, description="theme scheme slots (dk1, accent1, ...)")
    theme_fonts: dict[str, str] = Field(default_factory=dict, description="major/minor theme fonts")


ElementRole = Literal["structural", "branding", "decorative", "placeholder", "sample_content"]
LayoutArchetype = Literal[
    "title", "section", "bullets", "two_column", "comparison", "image_text", "title_only", "quote", "timeline",
    "closing", "blank", "other",
]


class ElementInfo(BaseModel):
    """One shape on a sample slide (or layout/master), with the role it plays in the template (FR-TPL-2/3)."""
    shape_index: int
    name: str
    kind: str = Field(description="text | picture | placeholder | shape | freeform | group | table | chart | line | other")
    role: ElementRole
    box: Box | None = None
    z: int = Field(0, description="Stacking order on the slide (0 = backmost)")
    placeholder_type: str | None = None
    text_preview: str = ""
    font: FontInfo | None = None
    fill_color: str | None = None
    has_image: bool = False
    reason: str = ""


class SlideInventory(BaseModel):
    index: int
    layout_name: str
    elements: list[ElementInfo] = Field(default_factory=list)


class DesignTokens(BaseModel):
    """Reusable design characteristics derived from a template (FR-TPL-7)."""
    heading_font: str | None = None
    body_font: str | None = None
    primary: str | None = None  # hex without '#'
    secondary: str | None = None
    accents: list[str] = Field(default_factory=list)
    text: str | None = None
    background: str | None = None
    source: str = Field("theme", description="theme | slides | styles | sheet")


class DocxSection(BaseModel):
    orientation: str = "portrait"
    page_width_in: float | None = None
    page_height_in: float | None = None
    margins_in: dict[str, float] = Field(default_factory=dict)
    header_text: str = ""
    footer_text: str = ""
    has_page_number: bool = False
    different_first_page: bool = False


class DocxStyleInfo(BaseModel):
    style_name: str
    font: str | None = None
    size_pt: float | None = None
    bold: bool | None = None
    color: str | None = None


class DocxManifest(BaseModel):
    """Structure of a Word template beyond its text (FR-TPL-5)."""
    sections: list[DocxSection] = Field(default_factory=list)
    styles_by_role: dict[str, DocxStyleInfo] = Field(default_factory=dict)
    list_styles_used: list[str] = Field(default_factory=list)
    numbering_definitions: int = 0
    cover_lines: list[str] = Field(default_factory=list, description="Text before the first heading")
    cover_images: int = 0
    tables: list[dict[str, Any]] = Field(default_factory=list)
    has_toc: bool = False
    body_paragraphs: int = 0
    images: int = 0
    theme_colors: dict[str, str] = Field(default_factory=dict)
    theme_fonts: dict[str, str] = Field(default_factory=dict)


class XlsxSheetInfo(BaseModel):
    name: str
    role: str = Field("data", description="readme | assumptions | data | calculations | report | sources | other")
    visible: bool = True
    used_range: str = ""
    max_row: int = 0
    max_col: int = 0
    header: list[str] = Field(default_factory=list)
    freeze_panes: str | None = None
    tables: list[dict[str, Any]] = Field(default_factory=list)
    formula_count: int = 0
    formula_samples: list[str] = Field(default_factory=list)
    references_sheets: list[str] = Field(default_factory=list, description="Sheets its formulas read from")
    number_formats: list[str] = Field(default_factory=list)
    conditional_formats: int = 0
    data_validations: int = 0
    merged_ranges: int = 0
    charts: list[dict[str, Any]] = Field(default_factory=list)
    images: int = 0


class XlsxManifest(BaseModel):
    """Structure of an Excel template (FR-TPL-6)."""
    sheets: list[XlsxSheetInfo] = Field(default_factory=list)
    defined_names: dict[str, str] = Field(default_factory=dict)
    theme_colors: dict[str, str] = Field(default_factory=dict)


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
    slide_design: SlideDesign | None = None
    inventory: list[SlideInventory] = Field(default_factory=list, description="Every element of every sample slide with its role")
    branding: list[ElementInfo] = Field(default_factory=list, description="Logos and repeated brand text to keep on new slides")


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
    analyzer_version: int = 0  # profiles cached before versioning read as 0 (stale)
    xlsx_style: XlsxManifest | None = None
    design_tokens: DesignTokens | None = None
    sha256: str | None = None


def is_current_profile(profile_json: str | None) -> bool:
    """True when a cached profile JSON was produced by the current analyzer version."""
    if not profile_json:
        return False
    try:
        import json

        return int(json.loads(profile_json).get("analyzer_version", 0)) >= ANALYZER_VERSION
    except (ValueError, TypeError, AttributeError):
        return False


# ──────── SQLAlchemy ORM Model ─────────────────────────────────────────────

class TemplateProfileRecord(Base, TimestampMixin):
    """Database record persisting a TemplateProfile JSON string for an uploaded file."""

    __tablename__ = "template_profile_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_json: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # cache key with analyzer_version
    analyzer_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
