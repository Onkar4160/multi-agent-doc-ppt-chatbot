"""Research dossier: Source → EvidenceItem → Claim (DATA_MODELS §5.2, ADR-006).

Schema defined in Stage 3; filled by the research pipeline from Stage 5. Invariants keep traceability honest:
evidence points at registered sources, and a claim marked *supported* points at evidence.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ResearchQuestion(BaseModel):
    id: str
    question: str
    priority: int = Field(2, ge=1, le=3)
    rationale: str = ""


class ResearchPlan(BaseModel):
    depth: Literal["none", "light", "standard", "deep"] = "standard"
    questions: list[ResearchQuestion] = Field(default_factory=list)


class SourceRecord(BaseModel):
    id: int = Field(ge=1, description="Citation number space shared with the rendered output")
    kind: Literal["web", "paper", "user_file", "kb", "dataset"]
    title: str
    url: str | None = None
    file_id: int | None = None
    locator: str | None = None
    publisher: str | None = None
    authors: list[str] = Field(default_factory=list)
    venue: str | None = None
    doi: str | None = None
    published_at: str | None = None
    retrieved_at: str | None = None
    authority: Literal["high", "medium", "low"] = "medium"
    authority_rationale: str = ""


class EvidenceItem(BaseModel):
    id: str
    source_id: int
    question_ids: list[str] = Field(default_factory=list)
    statement: str
    value: float | None = None
    unit: str | None = None
    as_of: str | None = None
    quote: str | None = Field(None, max_length=300)
    locator: str = ""


class Claim(BaseModel):
    id: str
    statement: str
    status: Literal["supported", "assumption", "interpretation", "estimate", "unsupported"]
    evidence_ids: list[str] = Field(default_factory=list)
    conflict_note: str | None = None

    @model_validator(mode="after")
    def _supported_needs_evidence(self) -> Claim:
        if self.status == "supported" and not self.evidence_ids:
            raise ValueError(f"claim {self.id} is marked supported but cites no evidence")
        return self


class Conflict(BaseModel):
    claim_ids: list[str]
    note: str


class Coverage(BaseModel):
    answered: list[str] = Field(default_factory=list)
    partial: list[str] = Field(default_factory=list)
    unanswered: list[str] = Field(default_factory=list)


class ResearchDossier(BaseModel):
    schema_version: int = 1
    plan: ResearchPlan = Field(default_factory=ResearchPlan)
    sources: list[SourceRecord] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    gaps: list[str] = Field(default_factory=list)
    providers_used: list[str] = Field(default_factory=list)
    provider_errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _references_resolve(self) -> ResearchDossier:
        source_ids = {s.id for s in self.sources}
        evidence_ids = {e.id for e in self.evidence}
        claim_ids = {c.id for c in self.claims}
        if len(source_ids) != len(self.sources):
            raise ValueError("source IDs must be unique")
        for e in self.evidence:
            if e.source_id not in source_ids:
                raise ValueError(f"evidence {e.id} points at unknown source {e.source_id}")
        for c in self.claims:
            unknown = set(c.evidence_ids) - evidence_ids
            if unknown:
                raise ValueError(f"claim {c.id} cites unknown evidence {sorted(unknown)}")
        for conflict in self.conflicts:
            if set(conflict.claim_ids) - claim_ids:
                raise ValueError("a conflict references an unknown claim")
        return self

    def coverage_pct(self) -> float:
        """Share of claims that are supported or carry an explicit non-fact status (AC-5)."""
        if not self.claims:
            return 100.0
        labelled = sum(1 for c in self.claims if c.status != "unsupported")
        return round(100.0 * labelled / len(self.claims), 1)
