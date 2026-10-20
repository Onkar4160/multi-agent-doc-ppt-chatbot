"""Editorial polish of a deck's content before it is drawn (ADR-037).

Idempotent rules a presentation editor applies regardless of topic or template:
- headlines are assertions short enough to read at a glance (at most 14 words, cut at a clause boundary);
- citation markers live in ``source_ids`` and are printed once by the renderer, never inside the text as well;
- long integers get thousands separators;
- a "timeline" whose events do not have at least three distinct dates is shown as points, not a time axis;
- the same point is never listed twice on a slide.
Running it twice changes nothing, so an edited deck renders its untouched slides exactly as before.
"""

from __future__ import annotations

from typing import Any

from app.services.copyfit import group_digits, smart_trim, strip_citations

HEADLINE_WORDS = 14


def _text(t: str) -> str:
    from app.services.pptx_renderer import strip_markdown

    return group_digits(strip_citations(strip_markdown(t or "")))


def polish_slide(slide: Any) -> Any:
    from app.models.deck_model import BulletItem, SlideBulletsBlock

    full = " ".join(_text(slide.title).split())
    short = smart_trim(full, HEADLINE_WORDS)
    if short != full and not slide.key_message:
        slide.key_message = full
    slide.title = short
    slide.subtitle = _text(slide.subtitle)
    for items in (slide.bullets, slide.left, slide.right):
        seen: set[str] = set()
        keep = []
        for b in items:
            b.text = _text(b.text)
            key = b.text.lower().strip(" .")
            if key and key not in seen:
                seen.add(key)
                keep.append(b)
        items[:] = keep
    blocks = []
    for blk in slide.blocks:
        kind = getattr(blk, "type", "")
        if kind == "bullets":
            for item in blk.items:
                item.text = _text(item.text)
        elif kind == "kpi":
            for item in blk.items:
                item.value, item.label = group_digits(item.value), _text(item.label)
        elif kind == "timeline":
            dates = {e.date.strip().lower() for e in blk.events if e.date.strip()}
            if len(dates) < 3:  # "By 2026", "As of 2026", … is not a sequence in time
                items = [BulletItem(text=_text(f"{e.label}: {e.detail}" if e.detail else e.label),
                                    source_ids=list(blk.source_ids)) for e in blk.events]
                blk = SlideBulletsBlock(items=items, claim_ids=list(blk.claim_ids))
                if slide.archetype == "timeline":
                    slide.archetype = "bullets"
        elif kind in ("callout", "quote"):
            blk.text = _text(blk.text)
        blocks.append(blk)
    slide.blocks = blocks
    return slide


def polish_deck(deck: Any) -> Any:
    """Polish every slide in place and return the deck."""
    deck.title = smart_trim(" ".join(_text(deck.title).split()), HEADLINE_WORDS)
    for slide in deck.slides:
        polish_slide(slide)
    return deck
