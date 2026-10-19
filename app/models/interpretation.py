"""Interpretation of a user message: request kind, output specification, change request (DATA_MODELS §4.3).

Schema defined in Stage 3; produced by the interpreter from Stage 4. ``ExecutionPlan`` remains the graph's
working plan; ``Interpretation`` is the typed understanding it is derived from.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

RequestKind = Literal[
    "new_task", "continue_task", "modify_artifact", "regenerate", "change_objective",
    "clarification_answer", "question", "retry",
]
CLARIFY_THRESHOLD = 0.6


class LengthSpec(BaseModel):
    unit: Literal["slides", "pages", "words", "sheets", "auto"] = "auto"
    value: int | None = Field(None, ge=1)
    explicit: bool = Field(False, description="True only when the user stated the length")

    @model_validator(mode="after")
    def _explicit_needs_value(self) -> LengthSpec:
        if self.explicit and (self.value is None or self.unit == "auto"):
            raise ValueError("an explicit length needs a unit and a value")
        return self


class OutputSpec(BaseModel):
    format: Literal["pptx", "docx", "xlsx"]
    purpose: str = ""
    audience: str = ""
    tone: str = ""
    language: str = "en"
    detail_level: Literal["brief", "standard", "detailed"] = "standard"
    length: LengthSpec = Field(default_factory=LengthSpec)
    citation_style: Literal["numeric", "apa", "harvard", "none"] = "numeric"
    style_notes: list[str] = Field(default_factory=list)


class TargetRef(BaseModel):
    kind: Literal["slide", "section", "sheet", "element", "table", "chart", "paragraph", "style"]
    locator: str = Field(description="As the user said it, e.g. 'slide 4', 'the revenue chart'")
    node_id: str | None = Field(None, description="Resolved node ID in the artifact")


class ChangeScope(BaseModel):
    level: Literal["artifact", "unit", "element", "style"]
    targets: list[TargetRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unit_scope_needs_target(self) -> ChangeScope:
        if self.level in ("unit", "element") and not self.targets:
            raise ValueError(f"a {self.level}-level change needs at least one target")
        return self


class StyleChange(BaseModel):
    path: str = Field(description="Style property, e.g. 'heading_1.size_pt', 'body.font', 'accent_color'")
    value: str | float | int | bool


class ChangeRequest(BaseModel):
    operation: Literal[
        "restyle", "rewrite", "add", "remove", "reorder", "expand", "condense", "replace_data", "regenerate", "convert",
    ]
    scope: ChangeScope
    instruction: str
    style_changes: list[StyleChange] = Field(default_factory=list)

    @model_validator(mode="after")
    def _restyle_is_explicit(self) -> ChangeRequest:
        if self.operation == "restyle" and not self.style_changes:
            raise ValueError("a restyle names at least one style change")
        return self


class ResearchNeed(BaseModel):
    depth: Literal["none", "light", "standard", "deep"] = "standard"
    rationale: str = ""


class ClarificationQuestion(BaseModel):
    question: str
    options: list[str] = Field(default_factory=list)


class Interpretation(BaseModel):
    schema_version: int = 1
    kind: RequestKind
    confidence: float = Field(ge=0.0, le=1.0)
    objective: str = ""
    output_spec: OutputSpec | None = None
    change: ChangeRequest | None = None
    research: ResearchNeed = Field(default_factory=ResearchNeed)
    clarification_questions: list[ClarificationQuestion] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    referenced_file_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _invariants(self) -> Interpretation:
        if self.kind == "modify_artifact" and self.change is None:
            raise ValueError("modify_artifact needs a change request")
        if self.confidence < CLARIFY_THRESHOLD and not self.clarification_questions:
            raise ValueError("low-confidence interpretations must ask a clarification question")
        return self

    @property
    def needs_clarification(self) -> bool:
        return bool(self.clarification_questions)
