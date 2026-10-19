"""Length targets (FR-PLAN-5, AC-7).

An explicit user requirement wins and is honoured exactly (slides, sheets) or within ±15 % (pages, words). Otherwise
length follows content need: how much evidence there is and the requested detail level. The template's own length
(its number of sample slides or headings) never decides it.
"""

from __future__ import annotations

import math
from typing import Any

from app.models.content_plan import LengthTarget
from app.services.plan_inputs import ClaimRef

WORDS_PER_PAGE = 400  # body text at 11 pt with headings, lists and the odd table
EXPLICIT_WORD_TOLERANCE = 15.0
DECK_RANGE = {"brief": (4, 7), "standard": (7, 12), "detailed": (10, 18)}
WORDS_PER_SECTION = {"brief": 120, "standard": 220, "detailed": 380}
SECTION_RANGE = {"brief": (3, 5), "standard": (4, 8), "detailed": (6, 11)}


def _explicit(length: dict[str, Any] | None, *units: str) -> tuple[str, int] | None:
    if not length or not length.get("explicit") or length.get("unit") not in units or not length.get("value"):
        return None
    return length["unit"], int(length["value"])


def _evidence_groups(claims: list[ClaimRef]) -> int:
    """Distinct things there is evidence about: research questions answered, else claims in threes."""
    facts = [c for c in claims if c.status != "unsupported"]
    questions = {q for c in facts for q in c.question_ids}
    return len(questions) if questions else math.ceil(len(facts) / 3)


def deck_length(length: dict[str, Any] | None, detail: str = "standard", claims: list[ClaimRef] | None = None,
                requested: int | None = None) -> LengthTarget:
    exp = _explicit(length, "slides")
    if exp:
        return LengthTarget(unit="slides", value=exp[1], explicit=True, rationale="stated by the user")
    if requested:
        return LengthTarget(unit="slides", value=requested, explicit=True, rationale="requested by the caller")
    lo, hi = DECK_RANGE.get(detail, DECK_RANGE["standard"])
    groups = _evidence_groups(claims or [])
    # title + closing + (agenda and summary for longer decks) around one or two slides per evidence group
    need = 2 + (2 if groups >= 4 else 1) + max(groups, 1) + len([c for c in (claims or []) if c.numeric]) // 4
    value = max(lo, min(hi, need)) if groups else (lo + hi) // 2
    return LengthTarget(unit="slides", value=value, rationale=f"content need: {groups} evidence groups, {detail} detail")


def document_length(length: dict[str, Any] | None, detail: str = "standard",
                    claims: list[ClaimRef] | None = None) -> LengthTarget:
    exp = _explicit(length, "pages", "words")
    if exp:
        unit, value = exp
        words = value * WORDS_PER_PAGE if unit == "pages" else value
        why = f"{value} pages × {WORDS_PER_PAGE} words" if unit == "pages" else "stated by the user"
        return LengthTarget(unit="words", value=words, explicit=True, tolerance_pct=EXPLICIT_WORD_TOLERANCE, rationale=why)
    n = document_sections(None, detail, claims)
    words = n * WORDS_PER_SECTION.get(detail, WORDS_PER_SECTION["standard"])
    return LengthTarget(unit="words", value=words, tolerance_pct=30.0,
                        rationale=f"content need: {n} sections at {detail} detail")


def document_sections(target: LengthTarget | None, detail: str = "standard", claims: list[ClaimRef] | None = None) -> int:
    lo, hi = SECTION_RANGE.get(detail, SECTION_RANGE["standard"])
    if target is not None and target.explicit:
        # about 250 words per section, at least 2 sections, so a one-page brief is not chopped into fragments
        return max(2, min(12, round(target.value / 250)))
    groups = _evidence_groups(claims or [])
    return max(lo, min(hi, groups + 2))


def workbook_length(length: dict[str, Any] | None, n_roles: int) -> LengthTarget:
    exp = _explicit(length, "sheets")
    if exp:
        return LengthTarget(unit="sheets", value=exp[1], explicit=True, rationale="stated by the user")
    return LengthTarget(unit="sheets", value=n_roles, rationale="one sheet per role the content needs")


def allocate_words(total: int, weights: list[float]) -> list[int]:
    """Split ``total`` across sections by weight; the parts add up to ``total`` exactly."""
    if not weights:
        return []
    s = sum(weights) or 1.0
    raw = [total * w / s for w in weights]
    parts = [int(r) for r in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - parts[i], reverse=True)[: total - sum(parts)]:
        parts[i] += 1
    return parts


def within(actual: int, target: LengthTarget) -> bool:
    if target.tolerance_pct <= 0:
        return actual == target.value
    return abs(actual - target.value) <= target.value * target.tolerance_pct / 100.0
