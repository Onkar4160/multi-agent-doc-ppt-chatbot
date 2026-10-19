"""Deterministic checks of content plans and of content written from them (FR-PLAN-7, AC-5, AC-7).

Plan checks: duplicate messages, empty units, claim/source resolution, unsupported claims used as facts, length
compliance, archetype–content fit, off-brief units, structure. Content checks: the written artifact follows its plan
(unit count and order, archetypes, well-formed blocks, length).
"""

from __future__ import annotations

import re
from typing import Any

from app.models.content_plan import (
    ARCHETYPE_BLOCKS, DATED_ARCHETYPES, NUMERIC_ARCHETYPES, DeckPlan, DocumentPlan, PlanIssue, PlanReport, WorkbookPlan,
)
from app.services.length_rules import within
from app.services.plan_inputs import ClaimRef, by_id

STOP = set("""a an the and or of to in on for with by from at as is are was were be been this that these those it its
their our your we you they how what why which who when where into over under than then so not no can will would should
may might must about across between per vs via more most less least new key""".split())
FRAME_ARCHETYPES = {"title", "section", "closing", "references", "agenda"}
OFF_BRIEF = re.compile(r"\b(pricing|commercials?|investment|our team|meet the team|fees?)\b", re.I)
DUPLICATE_JACCARD = 0.7


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9%$€£]+", (text or "").lower()) if len(w) > 2 and w not in STOP}


def similar(a: str, b: str) -> bool:
    ta, tb = tokens(a), tokens(b)
    if len(ta) < 2 or len(tb) < 2:
        return bool(ta) and ta == tb
    return len(ta & tb) / len(ta | tb) >= DUPLICATE_JACCARD


def coverage_pct(cited: list[str], claims: dict[str, ClaimRef]) -> float:
    """AC-5: share of cited claims that exist and carry evidence or an explicit non-fact status."""
    if not cited:
        return 100.0
    ok = sum(1 for cid in cited if cid in claims and claims[cid].status != "unsupported")
    return round(100.0 * ok / len(cited), 1)


def _claim_checks(unit: str, claim_ids: list[str], source_ids: list[int], claims: dict[str, ClaimRef],
                  valid_sources: set[int] | None) -> list[PlanIssue]:
    issues = []
    for cid in claim_ids:
        if cid not in claims:
            issues.append(PlanIssue(code="unknown_claim", severity="critical", unit=unit, message=f"cites unknown claim {cid}"))
        elif claims[cid].status == "unsupported":
            issues.append(PlanIssue(code="unsupported_claim", severity="major", unit=unit,
                                    message=f"rests on {cid}, which the sources do not support"))
    if valid_sources is not None:
        for sid in source_ids:
            if sid not in valid_sources:
                issues.append(PlanIssue(code="unknown_source", severity="critical", unit=unit, message=f"cites unknown source {sid}"))
    return issues


def archetype_misfit(unit: str, archetype: str, claim_ids: list[str], claims: dict[str, ClaimRef]) -> list[PlanIssue]:
    cited = [claims[c] for c in claim_ids if c in claims and claims[c].status != "unsupported"]
    if archetype in NUMERIC_ARCHETYPES:
        need = 2 if archetype == "chart" else 1
        if sum(1 for c in cited if c.numeric) < need:
            return [PlanIssue(code="archetype_fit", severity="major", unit=unit,
                              message=f"{archetype} needs at least {need} cited figure(s)")]
    if archetype in DATED_ARCHETYPES and sum(1 for c in cited if c.dated) < 3:
        return [PlanIssue(code="archetype_fit", severity="major", unit=unit, message="timeline needs at least 3 dated claims")]
    return []


def validate_deck_plan(plan: DeckPlan, claims: list[ClaimRef], *, brief: str = "", document_type: str = "",
                       valid_sources: set[int] | None = None) -> PlanReport:
    cmap = by_id(claims)
    issues: list[PlanIssue] = []
    slides = plan.slides
    if plan.length is not None and len(slides) != plan.length.value:
        issues.append(PlanIssue(code="length", severity="critical", unit="plan",
                                message=f"{len(slides)} slides planned, {plan.length.value} required ({plan.length.rationale})"))
    if slides and slides[0].archetype != "title":
        issues.append(PlanIssue(code="structure", severity="major", unit="slide 1", message="the deck must open with a title slide"))
    cited: list[str] = []
    for i, s in enumerate(slides, start=1):
        unit = f"slide {i}"
        cited += s.claim_ids
        content = s.archetype not in FRAME_ARCHETYPES
        if not s.headline.strip() or (content and not (s.key_message.strip() or s.blocks)):
            issues.append(PlanIssue(code="empty_unit", severity="major", unit=unit, message="no headline or message"))
        main = ARCHETYPE_BLOCKS.get(s.archetype, ())
        if main and main[0] not in s.blocks:
            issues.append(PlanIssue(code="archetype_fit", severity="minor", unit=unit,
                                    message=f"{s.archetype} slide does not plan its {main[0]} block"))
        issues += _claim_checks(unit, s.claim_ids, s.source_ids, cmap, valid_sources)
        issues += archetype_misfit(unit, s.archetype, s.claim_ids, cmap)
        if content and claims and not s.claim_ids and s.archetype not in ("summary", "process"):
            issues.append(PlanIssue(code="uncited_unit", severity="minor", unit=unit, message="content slide cites no claim"))
        if document_type != "proposal" and OFF_BRIEF.search(s.headline) and not OFF_BRIEF.search(brief):
            issues.append(PlanIssue(code="off_brief", severity="minor", unit=unit, message=f"'{s.headline}' was not asked for"))
        for j in range(1, i):
            other = slides[j - 1]
            if s.archetype in FRAME_ARCHETYPES and s.archetype == other.archetype:
                continue
            if similar(s.headline, other.headline) or (s.key_message and similar(s.key_message, other.key_message)):
                issues.append(PlanIssue(code="duplicate", severity="major", unit=unit, message=f"repeats slide {j}"))
                break
    return PlanReport(kind="deck", issues=issues, claim_coverage_pct=coverage_pct(cited, cmap),
                      length_ok=plan.length is None or len(slides) == plan.length.value)


def validate_document_plan(plan: DocumentPlan, claims: list[ClaimRef], *, brief: str = "", document_type: str = "",
                           valid_sources: set[int] | None = None) -> PlanReport:
    cmap = by_id(claims)
    issues: list[PlanIssue] = []
    if not plan.sections:
        issues.append(PlanIssue(code="empty_unit", severity="critical", unit="plan", message="no sections planned"))
    cited: list[str] = []
    for i, s in enumerate(plan.sections, start=1):
        unit = f"section {i}"
        cited += s.claim_ids
        if not s.heading.strip():
            issues.append(PlanIssue(code="empty_unit", severity="critical", unit=unit, message="section without a heading"))
        elif not (s.purpose.strip() or s.argument.strip()):
            issues.append(PlanIssue(code="empty_unit", severity="major", unit=unit, message=f"'{s.heading}' has no purpose or argument"))
        if s.target_words <= 0:
            issues.append(PlanIssue(code="length", severity="major", unit=unit, message="no word budget"))
        issues += _claim_checks(unit, s.claim_ids, s.source_ids, cmap, valid_sources)
        if document_type != "proposal" and OFF_BRIEF.search(s.heading) and not OFF_BRIEF.search(brief):
            issues.append(PlanIssue(code="off_brief", severity="major", unit=unit, message=f"'{s.heading}' was not asked for"))
        for j in range(1, i):
            if similar(s.heading, plan.sections[j - 1].heading) or (s.argument and similar(s.argument, plan.sections[j - 1].argument)):
                issues.append(PlanIssue(code="duplicate", severity="major", unit=unit, message=f"repeats section {j}"))
                break
    total = sum(s.target_words for s in plan.sections) + (plan.summary_words if plan.executive_summary else 0)
    length_ok = True
    if plan.length is not None and plan.length.value:
        # the allocation itself must hit the target closely; written text then gets the tolerance
        length_ok = abs(total - plan.length.value) <= plan.length.value * 0.05
        if not length_ok:
            issues.append(PlanIssue(code="length", severity="major", unit="plan",
                                    message=f"sections budget {total} words, target {plan.length.value}"))
    return PlanReport(kind="document", issues=issues, claim_coverage_pct=coverage_pct(cited, cmap), length_ok=length_ok)


def validate_workbook_plan(plan: WorkbookPlan, claims: list[ClaimRef], *,
                           valid_sources: set[int] | None = None) -> PlanReport:
    cmap = by_id(claims)
    issues: list[PlanIssue] = []
    names = [s.name for s in plan.sheets]
    roles = [s.role for s in plan.sheets]
    if plan.length is not None and plan.length.explicit and len(plan.sheets) != plan.length.value:
        issues.append(PlanIssue(code="length", severity="critical", unit="plan",
                                message=f"{len(plan.sheets)} sheets planned, {plan.length.value} required"))
    if len(set(n.lower() for n in names)) != len(names):
        issues.append(PlanIssue(code="duplicate", severity="critical", unit="plan", message="two sheets share a name"))
    for n in names:
        if len(n) > 31 or re.search(r"[\\/*?:\[\]]", n):
            issues.append(PlanIssue(code="structure", severity="critical", unit=f"sheet {n}", message="not a valid Excel sheet name"))
    for role in ("data",):
        if role not in roles and (plan.length is None or not plan.length.explicit or plan.length.value > 1):
            issues.append(PlanIssue(code="missing_role", severity="major", unit="plan", message=f"no {role} sheet"))
    cited: list[str] = []
    for s in plan.sheets:
        unit = f"sheet {s.name}"
        cited += s.claim_ids
        if s.role in ("data", "calculations", "assumptions") and not s.columns:
            issues.append(PlanIssue(code="empty_unit", severity="major", unit=unit, message="no columns planned"))
        for r in s.reads:
            if r not in names:
                issues.append(PlanIssue(code="structure", severity="major", unit=unit, message=f"reads unknown sheet '{r}'"))
        issues += _claim_checks(unit, s.claim_ids, s.source_ids, cmap, valid_sources)
    for a in plan.assumptions:
        if a.source_id is None and not a.rationale.strip():
            issues.append(PlanIssue(code="assumption_unsourced", severity="major", unit=f"assumption {a.name}",
                                    message="an assumption needs a source or a stated rationale"))
        if valid_sources is not None and a.source_id is not None and a.source_id not in valid_sources:
            issues.append(PlanIssue(code="unknown_source", severity="critical", unit=f"assumption {a.name}",
                                    message=f"cites unknown source {a.source_id}"))
    if "calculations" in roles and not plan.checks:
        issues.append(PlanIssue(code="structure", severity="minor", unit="plan", message="calculations without expected-value checks"))
    return PlanReport(kind="workbook", issues=issues, claim_coverage_pct=coverage_pct(cited, cmap),
                      length_ok=plan.length is None or not plan.length.explicit or len(plan.sheets) == plan.length.value)


# ── Content written from a plan ──────────────────────────────────────────────

def _block_problem(block: Any) -> str | None:
    t = getattr(block, "type", "")
    if t == "bullets" and not getattr(block, "items", None):
        return "empty bullet list"
    if t == "table":
        if not block.rows:
            return "table without rows"
        if block.headers and any(len(r) != len(block.headers) for r in block.rows):
            return "table rows do not match its headers"
    if t == "chart":
        if len(block.categories) < 2 or not block.series:
            return "chart needs at least 2 categories and a series"
        if any(len(s.values) != len(block.categories) for s in block.series):
            return "chart series length differs from its categories"
    if t == "timeline" and len(block.events) < 2:
        return "timeline needs at least 2 events"
    if t == "process" and len(block.steps) < 2:
        return "process needs at least 2 steps"
    if t == "comparison" and (len(block.columns) < 2 or any(not c.points for c in block.columns)):
        return "comparison needs 2 columns with points"
    if t == "kpi" and not block.items:
        return "KPI block without figures"
    return None


def slide_text(slide: Any) -> str:
    parts = [slide.title, slide.subtitle or "", slide.key_message or ""]
    parts += [b.text for b in list(slide.bullets) + list(slide.left) + list(slide.right)]
    for blk in slide.blocks:
        parts += block_texts(blk)
    return " ".join(p for p in parts if p)


def block_texts(blk: Any) -> list[str]:
    t = getattr(blk, "type", "")
    if t == "bullets":
        return [i.text if hasattr(i, "text") else str(i) for i in blk.items]
    if t in ("paragraph", "quote", "callout"):
        return [blk.text]
    if t == "table":
        return [" ".join(map(str, r)) for r in blk.rows]
    if t == "chart":
        return [blk.title] + blk.categories
    if t == "timeline":
        return [f"{e.date} {e.label} {e.detail}" for e in blk.events]
    if t == "process":
        return [f"{s.label} {s.detail}" for s in blk.steps]
    if t == "comparison":
        return [c.heading + " " + " ".join(c.points) for c in blk.columns]
    if t == "kpi":
        return [f"{i.value} {i.label} {i.delta}" for i in blk.items]
    if t == "figure":
        return [blk.caption]
    return []


def document_words(doc: Any) -> int:
    words = len((getattr(doc, "executive_summary", "") or "").split())
    for sec in doc.sections:
        for blk in sec.blocks:
            words += sum(len(t.split()) for t in block_texts(blk))
    return words


def validate_deck_content(deck: Any, plan: DeckPlan) -> PlanReport:
    issues: list[PlanIssue] = []
    if len(deck.slides) != len(plan.slides):
        issues.append(PlanIssue(code="length", severity="critical", unit="deck",
                                message=f"{len(deck.slides)} slides written, {len(plan.slides)} planned"))
    for i, (slide, sp) in enumerate(zip(deck.slides, plan.slides), start=1):
        unit = f"slide {i}"
        if slide.archetype and slide.archetype != sp.archetype:
            issues.append(PlanIssue(code="structure", severity="major", unit=unit,
                                    message=f"written as {slide.archetype}, planned as {sp.archetype}"))
        if sp.archetype not in FRAME_ARCHETYPES and not (slide.blocks or slide.bullets or slide.left or slide.right):
            issues.append(PlanIssue(code="empty_unit", severity="major", unit=unit, message="content slide has no content"))
        main = ARCHETYPE_BLOCKS.get(sp.archetype, ())
        if main and main[0] not in ("bullets",) and not any(getattr(b, "type", "") == main[0] for b in slide.blocks):
            issues.append(PlanIssue(code="archetype_fit", severity="major", unit=unit, message=f"planned {main[0]} block missing"))
        for blk in slide.blocks:
            problem = _block_problem(blk)
            if problem:
                issues.append(PlanIssue(code="archetype_fit", severity="major", unit=unit, message=problem))
        for j in range(1, i):
            if slide.title and similar(slide.title, deck.slides[j - 1].title):
                issues.append(PlanIssue(code="duplicate", severity="major", unit=unit, message=f"same title as slide {j}"))
                break
    return PlanReport(kind="deck", stage="content", issues=issues, length_ok=len(deck.slides) == len(plan.slides))


def validate_document_content(doc: Any, plan: DocumentPlan) -> PlanReport:
    issues: list[PlanIssue] = []
    if len(doc.sections) != len(plan.sections):
        issues.append(PlanIssue(code="structure", severity="major", unit="document",
                                message=f"{len(doc.sections)} sections written, {len(plan.sections)} planned"))
    for i, sec in enumerate(doc.sections, start=1):
        if not sec.blocks:
            issues.append(PlanIssue(code="empty_unit", severity="major", unit=f"section {i}", message=f"'{sec.heading}' is empty"))
        for blk in sec.blocks:
            problem = _block_problem(blk)
            if problem:
                issues.append(PlanIssue(code="archetype_fit", severity="major", unit=f"section {i}", message=problem))
    words = document_words(doc)
    ok = plan.length is None or within(words, plan.length)
    if not ok:
        sev = "critical" if plan.length.explicit else "minor"
        issues.append(PlanIssue(code="length", severity=sev, unit="document",
                                message=f"{words} words written, target {plan.length.value} ±{plan.length.tolerance_pct:g} %"))
    return PlanReport(kind="document", stage="content", issues=issues, length_ok=ok)


def validate_workbook_content(wb: Any, plan: WorkbookPlan) -> PlanReport:
    issues: list[PlanIssue] = []
    written = {s.name: s for s in wb.sheets}
    for sp in plan.sheets:
        s = written.get(sp.name)
        if s is None:
            issues.append(PlanIssue(code="missing_role", severity="major", unit=f"sheet {sp.name}", message="planned sheet missing"))
            continue
        if sp.role in ("data", "calculations", "assumptions") and not (s.tables or s.cells):
            issues.append(PlanIssue(code="empty_unit", severity="major", unit=f"sheet {sp.name}", message="sheet has no content"))
        if sp.role == "calculations" and not any(c.formula for c in s.cells) and not any(
                isinstance(v, str) and v.startswith("=") for t in s.tables for r in t.rows for v in r):
            issues.append(PlanIssue(code="structure", severity="major", unit=f"sheet {sp.name}", message="calculations without formulas"))
    defined = {n.name for n in getattr(wb, "named_ranges", [])}
    for a in plan.assumptions:
        if a.name not in defined:
            issues.append(PlanIssue(code="structure", severity="minor", unit=f"assumption {a.name}", message="no defined name"))
    ok = plan.length is None or not plan.length.explicit or len(wb.sheets) == plan.length.value
    if not ok:
        issues.append(PlanIssue(code="length", severity="critical", unit="workbook",
                                message=f"{len(wb.sheets)} sheets written, {plan.length.value} required"))
    return PlanReport(kind="workbook", stage="content", issues=issues, length_ok=ok)
