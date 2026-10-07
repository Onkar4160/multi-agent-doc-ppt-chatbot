"""Strongly typed ExecutionPlan schema and taxonomy for Part 1 Intelligence & Context Layer."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


class OperationType(str, Enum):
    """Operation taxonomy for user requests."""

    CREATE = "CREATE"
    EDIT = "EDIT"
    REGENERATE = "REGENERATE"
    APPEND = "APPEND"
    INSERT = "INSERT"
    DELETE = "DELETE"
    REPLACE = "REPLACE"
    MOVE = "MOVE"
    RESTYLE = "RESTYLE"
    REFRESH = "REFRESH"
    CONVERT = "CONVERT"
    ANSWER = "ANSWER"


class ArtifactType(str, Enum):
    """Supported artifact formats."""

    DOCX = "DOCX"
    PPTX = "PPTX"
    XLSX = "XLSX"
    UNKNOWN = "UNKNOWN"


class TemplateMode(str, Enum):
    """Template handling mode."""

    UPLOADED = "UPLOADED"
    DEFAULT = "DEFAULT"
    EXISTING = "EXISTING"
    NONE = "NONE"


class ExecutionPlan(BaseModel):
    """Strongly typed execution plan produced by the Intent & Context Layer."""

    # 1. Primary Taxonomy & Intent Fields
    operation: OperationType = Field(
        default=OperationType.CREATE,
        description="Classified user operation taxonomy.",
    )
    artifact_types: list[ArtifactType] = Field(
        default_factory=lambda: [ArtifactType.DOCX, ArtifactType.PPTX],
        description="Classified target artifact formats (DOCX, PPTX, XLSX, UNKNOWN).",
    )
    target_artifact_id: int | None = Field(
        default=None,
        description="ID of the target artifact to edit, query, or regenerate.",
    )
    target_version_id: int | None = Field(
        default=None,
        description="Specific version ID if targeted.",
    )
    target_nodes: list[str] = Field(
        default_factory=list,
        description="Target sections, slides, cells or positions (e.g. ['slide 5'], ['executive summary']).",
    )
    template_mode: TemplateMode = Field(
        default=TemplateMode.DEFAULT,
        description="Template resolution mode (UPLOADED, DEFAULT, EXISTING, NONE).",
    )
    template_file_ids: list[int] = Field(
        default_factory=list,
        description="Resolved template file IDs from uploaded files or workspace.",
    )
    source_file_ids: list[int] = Field(
        default_factory=list,
        description="Explicitly uploaded source file IDs for content grounding.",
    )
    source_references: list[str] = Field(
        default_factory=list,
        description="Textual references to source files (e.g. ['uploaded PDF', 'attached report']).",
    )
    use_web: bool = Field(
        default=False,
        description="Whether fresh external web research is required.",
    )
    use_rag: bool = Field(
        default=False,
        description="Whether internal enterprise knowledge base retrieval is required.",
    )
    research_query: str = Field(
        default="",
        description="Formulated research query if web or RAG search is needed.",
    )
    document_type: str = Field(
        default="generic",
        description="Classified document type ('proposal', 'research_report', 'company_profile', 'market_analysis', or 'generic').",
    )
    slide_count: int = Field(
        default=12,
        description="Target slide count if a presentation is requested.",
    )
    output_requirements: dict[str, Any] = Field(
        default_factory=dict,
        description="Extensible output constraints (tone, layout, word count, styling).",
    )
    edit_scope: str = Field(
        default="",
        description="Scope of modification (e.g. 'slide 5', 'executive summary', 'append_end', 'full').",
    )
    preserve_constraints: list[str] = Field(
        default_factory=list,
        description="Constraints that downstream agents must preserve (e.g. ['preserve_existing_content', 'preserve_style']).",
    )
    user_instruction: str = Field(
        default="",
        description="Standalone rewritten instruction with conversational references resolved.",
    )
    confidence: float = Field(
        default=1.0,
        description="Confidence score in the intent resolution (0.0 to 1.0).",
    )
    reasoning: str = Field(
        default="",
        description="Structured rationale for intent classification and reference resolution.",
    )

    # 2. Backward Compatibility Fields (mirroring legacy Plan in supervisor.py)
    action: Literal["generate", "edit", "convert", "answer"] = Field(
        default="generate",
        description="[Compatibility] Legacy action string.",
    )
    outputs: list[Literal["docx", "pptx"]] = Field(
        default_factory=lambda: ["docx", "pptx"],
        description="[Compatibility] Legacy target formats ('docx', 'pptx').",
    )
    topic: str = Field(
        default="",
        description="[Compatibility] Topic or user context.",
    )
    use_kb: bool = Field(
        default=False,
        description="[Compatibility] Alias for use_rag.",
    )
    doc_template_file_id: int | None = Field(
        default=None,
        description="[Compatibility] Uploaded DOCX template file ID.",
    )
    ppt_template_file_id: int | None = Field(
        default=None,
        description="[Compatibility] Uploaded PPTX template file ID.",
    )
    resolved_message: str = Field(
        default="",
        description="[Compatibility] Resolved user message.",
    )
    target_artifact_ids: list[int] = Field(
        default_factory=list,
        description="[Compatibility] Target artifact IDs list.",
    )
    answer_scope: Literal["document", "fresh"] | None = Field(
        default=None,
        description="[Compatibility] Q&A answer scope.",
    )
    needs_new_facts: bool = Field(
        default=False,
        description="[Compatibility] True for edits that expand topic/facts.",
    )
    error: str | None = Field(
        default=None,
        description="Error or clarification state (e.g. TEMPLATE_FILE_REQUIRED, AMBIGUOUS_TEMPLATES).",
    )


    @model_validator(mode="after")
    def sync_compatibility_fields(self) -> ExecutionPlan:
        """Keep legacy fields synchronized with new strongly typed fields."""
        # 1. Sync action from operation
        if self.operation in (OperationType.CREATE, OperationType.REGENERATE):
            self.action = "generate"
        elif self.operation == OperationType.CONVERT:
            self.action = "convert"
        elif self.operation == OperationType.ANSWER:
            self.action = "answer"
        else:
            self.action = "edit"

        # 2. Sync outputs from artifact_types
        valid_outputs: list[Literal["docx", "pptx"]] = []
        for at in self.artifact_types:
            if at == ArtifactType.DOCX and "docx" not in valid_outputs:
                valid_outputs.append("docx")
            elif at == ArtifactType.PPTX and "pptx" not in valid_outputs:
                valid_outputs.append("pptx")
        if valid_outputs:
            self.outputs = valid_outputs

        # 3. Sync use_kb with use_rag
        self.use_kb = self.use_rag

        # 4. Sync target_artifact_ids
        if self.target_artifact_id and self.target_artifact_id not in self.target_artifact_ids:
            self.target_artifact_ids = [self.target_artifact_id]
        elif not self.target_artifact_id and self.target_artifact_ids:
            self.target_artifact_id = self.target_artifact_ids[0]

        # 5. Sync resolved_message and topic
        if not self.resolved_message and self.user_instruction:
            self.resolved_message = self.user_instruction
        elif not self.user_instruction and self.resolved_message:
            self.user_instruction = self.resolved_message

        if not self.topic:
            self.topic = self.user_instruction or self.resolved_message

        return self

    def __setattr__(self, name: str, value: Any) -> None:
        """Keep legacy fields synchronized on dynamic assignment."""
        super().__setattr__(name, value)
        if name == "operation":
            op_val = value.value if hasattr(value, "value") else str(value)
            if op_val in ("CREATE", "REGENERATE"):
                super().__setattr__("action", "generate")
            elif op_val == "CONVERT":
                super().__setattr__("action", "convert")
            elif op_val == "ANSWER":
                super().__setattr__("action", "answer")
            else:
                super().__setattr__("action", "edit")
        elif name == "use_rag":
            super().__setattr__("use_kb", bool(value))
        elif name == "target_artifact_id" and value:
            super().__setattr__("target_artifact_ids", [value])
        elif name == "target_artifact_ids" and value:
            super().__setattr__("target_artifact_id", value[0])
        elif name == "user_instruction" and value:
            super().__setattr__("resolved_message", str(value))
            if not getattr(self, "topic", ""):
                super().__setattr__("topic", str(value))


# Backward-compatible alias
Plan = ExecutionPlan
