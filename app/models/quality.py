"""Quality report of one artifact version (DATA_MODELS §9.2, QUALITY §6, ADR-009).

Schema defined in Stage 3; produced by the check-and-repair loop from Stage 8. Counts and verdict are derived
from the findings, so they cannot disagree with them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, computed_field

Severity = Literal["critical", "major", "minor", "info"]


class Repair(BaseModel):
    action: str
    by: Literal["deterministic", "llm"]


class Finding(BaseModel):
    check_id: str = Field(description="e.g. 'pptx.text_overflow'")
    tier: int = Field(ge=1, le=5)
    severity: Severity
    status: Literal["detected", "corrected", "unresolved"] = "detected"
    unit: str | None = Field(None, description="node_id or 'slide 3'")
    element: str | None = None
    message: str
    repair: Repair | None = None


class NotPerformed(BaseModel):
    check_id: str
    tier: int = Field(ge=1, le=5)
    reason: str


class EvidenceSummary(BaseModel):
    sources_used: int = 0
    coverage_pct: float | None = None
    unsupported_claims: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class QualityReport(BaseModel):
    schema_version: int = 1
    artifact_version_id: int | None = None
    findings: list[Finding] = Field(default_factory=list)
    not_performed: list[NotPerformed] = Field(default_factory=list)
    evidence_summary: EvidenceSummary = Field(default_factory=EvidenceSummary)
    iterations: int = 0

    @computed_field
    @property
    def counts(self) -> dict[str, int]:
        return {
            "detected": len(self.findings),
            "corrected": sum(1 for f in self.findings if f.status == "corrected"),
            "unresolved": sum(1 for f in self.findings if f.status != "corrected"),
            "not_performed": len(self.not_performed),
        }

    @computed_field
    @property
    def verdict(self) -> Literal["pass", "pass_with_warnings", "fail"]:
        open_findings = [f for f in self.findings if f.status != "corrected" and f.severity != "info"]
        if any(f.severity == "critical" for f in open_findings):
            return "fail"
        if open_findings or self.not_performed:
            return "pass_with_warnings"
        return "pass"
