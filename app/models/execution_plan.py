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


class SourceRequirements(BaseModel):
    """Grounded evidence source requirements supporting dual attribute and dict-like access."""

    uploaded_files: bool = False
    knowledge_base: bool = False
    web: bool = False

    def __getitem__(self, item: str) -> bool:
        return getattr(self, item, False)

    def __setitem__(self, key: str, value: bool) -> None:
        setattr(self, key, value)

    def get(self, key: str, default: bool = False) -> bool:
        return getattr(self, key, default)


class ExecutionPlan(BaseModel):
    """Strongly typed execution plan produced by the Intent & Context Layer."""

    # 1. Primary Taxonomy & Intent Fields
    goal: str = Field(
        default="",
        description="High-level semantic goal inferred from user request.",
    )
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
    attachment_roles: dict[str, str] = Field(
        default_factory=dict,
        description="Semantic role mapping for attached files (TEMPLATE, SOURCE, STYLE_REFERENCE, DATA_SOURCE, etc.).",
    )
    required_information: list[str] = Field(
        default_factory=list,
        description="List of specific information items required to satisfy request.",
    )
    source_requirements: SourceRequirements = Field(
        default_factory=SourceRequirements,
        description="Grounded evidence source requirements.",
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
    subject: str = Field(
        default="",
        description="What the deliverable is about, resolved from the conversation (e.g. 'Generative AI in 2026'); "
                    "never a pronoun or a phrase like 'this same'.",
    )
    confidence: float = Field(
        default=1.0,
        description="Confidence score in the intent resolution (0.0 to 1.0).",
    )
    ambiguity: bool = Field(
        default=False,
        description="Whether the request is ambiguous or underspecified.",
    )
    clarification_needed: str | None = Field(
        default=None,
        description="Clarification question to ask user if ambiguity is critical.",
    )
    reasoning: str = Field(
        default="",
        description="Structured rationale for intent classification and reference resolution.",
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="List of capabilities selected by the planner to execute this request.",
    )
    execution_order: list[str] = Field(
        default_factory=list,
        description="Ordered sequence of capability execution.",
    )

    # 2. Backward Compatibility Fields (mirroring legacy Plan in supervisor.py)
    action: Literal["generate", "edit", "convert", "answer"] = Field(
        default="generate",
        description="[Compatibility] Legacy action string.",
    )
    outputs: list[str] = Field(
        default_factory=lambda: ["docx", "pptx"],
        description="[Compatibility] Legacy target formats ('docx', 'pptx', 'xlsx').",
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
        valid_outputs: list[str] = []
        for at in self.artifact_types:
            if at == ArtifactType.DOCX and "docx" not in valid_outputs:
                valid_outputs.append("docx")
            elif at == ArtifactType.PPTX and "pptx" not in valid_outputs:
                valid_outputs.append("pptx")
            elif at == ArtifactType.XLSX and "xlsx" not in valid_outputs:
                valid_outputs.append("xlsx")
        if valid_outputs:
            self.outputs = valid_outputs

        # 3. Sync source_requirements with use_rag and use_web
        if not self.source_requirements:
            self.source_requirements = {
                "uploaded_files": bool(self.source_file_ids),
                "knowledge_base": self.use_rag,
                "web": self.use_web,
            }
        else:
            if "knowledge_base" in self.source_requirements:
                self.use_rag = bool(self.source_requirements["knowledge_base"])
            if "web" in self.source_requirements:
                self.use_web = bool(self.source_requirements["web"])

        # 4. Sync use_kb with use_rag
        self.use_kb = self.use_rag

        # 5. Sync target_artifact_ids
        if self.target_artifact_id and self.target_artifact_id not in self.target_artifact_ids:
            self.target_artifact_ids = [self.target_artifact_id]
        elif not self.target_artifact_id and self.target_artifact_ids:
            self.target_artifact_id = self.target_artifact_ids[0]

        # 6. Sync resolved_message and topic
        if not self.resolved_message and self.user_instruction:
            self.resolved_message = self.user_instruction
        elif not self.user_instruction and self.resolved_message:
            self.user_instruction = self.resolved_message

        if not self.topic:
            self.topic = self.user_instruction or self.resolved_message

        # 7. Sync goal if empty
        if not self.goal:
            self.goal = self.user_instruction or self.resolved_message or "Process user request"

        # 8. Sync default capabilities and execution order if not specified
        if not self.capabilities:
            caps: list[str] = []
            if self.source_file_ids:
                caps.append("inspect_file")
                caps.append("retrieve_relevant_chunks")
            if self.use_rag:
                caps.append("search_knowledge_base")
            if self.use_web:
                caps.append("search_web")
            if self.use_rag or self.use_web or self.source_file_ids:
                caps.append("evaluate_evidence")

            if self.operation in (OperationType.CREATE, OperationType.REGENERATE):
                if self.template_mode in (TemplateMode.UPLOADED, TemplateMode.EXISTING):
                    caps.append("analyze_template")
                for at in self.artifact_types:
                    if at == ArtifactType.DOCX:
                        caps.append("generate_docx")
                    elif at == ArtifactType.PPTX:
                        caps.append("generate_pptx")
                    elif at == ArtifactType.XLSX:
                        caps.append("generate_xlsx")
                caps.append("validate_artifact")
                caps.append("create_version")
            elif self.operation in (OperationType.EDIT, OperationType.REFRESH, OperationType.RESTYLE):
                caps.extend(["retrieve_artifact", "resolve_reference", "edit_artifact", "validate_artifact", "validate_scope", "create_version"])
            elif self.operation == OperationType.APPEND:
                caps.extend(["retrieve_artifact", "resolve_reference", "append_artifact", "validate_artifact", "validate_scope", "create_version"])
            elif self.operation == OperationType.INSERT:
                caps.extend(["retrieve_artifact", "resolve_reference", "insert_artifact", "validate_artifact", "validate_scope", "create_version"])
            elif self.operation == OperationType.DELETE:
                caps.extend(["retrieve_artifact", "resolve_reference", "delete_artifact_content", "validate_artifact", "validate_scope", "create_version"])
            elif self.operation == OperationType.MOVE:
                caps.extend(["retrieve_artifact", "resolve_reference", "move_artifact_content", "validate_artifact", "validate_scope", "create_version"])
            elif self.operation == OperationType.CONVERT:
                caps.extend(["retrieve_artifact", "convert_artifact", "validate_artifact", "create_version"])

            self.capabilities = caps

        if not self.execution_order:
            self.execution_order = list(self.capabilities)

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
