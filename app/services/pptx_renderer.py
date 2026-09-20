"""PPTX Rendering Service – turns DeckModel into clean PowerPoint presentation decks using real master layouts & placeholders."""

from __future__ import annotations

import logging
import math
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pptx
from pptx.util import Inches, Pt
from PIL import Image, ImageDraw

from app.models.deck_model import BulletItem, DeckModel, SlideModel
from app.models.template_profile import TemplateProfile

logger = logging.getLogger(__name__)


def render_pptx(
    deck: DeckModel,
    template_path: str | Path,
    profile: TemplateProfile | None = None,
    out_path: str | Path = "output.pptx",
    sources_map: dict[int, dict[str, Any]] | None = None,
) -> Path:
    """Render a DeckModel into a PowerPoint (.pptx) file using template slide layouts."""
    tmpl_path = Path(template_path)
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if not tmpl_path.exists():
        raise FileNotFoundError(f"PPTX template file not found: {template_path}")

    try:
        prs = pptx.Presentation(tmpl_path)
    except Exception as exc:
        raise ValueError(f"Failed to open template PPTX '{tmpl_path.name}': {exc}") from exc

    # 1. Cleanly remove all existing slides (drop relationships and sldId entries)
    for sldId in list(prs.slides._sldIdLst):
        rId = sldId.rId
        prs.part.drop_rel(rId)
        prs.slides._sldIdLst.remove(sldId)

    # 2. Layout Rotation & Candidate Resolution
    layout_candidates_by_role = _build_layout_candidates_map(prs, profile)
    role_counters: dict[str, int] = {}

    def get_rotated_layout_idx(role: str) -> tuple[int, str]:
        candidates = layout_candidates_by_role.get(role, [(0, "Default Title")])
        count = role_counters.get(role, 0)
        idx, name = candidates[count % len(candidates)]
        role_counters[role] = count + 1
        return idx, name

    all_referenced_source_ids: set[int] = set()

    # 3. Render slides
    for slide_idx, slide_model in enumerate(deck.slides, start=1):
        layout_idx, layout_name = get_rotated_layout_idx(slide_model.role)
        layout = prs.slide_layouts[layout_idx]
        slide = prs.slides.add_slide(layout)

        logger.info(
            "Slide %d ('%s') using Layout %d ('%s')",
            slide_idx,
            slide_model.title[:30],
            layout_idx,
            layout_name,
        )

        if slide_model.source_ids:
            all_referenced_source_ids.update(slide_model.source_ids)

        # ── COVER / TITLE SLIDE ──
        if slide_model.role == "title":
            _render_cover_title_slide(slide, slide_model)

        # ── SECTION HEADER SLIDE ──
        elif slide_model.role == "section_header":
            _render_section_header_slide(slide, slide_model)

        # ── TWO CONTENT SLIDE ──
        elif slide_model.role == "two_content":
            if slide.shapes.title and slide_model.title:
                _set_fitted_title(slide.shapes.title, slide_model.title, is_cover=False)
            _fill_two_content_placeholders(slide, slide_model.left, slide_model.right, all_referenced_source_ids)

        # ── STANDARD TITLE & CONTENT SLIDE ──
        else:
            if slide.shapes.title and slide_model.title:
                _set_fitted_title(slide.shapes.title, slide_model.title, is_cover=False)
            if slide_model.bullets:
                _fill_content_placeholder(slide, slide_model.bullets, all_referenced_source_ids)

        # Handle picture placeholders on layout
        _process_picture_placeholders(slide)

        # Remove any empty/unused text placeholders
        _remove_unused_placeholders(slide)

        # Speaker notes
        if slide_model.notes and hasattr(slide, "notes_slide"):
            try:
                slide.notes_slide.notes_text_frame.text = slide_model.notes
            except Exception:
                pass

    # 4. Add Final "Sources" Slide if citations exist
    if all_referenced_source_ids or sources_map:
        sources_layout_idx, s_layout_name = get_rotated_layout_idx("title_content")
        src_slide = prs.slides.add_slide(prs.slide_layouts[sources_layout_idx])
        logger.info("Added Sources Slide using Layout %d ('%s')", sources_layout_idx, s_layout_name)

        if src_slide.shapes.title:
            _set_fitted_title(src_slide.shapes.title, "Sources & References", is_cover=False)

        ref_ids = sorted(list(all_referenced_source_ids)) if all_referenced_source_ids else (sorted(list(sources_map.keys())) if sources_map else [])
        source_bullets: list[BulletItem] = []

        for sid in ref_ids:
            s_info = (sources_map or {}).get(sid, {})
            title = s_info.get("title") or f"Source {sid}"
            url = s_info.get("url") or ""
            txt = f"[{sid}] {title}" + (f" ({url})" if url else "")
            source_bullets.append(BulletItem(text=txt, level=0, source_ids=[sid]))

        _fill_content_placeholder(src_slide, source_bullets, set())
        _process_picture_placeholders(src_slide)
        _remove_unused_placeholders(src_slide)

    prs.save(destination)
    logger.info("Successfully rendered PPTX deck with %d slides to %s", len(prs.slides), destination)
    return destination


# ── Cover Slide Formatting & Fit Control ───────────────────────────────────

def _render_cover_title_slide(slide: Any, model: SlideModel) -> None:
    """Render cover title slide with word-wrap title fitting and subtitle spacing."""
    if slide.shapes.title:
        _set_fitted_title(slide.shapes.title, model.title, is_cover=True)

    # Fill subtitle placeholder
    subtitle_shape = None
    for shape in slide.placeholders:
        if shape != slide.shapes.title and ("sub" in shape.name.lower() or shape.placeholder_format.idx == 1):
            subtitle_shape = shape
            break

    if subtitle_shape and subtitle_shape.has_text_frame:
        sub_text = model.subtitle or "NexaWorks AI Solutions | Enterprise Strategy"
        tf = subtitle_shape.text_frame
        tf.word_wrap = True
        tf.text = sub_text
        if tf.paragraphs:
            p = tf.paragraphs[0]
            p.font.size = Pt(20)


def _render_section_header_slide(slide: Any, model: SlideModel) -> None:
    """Render section header slide filling title and subtitle/description placeholder."""
    if slide.shapes.title:
        _set_fitted_title(slide.shapes.title, model.title, is_cover=False)

    for shape in slide.placeholders:
        if shape != slide.shapes.title and shape.has_text_frame:
            sub_text = model.subtitle or "Strategic Focus Area & Key Initiatives"
            tf = shape.text_frame
            tf.word_wrap = True
            tf.text = sub_text
            if tf.paragraphs:
                tf.paragraphs[0].font.size = Pt(18)
            break


def _set_fitted_title(shape: Any, title_text: str, is_cover: bool = False) -> None:
    """Set title text with word wrapping and font scaling to prevent line overflow."""
    tf = shape.text_frame
    tf.word_wrap = True

    # Clean multi-space / line breaks
    clean_title = " ".join(title_text.split())

    # Truncate if unusually long (> 3 lines / 20 words)
    words = clean_title.split()
    if len(words) > 20:
        clean_title = " ".join(words[:20]) + "..."

    tf.text = clean_title
    if not tf.paragraphs:
        return

    p = tf.paragraphs[0]

    # Calculate target font size based on title length
    start_pt = 36 if is_cover else 28
    min_pt = 28 if is_cover else 24

    char_count = len(clean_title)
    if char_count > 60:
        fitted_pt = max(min_pt, start_pt - 6)
    elif char_count > 40:
        fitted_pt = max(min_pt, start_pt - 4)
    else:
        fitted_pt = start_pt

    if fitted_pt < start_pt:
        logger.info("Title font reduced from %d pt to %d pt for fit: '%s'", start_pt, fitted_pt, clean_title[:30])

    p.font.size = Pt(fitted_pt)


# ── Body Content & Bullet Fit Control ──────────────────────────────────────

def _fill_content_placeholder(slide: Any, bullets: list[BulletItem], cited_set: set[int]) -> None:
    """Fill body content placeholder with fitted bullets (min font size 16pt)."""
    body_shape = None
    for shape in slide.placeholders:
        if shape != slide.shapes.title and shape.has_text_frame and shape.placeholder_format.type != pptx.enum.shapes.PP_PLACEHOLDER.PICTURE:
            body_shape = shape
            break

    if not body_shape:
        return

    tf = body_shape.text_frame
    tf.word_wrap = True
    _populate_text_frame(tf, bullets, cited_set, min_font_pt=16)


def _fill_two_content_placeholders(
    slide: Any,
    left_bullets: list[BulletItem],
    right_bullets: list[BulletItem],
    cited_set: set[int],
) -> None:
    """Fill left and right content placeholders for two-column slides (min font size 14pt)."""
    content_shapes = [
        shape for shape in slide.placeholders
        if shape != slide.shapes.title and shape.has_text_frame and shape.placeholder_format.type != pptx.enum.shapes.PP_PLACEHOLDER.PICTURE
    ]

    if len(content_shapes) >= 1 and left_bullets:
        tf_left = content_shapes[0].text_frame
        tf_left.word_wrap = True
        _populate_text_frame(tf_left, left_bullets, cited_set, min_font_pt=14)

    if len(content_shapes) >= 2 and right_bullets:
        tf_right = content_shapes[1].text_frame
        tf_right.word_wrap = True
        _populate_text_frame(tf_right, right_bullets, cited_set, min_font_pt=14)


def _populate_text_frame(
    tf: Any,
    bullets: list[BulletItem],
    cited_set: set[int],
    min_font_pt: int = 16,
) -> None:
    """Populate text frame with bullet items enforcing minimum font size limits."""
    # Fit control: cap at max 6 bullets per placeholder
    capped_bullets = bullets[:6]

    # Calculate font size: default 18pt, reduce in steps of 2pt if text is long, never below min_font_pt
    total_words = sum(len(b.text.split()) for b in capped_bullets)
    if total_words > 90:
        target_pt = max(min_font_pt, 14)
    elif total_words > 60:
        target_pt = max(min_font_pt, 16)
    else:
        target_pt = 18

    if target_pt < 18:
        logger.info("Body bullet font size reduced to %d pt (total words=%d)", target_pt, total_words)

    for i, b_item in enumerate(capped_bullets):
        if i == 0 and len(tf.paragraphs) > 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()

        # Word wrap check per bullet (12-20 words)
        words = b_item.text.split()
        if len(words) > 22:
            text_str = " ".join(words[:22]) + "..."
        else:
            text_str = b_item.text

        if b_item.source_ids:
            unique_ids = sorted(list(set(b_item.source_ids)))
            citation_str = " " + "".join(f"[{sid}]" for sid in unique_ids)
            text_str += citation_str
            cited_set.update(b_item.source_ids)

        p.text = text_str
        p.level = min(1, max(0, b_item.level))
        p.font.size = Pt(target_pt)


# ── Picture & Placeholder Clean-up ─────────────────────────────────────────

def _process_picture_placeholders(slide: Any) -> None:
    """Fill picture placeholders with generated abstract image or delete if unfilled."""
    pic_shapes = [
        shape for shape in slide.placeholders
        if shape.placeholder_format.type == pptx.enum.shapes.PP_PLACEHOLDER.PICTURE
    ]

    for pic_shape in pic_shapes:
        try:
            # Generate abstract theme image matching placeholder aspect ratio
            w_in = pic_shape.width.inches if pic_shape.width else 4.0
            h_in = pic_shape.height.inches if pic_shape.height else 3.0
            img_path = _generate_abstract_theme_image(w_in, h_in)

            # Insert picture into placeholder
            pic_shape.insert_picture(str(img_path))
            if img_path.exists():
                img_path.unlink()
        except Exception as exc:
            logger.warning("Failed to fill picture placeholder: %s. Deleting placeholder shape.", exc)
            sp = pic_shape.element
            sp.getparent().remove(sp)


def _remove_unused_placeholders(slide: Any) -> None:
    """Remove empty, unused text placeholders from slide."""
    unused = []
    for shape in slide.placeholders:
        if shape == slide.shapes.title:
            continue
        if shape.has_text_frame and not shape.text_frame.text.strip():
            unused.append(shape)

    for shape in unused:
        try:
            sp = shape.element
            sp.getparent().remove(sp)
        except Exception:
            pass


def _generate_abstract_theme_image(width_in: float, height_in: float) -> Path:
    """Generate abstract geometric image using brand palette matching placeholder ratio."""
    width_px = int(max(200, width_in * 100))
    height_px = int(max(200, height_in * 100))

    img = Image.new("RGB", (width_px, height_px), (30, 58, 138))  # Primary Navy
    draw = ImageDraw.Draw(img)

    # Accent geometric polygons (Teal & Muted Grey)
    draw.polygon([(0, height_px), (width_px, height_px // 2), (width_px, height_px)], fill=(13, 148, 136))
    draw.polygon([(width_px // 2, 0), (width_px, 0), (width_px, height_px // 3)], fill=(245, 158, 11))
    draw.rectangle([10, 10, width_px - 10, height_px - 10], outline=(209, 213, 219), width=2)

    temp_file = Path(tempfile.gettempdir()) / f"abs_img_{uuid.uuid4().hex[:8]}.png"
    img.save(temp_file, "PNG")
    return temp_file


# ── Helper for Candidate Layout Rotation ───────────────────────────────────

def _build_layout_candidates_map(prs: pptx.Presentation, profile: TemplateProfile | None) -> dict[str, list[tuple[int, str]]]:
    """Build candidate layout lists sorted by decoration score for rotation."""
    result: dict[str, list[tuple[int, str]]] = {
        "title": [],
        "section_header": [],
        "title_content": [],
        "two_content": [],
        "title_only": [],
    }

    layouts_info = profile.ppt_style.layouts if (profile and profile.ppt_style) else []
    dec_scores = {l.index: l.decoration_score for l in layouts_info}

    for idx, layout in enumerate(prs.slide_layouts):
        ph_types = [str(ph.placeholder_format.type).split(".")[-1].lower() for ph in layout.placeholders]
        score = dec_scores.get(idx, 0)
        l_name = layout.name.lower()

        if "title" in l_name and len(layout.placeholders) <= 3:
            result["title"].append((score, idx, layout.name))
        if "section" in l_name or "header" in l_name:
            result["section_header"].append((score, idx, layout.name))
        if "content" in l_name or "body" in ph_types or "object" in ph_types:
            result["title_content"].append((score, idx, layout.name))
        if "two" in l_name or ph_types.count("body") >= 2 or ph_types.count("object") >= 2:
            result["two_content"].append((score, idx, layout.name))
        if "only" in l_name or len(layout.placeholders) == 1:
            result["title_only"].append((score, idx, layout.name))

    final_map: dict[str, list[tuple[int, str]]] = {}
    for role, items in result.items():
        if items:
            items.sort(key=lambda x: -x[0])
            final_map[role] = [(item[1], item[2]) for item in items]
        else:
            default_idx = 0 if role == "title" else min(1, len(prs.slide_layouts) - 1)
            final_map[role] = [(default_idx, prs.slide_layouts[default_idx].name)]

    return final_map
