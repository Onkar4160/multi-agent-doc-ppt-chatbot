"""What planners and writers know about the evidence: a compact claim digest (FR-PLAN-2, AC-5).

Claims come from the research dossier (Stage 5). Without a dossier (no research, attached-file evidence only), each
evidence line becomes a supported claim of its source, so plans can still cite something checkable.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.models.evidence import ResearchDossier

YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


class ClaimRef(BaseModel):
    id: str
    statement: str
    status: str = "supported"
    source_ids: list[int] = Field(default_factory=list)
    question_ids: list[str] = Field(default_factory=list)
    value: float | None = None
    unit: str | None = None
    as_of: str | None = None

    @property
    def numeric(self) -> bool:
        return self.value is not None

    @property
    def dated(self) -> bool:
        return bool(self.as_of) or bool(YEAR.search(self.statement))

    @property
    def is_fact(self) -> bool:
        return self.status == "supported"


def claim_digest(dossier: ResearchDossier | None) -> list[ClaimRef]:
    if dossier is None:
        return []
    evidence = {e.id: e for e in dossier.evidence}
    out = []
    for c in dossier.claims:
        items = [evidence[i] for i in c.evidence_ids if i in evidence]
        with_value = next((e for e in items if e.value is not None), None)
        out.append(ClaimRef(
            id=c.id, statement=c.statement, status=c.status,
            source_ids=sorted({e.source_id for e in items}),
            question_ids=sorted({q for e in items for q in e.question_ids}),
            value=with_value.value if with_value else None,
            unit=with_value.unit if with_value else None,
            as_of=next((e.as_of for e in items if e.as_of), None),
        ))
    return out


def digest_from_evidence(pack: list[Any] | None) -> list[ClaimRef]:
    """Claims from writer-facing evidence lines (id = source id; id 0 = a limitation, skipped)."""
    out = []
    for n, item in enumerate(pack or [], start=1):
        sid = item["id"] if isinstance(item, dict) else getattr(item, "id", 0)
        text = item["text"] if isinstance(item, dict) else getattr(item, "text", "")
        if not sid or not text:
            continue
        out.append(ClaimRef(id=f"E{n}", statement=text, source_ids=[int(sid)]))
    return out


def format_digest(claims: list[ClaimRef], limit: int = 60) -> str:
    """Claims as the planner/writer prompt sees them, wrapped as data."""
    if not claims:
        return "<claims>\n(no researched claims: write only general, non-factual connective content)\n</claims>"
    lines = []
    for c in claims[:limit]:
        figure = f" | figure: {c.value:g} {c.unit or ''}".rstrip() if c.value is not None else ""
        when = f" | as of {c.as_of}" if c.as_of else ""
        src = ",".join(str(s) for s in c.source_ids) or "-"
        lines.append(f"{c.id} [{c.status}] (sources {src}){figure}{when}: {c.statement}")
    return "<claims>\n" + "\n".join(lines) + "\n</claims>"


def by_id(claims: list[ClaimRef]) -> dict[str, ClaimRef]:
    return {c.id: c for c in claims}
