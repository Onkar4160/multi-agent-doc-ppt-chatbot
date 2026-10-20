"""What the editor sees and the precise operations it can apply (FR-EDIT-2/3, ADR-036).

An edit is only as precise as the editor's view of the file. The editor gets the real content of every unit —
numbered points, titles, block contents, node IDs — so "change the second point on the risks slide" or "replace 40%
with 45%" can be expressed as one small operation instead of rewriting a slide it has never seen. Everything the
operations do not touch is copied from the previous version unchanged.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.models.deck_model import BulletItem, DeckModel, SlideBulletsBlock
from app.models.document_model import DocumentModel

PARA_CHARS = 700  # paragraph text shown per block; targeted units are shown in full


def points(slide: Any, *, create: bool = True) -> list[BulletItem]:
    """The slide's visible points as a live list (typed bullet block first, legacy bullets otherwise).

    create adds an empty points block to a slide that has only visuals (for an added point); reading never does.
    """
    block = next((b for b in slide.blocks if getattr(b, "type", "") == "bullets"), None)
    if block is not None:
        return block.items
    if slide.bullets or not slide.blocks or not create:
        return slide.bullets
    block = SlideBulletsBlock(items=[])
    slide.blocks.append(block)
    return block.items


def _block_view(block: Any) -> Any:
    kind = getattr(block, "type", "")
    if kind == "bullets":
        return None  # shown as numbered points
    data = block.model_dump(exclude={"node_id", "claim_ids"}, exclude_defaults=True)
    data["type"] = kind
    return data


def outline_for_editor(model: Any, targets: list[str] | None = None) -> str:
    """Content of every unit with its node_id; units named in ``targets`` in full, others with long text cut."""
    targets = set(targets or [])

    def full(uid: str | None) -> bool:
        return not targets or any(t == uid or t.startswith(f"{uid}_") for t in targets)

    lines: list[str] = []
    if isinstance(model, DeckModel):
        lines.append(f"DECK TITLE: {model.title}")
        for i, s in enumerate(model.slides, start=1):
            lines.append(f"\n[Slide {i}] node_id={s.node_id} archetype={s.archetype or s.role}")
            lines.append(f"  title: {s.title}")
            if s.subtitle:
                lines.append(f"  subtitle: {s.subtitle}")
            for n, p in enumerate(points(s, create=False), start=1):
                cite = f" (sources {p.source_ids})" if p.source_ids else ""
                lines.append(f"  point {n}: {p.text}{cite}")
            for side, items in (("left", s.left), ("right", s.right)):
                for n, p in enumerate(items, start=1):
                    lines.append(f"  {side} {n}: {p.text}")
            for b in s.blocks:
                view = _block_view(b)
                if view is not None:
                    lines.append("  block: " + json.dumps(view, ensure_ascii=False)[: 1500 if full(s.node_id) else 400])
            if s.notes:
                lines.append(f"  notes: {s.notes[: 600 if full(s.node_id) else 160]}")
    elif isinstance(model, DocumentModel):
        lines.append(f"DOCUMENT TITLE: {model.title}")
        for i, sec in enumerate(model.sections, start=1):
            lines.append(f"\n[Section {i}] node_id={sec.node_id} heading: {sec.heading}")
            limit = 4000 if full(sec.node_id) else PARA_CHARS
            for b in sec.blocks:
                kind = getattr(b, "type", "")
                text = getattr(b, "text", None)
                if text is not None:
                    lines.append(f"  {kind} {b.node_id}: {text[:limit]}")
                elif kind == "bullets":
                    items = [getattr(it, "text", str(it)) for it in getattr(b, "items", [])]
                    lines.append(f"  bullets {b.node_id}: " + " | ".join(items)[:limit])
                else:
                    lines.append(f"  {kind} {b.node_id}: " + json.dumps(b.model_dump(exclude={"node_id"}), ensure_ascii=False)[:600])
    else:
        for i, sh in enumerate(getattr(model, "sheets", []), start=1):
            lines.append(f"[Sheet {i}] node_id={sh.node_id} name: {sh.name} ({len(sh.tables)} tables, {len(sh.cells)} cells)")
    return "\n".join(lines)


def normalize_targets(model: Any, targets: list[str] | None) -> list[str]:
    """Node IDs for targets given as IDs or labels ('slide 5', 'executive summary'); unknown labels are dropped."""
    out: list[str] = []
    units = getattr(model, "slides", None) or getattr(model, "sections", None) or getattr(model, "sheets", None) or []
    ids = {u.node_id for u in units if u.node_id}
    for t in targets or []:
        t = str(t).strip()
        if t in ids or any(t.startswith(f"{i}_") for i in ids):
            out.append(t)
            continue
        m = re.fullmatch(r"(?:slide|page|section|sheet)[\s_]*0*(\d+)", t, re.I)
        if m and 1 <= int(m.group(1)) <= len(units):
            out.append(units[int(m.group(1)) - 1].node_id)
            continue
        label = t.lower()
        for u in units:
            name = (getattr(u, "heading", None) or getattr(u, "title", None) or getattr(u, "name", "") or "").lower()
            if label and name and (label in name or name in label):
                out.append(u.node_id)
                break
    return list(dict.fromkeys(x for x in out if x))


def _replace_in(obj: Any, find: str, repl: str) -> int:
    """Replace ``find`` in every string field of a pydantic object tree; returns the number of fields changed."""
    count = 0
    for name, value in list(obj.__dict__.items()):
        if name in ("node_id", "type", "archetype", "role"):
            continue
        if isinstance(value, str) and find in value:
            setattr(obj, name, value.replace(find, repl))
            count += 1
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, str) and find in item:
                    value[i] = item.replace(find, repl)
                    count += 1
                elif hasattr(item, "__dict__"):
                    count += _replace_in(item, find, repl)
        elif hasattr(value, "__dict__") and not isinstance(value, type):
            count += _replace_in(value, find, repl)
    return count


def apply_precise_ops(model: Any, ops: list[Any]) -> list[str]:
    """Apply point/title/replace operations in place; returns the node_ids of the units they changed."""
    touched: list[str] = []
    for op in ops:
        kind = getattr(op, "type", "")
        if isinstance(model, DeckModel) and kind in ("update_point", "add_point", "delete_point", "update_slide_text"):
            if not 1 <= op.slide_index <= len(model.slides):
                continue
            slide = model.slides[op.slide_index - 1]
            items = points(slide, create=kind == "add_point")
            if kind == "update_point" and 1 <= op.point_index <= len(items):
                item = items[op.point_index - 1]
                item.text = op.new_text
                if op.source_ids is not None:
                    item.source_ids = list(op.source_ids)
            elif kind == "add_point":
                at = len(items) if op.after_point < 0 else min(op.after_point, len(items))
                items.insert(at, BulletItem(text=op.text, source_ids=list(op.source_ids)))
            elif kind == "delete_point" and 1 <= op.point_index <= len(items):
                items.pop(op.point_index - 1)
            elif kind == "update_slide_text":
                setattr(slide, op.field, op.new_text)
            else:
                continue
            touched.append(slide.node_id)
        elif kind == "replace_text" and op.find:
            units = getattr(model, "slides", None) or getattr(model, "sections", None) or []
            for unit in units:
                if unit.node_id == op.node_id and _replace_in(unit, op.find, op.replace):
                    touched.append(unit.node_id)
    return list(dict.fromkeys(touched))


def op_units(model: Any, ops: list[Any]) -> list[str]:
    """Node IDs of the existing units the structural ops address (for the scope guard)."""
    slides = getattr(model, "slides", None) or []
    sections = getattr(model, "sections", None) or []
    out: list[str] = []
    for op in ops:
        kind = getattr(op, "type", "")
        idx = getattr(op, "index", None) or getattr(op, "slide_index", None)
        if slides and idx and 1 <= idx <= len(slides):
            out.append(slides[idx - 1].node_id)
        if kind == "update_title" and slides:
            out += [s.node_id for s in slides if s.role == "title"][:1]
        heading = (getattr(op, "heading", None) or "").lower()
        if sections and heading:
            out += [s.node_id for s in sections if s.heading.lower() == heading]
        if kind in ("replace_text",):
            out.append(op.node_id)
    return [x for x in dict.fromkeys(out) if x]
