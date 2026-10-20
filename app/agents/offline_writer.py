"""Deterministic writing from a validated plan and the claim digest (offline mode; gap filling in real mode).

Only claim statements carry facts, always with their source IDs; any other text is plain, non-factual framing. Used
for every unit when the LLM is mocked, and for single units a real writer left out (so the planned length holds).
"""

from __future__ import annotations

import re

from app.models.blocks import (
    CalloutBlock, ChartBlock, ChartSeries, ComparisonBlock, ComparisonColumn, ImageBlock, KpiBlock, KpiItem, ProcessBlock,
    ProcessStep, QuoteBlock, TimelineBlock, TimelineEvent,
)
from app.models.content_plan import DeckPlan, DocumentPlan, SectionPlan, SlidePlan, WorkbookPlan
from app.models.deck_model import BulletItem, DeckModel, SlideBulletsBlock, SlideModel
from app.models.document_model import BulletsBlock, DocumentModel, ParagraphBlock, Section, TableBlock
from app.models.workbook_model import (
    CellModel, ChartModel, ExpectedCheck, NamedRange, SheetModel, TableModel, WorkbookModel,
)
from app.services.ir_compat import role_for
from app.services.plan_inputs import YEAR, ClaimRef, by_id

DEFAULT_STEPS = [("Assess", "establish the starting point and the goals"), ("Plan", "agree scope, owners and milestones"),
                 ("Pilot", "test on a small scale and measure"), ("Scale", "extend what works and review regularly")]


def _clean(statement: str) -> str:
    return re.sub(r"\s*\[[^\]]*\]", "", statement).strip()


def _label(statement: str, words: int = 6) -> str:
    from app.services.copyfit import smart_trim

    s = _clean(statement).rstrip(".")
    s = re.sub(r"^(the|a|an)\s+", "", s, flags=re.I)
    return smart_trim(s, words, min_words=min(3, words))


def _figure(c: ClaimRef) -> str:
    from app.services.copyfit import format_number

    unit = (c.unit or "").strip()
    return f"{format_number(c.value)}{unit}" if unit in ("%", "x") else f"{format_number(c.value)} {unit}".strip()


def main_unit(claims: list[ClaimRef]) -> str:
    """The unit most figures share: only those may be added up."""
    from collections import Counter

    return Counter(c.unit or "" for c in claims if c.numeric).most_common(1)[0][0] if claims else ""


def _items(sp_ids: list[str], cmap: dict[str, ClaimRef]) -> list[ClaimRef]:
    return [cmap[c] for c in sp_ids if c in cmap]


def write_slide(sp: SlidePlan, claims: list[ClaimRef], *, deck_title: str = "", agenda: list[str] | None = None) -> SlideModel:
    cmap = by_id(claims)
    items = _items(sp.claim_ids, cmap)
    sids = sorted({s for c in items for s in c.source_ids}) or list(sp.source_ids)
    slide = SlideModel(role=role_for(sp.archetype), archetype=sp.archetype, title=sp.headline, key_message=sp.key_message,
                       claim_ids=list(sp.claim_ids), source_ids=sids,
                       notes=f"{sp.notes_intent} {sp.key_message}".strip() if sp.archetype != "title" else "")
    a = sp.archetype
    if a == "title":
        slide.title = deck_title or sp.headline
        return slide
    if a in ("section", "closing", "references"):
        slide.subtitle = sp.key_message or ("Questions and discussion" if a == "closing" else "")
        return slide
    bullets = [BulletItem(text=_clean(c.statement), source_ids=c.source_ids) for c in items[:5]]
    if a == "agenda":
        slide.blocks = [SlideBulletsBlock(items=[BulletItem(text=x) for x in (agenda or [])[:7]])]
    elif a == "chart":
        numeric = [c for c in items if c.numeric]
        slide.blocks = [ChartBlock(chart_type="column", title=sp.headline, categories=[_label(c.statement, 4) for c in numeric],
                                   series=[ChartSeries(name=numeric[0].unit or "Value", values=[c.value for c in numeric])],
                                   unit=numeric[0].unit or "", source_ids=sids, claim_ids=[c.id for c in numeric])]
        if sp.key_message:
            slide.blocks.append(CalloutBlock(text=_clean(sp.key_message), source_ids=sids))
    elif a == "kpi":
        numeric = [c for c in items if c.numeric][:4]
        slide.blocks = [KpiBlock(items=[KpiItem(value=_figure(c), label=_label(c.statement, 10),
                                                source_ids=c.source_ids) for c in numeric],
                                 source_ids=sids, claim_ids=[c.id for c in numeric])]
    elif a == "timeline":
        events = []
        for c in [c for c in items if c.dated]:  # undated claims cannot sit on a time axis
            m = YEAR.search(c.statement)
            events.append(TimelineEvent(date=c.as_of or (m.group(0) if m else ""), label=_label(c.statement, 8)))
        events.sort(key=lambda e: e.date)
        slide.blocks = [TimelineBlock(events=events, source_ids=sids, claim_ids=[c.id for c in items])]
    elif a == "process":
        steps = [ProcessStep(label=_label(c.statement, 4), detail=_clean(c.statement)) for c in items] or [
            ProcessStep(label=label, detail=detail) for label, detail in DEFAULT_STEPS]
        slide.blocks = [ProcessBlock(steps=steps, source_ids=sids if items else [], claim_ids=[c.id for c in items])]
    elif a in ("comparison", "two_column"):
        half = max(1, (len(items) + 1) // 2)
        left, right = items[:half], items[half:] or items[:1]
        slide.blocks = [ComparisonBlock(columns=[ComparisonColumn(heading="Evidence for", points=[_clean(c.statement) for c in left]),
                                                 ComparisonColumn(heading="Constraints", points=[_clean(c.statement) for c in right])],
                                        source_ids=sids, claim_ids=[c.id for c in items])]
    elif a == "quote":
        lead = items[0] if items else None
        slide.blocks = [QuoteBlock(text=_clean(lead.statement) if lead else sp.key_message or sp.headline,
                                   source_ids=lead.source_ids if lead else [])]
    elif a == "table":
        slide.blocks = [TableBlock(headers=["Finding", "Figure", "As of"],
                                   rows=[[_label(c.statement, 10), _figure(c) if c.numeric else "",
                                          c.as_of or ""] for c in items], source_ids=sids, claim_ids=[c.id for c in items])]
    elif a == "image_text":
        slide.blocks = [ImageBlock(description=sp.headline, alt_text=sp.headline)]
        slide.blocks.append(SlideBulletsBlock(items=bullets or [BulletItem(text=sp.purpose)]))
    else:  # bullets, summary
        if not bullets:
            bullets = [BulletItem(text=x) for x in (sp.key_message, f"{sp.purpose}.", sp.notes_intent) if x]
        slide.blocks = [SlideBulletsBlock(items=bullets, claim_ids=[c.id for c in items])]
    return slide


def write_deck(plan: DeckPlan, claims: list[ClaimRef]) -> DeckModel:
    agenda = [s.headline for s in plan.slides if s.archetype not in ("title", "agenda", "closing", "references")]
    return DeckModel(title=plan.title, subtitle=plan.subtitle,
                     slides=[write_slide(sp, claims, deck_title=plan.title, agenda=agenda) for sp in plan.slides])


def write_section(sp: SectionPlan, claims: list[ClaimRef]) -> Section:
    cmap = by_id(claims)
    items = _items(sp.claim_ids, cmap)
    blocks: list = []
    lead = _clean(sp.argument) if sp.argument else ""
    facts = [c for c in items]
    para_facts = facts[:3]
    if para_facts:
        text = " ".join([lead.rstrip(".") + "." if lead and lead not in (_clean(c.statement) for c in para_facts) else ""]
                        + [_clean(c.statement).rstrip(".") + "." for c in para_facts]).strip()
        blocks.append(ParagraphBlock(text=text, source_ids=sorted({s for c in para_facts for s in c.source_ids}),
                                     claim_ids=[c.id for c in para_facts]))
    elif lead:
        blocks.append(ParagraphBlock(text=f"{lead.rstrip('.')}. This section covers {sp.purpose.lower().rstrip('.')}."))
    if "table" in sp.blocks:
        numeric = [c for c in items if c.numeric]
        if numeric:
            blocks.append(TableBlock(headers=["Finding", "Figure", "As of"],
                                     rows=[[_label(c.statement, 10), f"{c.value:g} {c.unit or ''}".strip(), c.as_of or ""] for c in numeric],
                                     source_ids=sorted({s for c in numeric for s in c.source_ids}), claim_ids=[c.id for c in numeric]))
    rest = facts[3:]
    if rest:
        blocks.append(BulletsBlock(items=[_clean(c.statement) for c in rest],
                                   source_ids=sorted({s for c in rest for s in c.source_ids}), claim_ids=[c.id for c in rest]))
    if not blocks:
        blocks.append(ParagraphBlock(text=f"This section covers {sp.purpose.lower().rstrip('.') or sp.heading.lower()}."))
    return Section(heading=sp.heading, level=sp.level, purpose=sp.purpose, blocks=blocks)


def write_document(plan: DocumentPlan, claims: list[ClaimRef]) -> DocumentModel:
    sections = [write_section(sp, claims) for sp in plan.sections]
    summary = ""
    if plan.executive_summary:
        points = [_clean(sp.argument).rstrip(".") + "." for sp in plan.sections if sp.argument][:5]
        summary = " ".join(points)
    return DocumentModel(title=plan.title, subtitle=plan.subtitle, executive_summary=summary, sections=sections)


def write_workbook(plan: WorkbookPlan, claims: list[ClaimRef]) -> WorkbookModel:
    cmap = by_id(claims)
    sheets: list[SheetModel] = []
    data_plan = next((s for s in plan.sheets if s.role == "data"), None)
    numeric_rows = [cmap[c] for c in (data_plan.claim_ids if data_plan else []) if c in cmap and cmap[c].numeric]
    for sp in plan.sheets:
        sheet = SheetModel(name=sp.name, role=sp.role)
        if sp.role == "readme":
            lines = [plan.title, sp.purpose, ""] + [f"{s.name}: {s.purpose}" for s in plan.sheets if s is not sp]
            sheet.cells = [CellModel(coordinate=f"A{i}", value=v) for i, v in enumerate(lines, start=1) if v != ""]
        elif sp.role == "assumptions":
            rows = [[a.name, a.value, a.unit, (f"[{a.source_id}]" if a.source_id else a.rationale)] for a in plan.assumptions]
            sheet.tables = [TableModel(name="Assumptions", range=f"A1:D{len(rows) + 1}", headers=[c.name for c in sp.columns][:4]
                                       or ["Driver", "Value", "Unit", "Source"], rows=rows)]
        elif sp.role == "data":
            items = numeric_rows if sp is data_plan else [cmap[c] for c in sp.claim_ids if c in cmap and cmap[c].numeric]
            if items:
                rows = [[_label(c.statement, 8), c.value, c.unit or "", c.as_of or "", c.source_ids[0] if c.source_ids else ""] for c in items]
            else:
                rows = []
            headers = [c.name for c in sp.columns] or ["Metric", "Value", "Unit", "As of", "Source"]
            sheet.tables = [TableModel(name="Data", range=f"A1:{chr(64 + len(headers))}{max(2, len(rows) + 1)}", headers=headers, rows=rows)]
        elif sp.role == "calculations":
            n = len(numeric_rows)
            if n:
                unit = main_unit(numeric_rows)  # only figures in the same unit may be added up
                rng = f'Data!C2:C{n + 1},"{unit}",Data!B2:B{n + 1}'
                sheet.cells = [CellModel(coordinate="A1", value="Measure"), CellModel(coordinate="B1", value="Result"),
                               CellModel(coordinate="A2", value=f"Total ({unit})"),
                               CellModel(coordinate="B2", formula=f"=SUMIF({rng})", data_type="formula"),
                               CellModel(coordinate="A3", value=f"Average ({unit})"),
                               CellModel(coordinate="B3", formula=f"=AVERAGEIF({rng})", data_type="formula")]
            else:
                sheet.cells = [CellModel(coordinate="A1", value="Measure"), CellModel(coordinate="B1", value="Result"),
                               CellModel(coordinate="A2", value="Total amount"),
                               CellModel(coordinate="B2", formula="=SUM(Data!C2:C1000)", data_type="formula")]
        elif sp.role in ("report", "dashboard"):
            sheet.cells = [CellModel(coordinate="A1", value=plan.title)]
            if numeric_rows:
                n = len(numeric_rows)
                sheet.charts = [ChartModel(title=f"{plan.title}: figures", chart_type="column",
                                           data_range=f"Data!$B$1:$B${n + 1}", categories_range=f"Data!$A$2:$A${n + 1}")]
        elif sp.role == "sources":
            ids = sorted({s for c in claims for s in c.source_ids})
            sheet.tables = [TableModel(name="Sources", range=f"A1:B{len(ids) + 1}", headers=["ID", "Source"],
                                       rows=[[i, f"Source [{i}]"] for i in ids])]
        else:
            sheet.cells = [CellModel(coordinate="A1", value=sp.purpose or sp.name)]
        sheets.append(sheet)
    return WorkbookModel(
        title=plan.title, sheets=sheets,
        named_ranges=[NamedRange(name=a.name, ref=f"Assumptions!$B${i}") for i, a in enumerate(plan.assumptions, start=2)
                      if any(s.role == "assumptions" for s in plan.sheets)],
        checks=[ExpectedCheck(ref=c.ref, expected=c.expected, tolerance=c.tolerance, description=c.description)
                for c in plan.checks if c.ref and c.expected is not None],
    )


def main_unit(claims: list[ClaimRef]) -> str:
    """The unit most figures share."""
    from collections import Counter

    return Counter(c.unit or "" for c in claims if c.numeric).most_common(1)[0][0] if claims else ""
