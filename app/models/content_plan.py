"""Content plans: the information architecture decided before any prose is written (FR-PLAN-1..7, ADR-028).

A plan says what each unit (slide, section, sheet) is for, which claims it rests on and how long it may be. Writers
fill a validated plan; validators compare plans and written content against it.
"""

from __future__ import annotations

from typing import Literal

from typing import get_args

from pydantic import BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from app.models.deck_model import Archetype

BlockKind = Literal[
    "bullets", "paragraph", "table", "chart", "timeline", "process", "comparison", "kpi", "quote", "image", "callout",
]

# What each archetype is made of (archetype–content fit, FR-PLAN-7). The first kind is the main block.
ARCHETYPE_BLOCKS: dict[str, tuple[str, ...]] = {
    "title": (), "section": (), "closing": (), "references": (),
    "agenda": ("bullets",), "bullets": ("bullets",), "summary": ("bullets", "callout"),
    "two_column": ("comparison", "bullets"), "comparison": ("comparison", "table"),
    "timeline": ("timeline",), "process": ("process",), "chart": ("chart", "callout"), "table": ("table",),
    "kpi": ("kpi", "callout"), "quote": ("quote",), "image_text": ("image", "bullets"),
}
# Archetypes whose main block needs figures from the evidence.
NUMERIC_ARCHETYPES = {"chart", "kpi"}
DATED_ARCHETYPES = {"timeline"}


class LengthTarget(BaseModel):
    """Resolved length: explicit user requirement or content need, never the template's length (FR-PLAN-5)."""
    unit: Literal["slides", "words", "sheets"]
    value: int = Field(ge=1)
    explicit: bool = False
    tolerance_pct: float = Field(0.0, description="Allowed deviation; 0 = exact (slides, sheets)")
    rationale: str = ""


class SlidePlan(BaseModel):
    purpose: str = Field(description="Why this slide exists in the narrative")
    archetype: Archetype = "bullets"
    headline: str = Field(description="Assertion headline: a full statement, not a topic label")
    key_message: str = Field(default="", description="The one sentence the audience must remember")
    blocks: list[BlockKind] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    source_ids: list[int] = Field(default_factory=list)
    notes_intent: str = Field(default="", description="What the speaker adds beyond the slide")

    @field_validator("archetype", mode="before")
    @classmethod
    def _known_archetype(cls, v: object) -> object:
        """An unknown archetype from the model becomes bullets instead of discarding the whole plan."""
        allowed = set(get_args(Archetype))
        v = str(v or "bullets").strip().lower().replace("-", "_").replace(" ", "_")
        return v if v in allowed else {"two_columns": "two_column", "kpis": "kpi", "image": "image_text"}.get(v, "bullets")

    @field_validator("blocks", mode="before")
    @classmethod
    def _known_blocks(cls, v: object) -> object:
        allowed = set(get_args(BlockKind))
        return [b for b in (v or []) if str(b) in allowed]


class DeckPlan(BaseModel):
    schema_version: SkipJsonSchema[int] = 1
    title: str
    subtitle: str = ""
    audience: str = ""
    narrative: list[str] = Field(default_factory=list, description="The arc, e.g. context → problem → evidence → recommendation")
    slides: list[SlidePlan] = Field(default_factory=list)
    length: SkipJsonSchema[LengthTarget | None] = None  # set by the planner, not the model
    origin: SkipJsonSchema[Literal["llm", "offline", "fallback"]] = "llm"


class SectionPlan(BaseModel):
    heading: str
    level: int = Field(1, ge=1, le=3)
    purpose: str = ""
    argument: str = Field(default="", description="The point the section makes")
    blocks: list[BlockKind] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    source_ids: list[int] = Field(default_factory=list)
    target_words: int = Field(150, ge=0)

    @field_validator("blocks", mode="before")
    @classmethod
    def _known_blocks(cls, v: object) -> object:
        allowed = set(get_args(BlockKind))
        return [b for b in (v or []) if str(b) in allowed]


class DocumentPlan(BaseModel):
    schema_version: SkipJsonSchema[int] = 1
    title: str
    subtitle: str = ""
    audience: str = ""
    executive_summary: bool = True
    summary_words: int = Field(0, ge=0, description="Word budget of the executive summary (part of the length target)")
    table_of_contents: bool = False
    references: bool = True
    sections: list[SectionPlan] = Field(default_factory=list)
    length: SkipJsonSchema[LengthTarget | None] = None  # set by the planner, not the model
    origin: SkipJsonSchema[Literal["llm", "offline", "fallback"]] = "llm"


class ColumnPlan(BaseModel):
    name: str
    type: Literal["text", "integer", "number", "currency", "percent", "date"] = "text"
    unit: str = ""
    number_format: str = ""
    formula: str | None = Field(None, description="Column formula pattern, e.g. '=B{r}*C{r}'")

    @field_validator("type", mode="before")
    @classmethod
    def _known_type(cls, v: object) -> object:
        v = str(v or "text").lower()
        return v if v in ("text", "integer", "number", "currency", "percent", "date") else (
            "number" if v in ("float", "decimal", "numeric") else "integer" if v == "int" else "text")


class SheetPlan(BaseModel):
    name: str
    role: Literal["readme", "assumptions", "data", "calculations", "report", "dashboard", "sources", "other"]
    purpose: str = ""
    columns: list[ColumnPlan] = Field(default_factory=list)
    reads: list[str] = Field(default_factory=list, description="Sheets this sheet's formulas read")
    charts: list[str] = Field(default_factory=list, description="Chart descriptions, e.g. 'column: revenue by month'")
    claim_ids: list[str] = Field(default_factory=list)
    source_ids: list[int] = Field(default_factory=list)


class AssumptionPlan(BaseModel):
    name: str = Field(description="Defined name, e.g. growth_rate")
    value: float
    unit: str = ""
    source_id: int | None = None
    rationale: str = Field(default="", description="Required when no source supports the value")


class CheckPlan(BaseModel):
    description: str
    ref: str = ""
    expected: float | None = None
    tolerance: float = 0.01


class WorkbookPlan(BaseModel):
    schema_version: SkipJsonSchema[int] = 1
    title: str
    sheets: list[SheetPlan] = Field(default_factory=list)
    assumptions: list[AssumptionPlan] = Field(default_factory=list)
    named_ranges: list[str] = Field(default_factory=list)
    checks: list[CheckPlan] = Field(default_factory=list)
    length: SkipJsonSchema[LengthTarget | None] = None  # set by the planner, not the model
    origin: SkipJsonSchema[Literal["llm", "offline", "fallback"]] = "llm"


class PlanIssue(BaseModel):
    code: Literal[
        "duplicate", "empty_unit", "unknown_claim", "unknown_source", "unsupported_claim", "uncited_unit",
        "length", "archetype_fit", "off_brief", "missing_role", "assumption_unsourced", "structure",
    ]
    severity: Literal["critical", "major", "minor"]
    unit: str = Field(description="'slide 3', 'section 2', 'sheet Data' or 'plan'")
    message: str


class PlanReport(BaseModel):
    """Result of validating a plan or the content written from it."""
    kind: Literal["deck", "document", "workbook"]
    stage: Literal["plan", "content"] = "plan"
    issues: list[PlanIssue] = Field(default_factory=list)
    claim_coverage_pct: float = Field(100.0, description="AC-5: share of planned claims with evidence or a non-fact status")
    length_ok: bool = True
    revised: bool = False
    repairs: list[str] = Field(default_factory=list)

    @property
    def blocking(self) -> list[PlanIssue]:
        return [i for i in self.issues if i.severity in ("critical", "major")]
