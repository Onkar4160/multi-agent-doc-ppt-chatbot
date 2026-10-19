"""Bridges between the typed IR (archetypes and blocks, Stage 7) and the renderers that predate it.

Until the archetype renderers of Stages 8–9 exist, a slide's typed blocks are shown as bullets or two columns and a
document's figures as data tables, so no planned content is lost in the file.
"""

from __future__ import annotations

from typing import Any

from app.models.deck_model import BulletItem, DeckModel, SlideModel

ROLE_FOR = {
    "title": "title", "section": "section_header", "two_column": "two_content", "comparison": "two_content",
    "closing": "title_only", "quote": "title_only",
}


def role_for(archetype: str | None) -> str:
    return ROLE_FOR.get(archetype or "", "title_content")


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:g}"


def block_bullets(block: Any) -> list[BulletItem]:
    t = getattr(block, "type", "")
    sids = list(getattr(block, "source_ids", []) or [])
    if t == "bullets":
        return [BulletItem(text=i.text, level=i.level, source_ids=i.source_ids or sids) for i in block.items]
    if t == "table":
        heads = block.headers or []
        rows = []
        for r in block.rows[:6]:
            cells = [str(c) for c in r]
            rows.append("; ".join(f"{h}: {c}" for h, c in zip(heads, cells)) if heads else " | ".join(cells))
        return [BulletItem(text=x, source_ids=sids) for x in rows]
    if t == "chart":
        s = block.series[0] if block.series else None
        unit = f" {block.unit}" if block.unit else ""
        return [BulletItem(text=f"{c}: {_fmt(v)}{unit}", source_ids=sids) for c, v in zip(block.categories, s.values if s else [])]
    if t == "kpi":
        return [BulletItem(text=f"{i.value} {i.label}" + (f" ({i.delta})" if i.delta else ""), source_ids=i.source_ids or sids)
                for i in block.items]
    if t == "timeline":
        return [BulletItem(text=f"{e.date}: {e.label}" + (f" ({e.detail})" if e.detail else ""), source_ids=sids) for e in block.events]
    if t == "process":
        return [BulletItem(text=f"{n}. {s.label}" + (f": {s.detail}" if s.detail else ""), source_ids=sids)
                for n, s in enumerate(block.steps, start=1)]
    if t == "quote":
        return [BulletItem(text=f"“{block.text}”" + (f" ({block.attribution})" if block.attribution else ""), source_ids=sids)]
    if t == "callout":
        return [BulletItem(text=block.text, source_ids=sids)]
    if t == "comparison":
        return [BulletItem(text=f"{c.heading}: " + "; ".join(c.points), source_ids=sids) for c in block.columns]
    return []


def legacy_view(deck: DeckModel) -> DeckModel:
    """A copy of ``deck`` whose typed blocks are also present as bullets / two columns for the current renderers."""
    out = deck.model_copy(deep=True)
    for slide in out.slides:
        _fill_legacy(slide)
    return out


def _fill_legacy(slide: SlideModel) -> None:
    if slide.archetype:
        slide.role = role_for(slide.archetype)
    if not slide.blocks or slide.bullets or slide.left or slide.right:
        return
    comparison = next((b for b in slide.blocks if getattr(b, "type", "") == "comparison"), None)
    if slide.role == "two_content" and comparison is not None and len(comparison.columns) >= 2:
        left, right = comparison.columns[0], comparison.columns[1]
        sids = list(comparison.source_ids)
        slide.left = [BulletItem(text=left.heading, source_ids=sids)] + [BulletItem(text=p, level=1, source_ids=sids) for p in left.points]
        slide.right = [BulletItem(text=right.heading, source_ids=sids)] + [BulletItem(text=p, level=1, source_ids=sids) for p in right.points]
        return
    items = [b for blk in slide.blocks for b in block_bullets(blk)]
    if slide.role == "two_content":
        half = (len(items) + 1) // 2
        slide.left, slide.right = items[:half], items[half:]
    elif slide.role == "title_only":
        if items and not slide.subtitle:
            slide.subtitle = items[0].text
    elif slide.role != "title":
        slide.bullets = items[:7]
