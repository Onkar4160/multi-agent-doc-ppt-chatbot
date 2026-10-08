"""Structured ConversationContext schema for Part 1 Context Management."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class ConversationContext(BaseModel):
    """Structured context representation maintained across conversation turns."""

    conversation_id: str = Field(
        default="", description="Unique chat session or conversation ID."
    )
    recent_messages: list[dict[str, Any]] = Field(
        default_factory=list, description="Recent conversation messages in chronological order."
    )
    conversation_summary: str = Field(
        default="", description="Compact, actionable state summary focusing on documents, versions, and requirements."
    )
    active_artifact: dict[str, Any] | None = Field(
        default=None, description="Most recently created or modified active artifact metadata."
    )
    active_version: int | None = Field(
        default=None, description="Active artifact's current version number."
    )
    artifact_history: list[dict[str, Any]] = Field(
        default_factory=list, description="All artifacts created or referenced in this session."
    )
    recent_operations: list[dict[str, Any]] = Field(
        default_factory=list, description="History of recent operations (plans) executed in this session."
    )
    relevant_sources: list[dict[str, Any]] = Field(
        default_factory=list, description="Citations and uploaded sources associated with active artifacts."
    )
    active_template: dict[str, Any] | None = Field(
        default=None, description="Active workspace or session template details."
    )
    current_topic: str = Field(
        default="", description="Active discussion topic or document theme."
    )
    extracted_requirements: dict[str, Any] = Field(
        default_factory=dict, description="Cumulative requirements (slide_count, tone, doc_type, etc.)."
    )
    unresolved_references: list[str] = Field(
        default_factory=list, description="Ambiguous or unresolved references flagged during intent analysis."
    )
    uploaded_files: list[dict[str, Any]] = Field(
        default_factory=list, description="Uploaded files associated with this conversation session."
    )
    active_artifact_id: int | None = Field(
        default=None, description="Active artifact ID for this conversation."
    )
    active_version_no: int | None = Field(
        default=None, description="Active artifact version number."
    )
    last_target_nodes: list[str] = Field(
        default_factory=list, description="Target nodes from the most recent operation."
    )

    @property
    def active_topic(self) -> str:
        """Alias property for current_topic."""
        return self.current_topic

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary preserving keys expected by existing services."""
        return {
            "conversation_id": self.conversation_id,
            "recent_messages": self.recent_messages,
            "artifacts": self.artifact_history,
            "active_topic": self.current_topic,
            "conversation_summary": self.conversation_summary,
            "active_artifact": self.active_artifact,
            "active_version": self.active_version,
            "recent_operations": self.recent_operations,
            "relevant_sources": self.relevant_sources,
            "active_template": self.active_template,
            "extracted_requirements": self.extracted_requirements,
            "unresolved_references": self.unresolved_references,
            "uploaded_files": self.uploaded_files,
            "active_artifact_id": self.active_artifact_id,
            "active_version_no": self.active_version_no,
            "last_target_nodes": self.last_target_nodes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationContext:
        """Construct ConversationContext from dictionary representation."""
        if not data:
            return cls()
        act_art = data.get("active_artifact")
        act_id = data.get("active_artifact_id") or (act_art.get("artifact_id") if act_art else None)
        act_ver = data.get("active_version_no") or data.get("active_version")
        return cls(
            conversation_id=data.get("conversation_id", data.get("session_id", "")),
            recent_messages=data.get("recent_messages", []),
            conversation_summary=data.get("conversation_summary", ""),
            active_artifact=act_art,
            active_version=act_ver,
            artifact_history=data.get("artifacts", data.get("artifact_history", [])),
            recent_operations=data.get("recent_operations", []),
            relevant_sources=data.get("relevant_sources", []),
            active_template=data.get("active_template"),
            current_topic=data.get("active_topic", data.get("current_topic", "")),
            extracted_requirements=data.get("extracted_requirements", {}),
            unresolved_references=data.get("unresolved_references", []),
            uploaded_files=data.get("uploaded_files", []),
            active_artifact_id=act_id,
            active_version_no=act_ver,
            last_target_nodes=data.get("last_target_nodes", []),
        )
