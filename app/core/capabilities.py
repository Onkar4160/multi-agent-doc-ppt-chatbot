"""Capability Registry and Governance for Dynamic Multi-Agent Execution.

Defines capability metadata, registration, prerequisites, and plan validation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence
from pydantic import BaseModel

logger = logging.getLogger(__name__)


@dataclass
class Capability:
    """Metadata definition for an executable capability in the multi-agent system."""

    name: str
    description: str
    input_schema: type[BaseModel] | dict[str, Any] | None = None
    output_schema: type[BaseModel] | dict[str, Any] | None = None
    prerequisites: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=lambda: ["user"])
    read_only: bool = True
    mutating: bool = False
    artifact_requirements: list[str] = field(default_factory=list)
    supported_artifact_types: list[str] = field(
        default_factory=lambda: ["DOCX", "PPTX", "XLSX"]
    )
    repeatable: bool = False
    executor: Callable[..., Any] | str | None = None


class CapabilityRegistry:
    """Central registry of system capabilities."""

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}
        self._register_default_capabilities()

    def register(self, capability: Capability) -> None:
        """Register a new capability."""
        self._capabilities[capability.name] = capability

    def get(self, name: str) -> Capability | None:
        """Look up a capability by name."""
        return self._capabilities.get(name)

    def list_all(self) -> list[Capability]:
        """Return all registered capabilities."""
        return list(self._capabilities.values())

    def list_names(self) -> list[str]:
        """Return names of all registered capabilities."""
        return list(self._capabilities.keys())

    def validate_plan_capabilities(
        self,
        capabilities: Sequence[str],
        artifact_types: Sequence[str] | None = None,
        operation: str | None = None,
    ) -> tuple[bool, list[str]]:
        """Validate an execution plan's capabilities against registry rules.

        Returns:
            (is_valid, list_of_error_messages)
        """
        errors: list[str] = []
        executed_set: set[str] = set()

        clean_art_types = [str(at).upper() for at in (artifact_types or [])]

        for cap_name in capabilities:
            cap = self.get(cap_name)
            if not cap:
                errors.append(f"Unknown capability: '{cap_name}'")
                continue

            # Check prerequisites
            for prereq in cap.prerequisites:
                if prereq not in executed_set:
                    errors.append(
                        f"Capability '{cap_name}' requires prerequisite '{prereq}' which has not been executed."
                    )

            # Check supported artifact types
            if clean_art_types and cap.supported_artifact_types:
                supported_upper = [s.upper() for s in cap.supported_artifact_types]
                # If capability is format-specific, at least one target artifact type must match
                is_supported = any(at in supported_upper for at in clean_art_types)
                if not is_supported and clean_art_types != ["UNKNOWN"]:
                    errors.append(
                        f"Capability '{cap_name}' does not support requested artifact types: {clean_art_types}. Supported: {supported_upper}"
                    )

            # Check repeatability
            if cap_name in executed_set and not cap.repeatable:
                errors.append(f"Non-repeatable capability '{cap_name}' executed more than once.")

            executed_set.add(cap_name)

        return (len(errors) == 0, errors)

    def _register_default_capabilities(self) -> None:
        """Register the baseline capability suite."""
        # File Analysis
        self.register(
            Capability(
                name="inspect_file",
                description="Inspect file metadata, MIME type, size, and layout properties.",
                read_only=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="analyze_document",
                description="Progressively inspect DOCX document structure, styles, and sections.",
                read_only=True,
                supported_artifact_types=["DOCX"],
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="analyze_presentation",
                description="Progressively inspect PPTX slide structure, layouts, and node IDs.",
                read_only=True,
                supported_artifact_types=["PPTX"],
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="analyze_spreadsheet",
                description="Progressively inspect XLSX sheets, tables, cell ranges, and formulas.",
                read_only=True,
                supported_artifact_types=["XLSX"],
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="analyze_image",
                description="Analyze image contents, dimensions, and extract OCR text if applicable.",
                read_only=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="analyze_template",
                description="Extract template layout profiles, fonts, colors, and tone guidelines.",
                read_only=True,
                repeatable=True,
            )
        )

        # Context & History
        self.register(
            Capability(
                name="retrieve_conversation_context",
                description="Load previous messages, active artifacts, and session state.",
                read_only=True,
                repeatable=False,
            )
        )
        self.register(
            Capability(
                name="retrieve_artifact",
                description="Retrieve artifact and latest version model from database.",
                read_only=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="retrieve_version",
                description="Retrieve a specific immutable artifact version.",
                read_only=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="resolve_reference",
                description="Resolve conversational references ('slide 4', 'the conclusion') to exact node IDs.",
                read_only=True,
                repeatable=True,
            )
        )

        # Evidence / Retrieval
        self.register(
            Capability(
                name="retrieve_relevant_chunks",
                description="Targeted retrieval of chunks from attached files without full context dump.",
                read_only=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="search_knowledge_base",
                description="Requirement-driven semantic vector search across enterprise knowledge base.",
                read_only=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="search_web",
                description="Targeted web search for fresh external intelligence and market data.",
                read_only=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="evaluate_evidence",
                description="Evaluate retrieved evidence sufficiency against plan requirements.",
                read_only=True,
                repeatable=True,
            )
        )

        # Generation
        self.register(
            Capability(
                name="generate_docx",
                description="Generate structured DocumentModel and render physical DOCX file.",
                read_only=False,
                mutating=True,
                supported_artifact_types=["DOCX"],
                repeatable=False,
            )
        )
        self.register(
            Capability(
                name="generate_pptx",
                description="Generate structured DeckModel and render physical PPTX presentation.",
                read_only=False,
                mutating=True,
                supported_artifact_types=["PPTX"],
                repeatable=False,
            )
        )
        self.register(
            Capability(
                name="generate_xlsx",
                description="Generate structured WorkbookModel and render physical XLSX spreadsheet.",
                read_only=False,
                mutating=True,
                supported_artifact_types=["XLSX"],
                repeatable=False,
            )
        )

        # Editing / Modification
        self.register(
            Capability(
                name="edit_artifact",
                description="Apply targeted modifications to an existing artifact.",
                read_only=False,
                mutating=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="append_artifact",
                description="Append new content/section/slide to the end of an existing artifact.",
                read_only=False,
                mutating=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="insert_artifact",
                description="Insert a section, slide, or row at a specific index.",
                read_only=False,
                mutating=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="delete_artifact_content",
                description="Remove targeted section, slide, or row from an artifact.",
                read_only=False,
                mutating=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="move_artifact_content",
                description="Reorder sections, slides, or sheets within an artifact.",
                read_only=False,
                mutating=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="restyle_artifact",
                description="Apply visual restyling, theme, or template colors to an artifact.",
                read_only=False,
                mutating=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="convert_artifact",
                description="Perform bi-directional artifact conversion (e.g. DOCX -> PPTX).",
                read_only=False,
                mutating=True,
                repeatable=True,
            )
        )

        # Validation
        self.register(
            Capability(
                name="validate_artifact",
                description="Comprehensive multi-layered validation across structure, citations, and scope.",
                read_only=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="validate_scope",
                description="Enforce that only requested target nodes were modified during an edit.",
                read_only=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="validate_citations",
                description="Verify that all claimed numbers and facts cite valid source IDs.",
                read_only=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="validate_structure",
                description="Verify that document, deck, or workbook meets required structural schema.",
                read_only=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="validate_content",
                description="Verify that required topics, conclusions, and evidence are addressed.",
                read_only=True,
                repeatable=True,
            )
        )
        self.register(
            Capability(
                name="validate_visual",
                description="Inspect rendered layout aesthetics, page counts, or slide spacing where available.",
                read_only=True,
                repeatable=True,
            )
        )

        # Versioning
        self.register(
            Capability(
                name="create_version",
                description="Persist immutable new version record and physical artifact to disk.",
                read_only=False,
                mutating=True,
                prerequisites=["validate_artifact"],
                repeatable=True,
            )
        )


# Global singleton instance
_capability_registry = CapabilityRegistry()


def get_capability_registry() -> CapabilityRegistry:
    """Return the global capability registry singleton."""
    return _capability_registry
