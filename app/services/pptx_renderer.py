"""PPTX Rendering Service – turns DeckModel into clean PowerPoint presentation decks using real master layouts & placeholders."""

from __future__ import annotations

import logging
import math
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pptx
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt
from PIL import Image, ImageDraw

from app.models.deck_model import BulletItem, DeckModel, SlideModel
from app.models.template_profile import TemplateProfile

logger = logging.getLogger(__name__)


def strip_markdown(text: str) -> str:
    """Strip stray markdown markers (**text** -> text, __text__ -> text, leading #'s removed)."""
    if not text:
        return ""
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"__(.*?)__", r"\1", text)
    lines = [re.sub(r"^\s*#{1,6}\s*", "", line) for line in text.split("\n")]
    return "\n".join(lines)


def _is_slide_empty(slide_model: SlideModel) -> bool:
    """Check if a slide has zero content."""
    clean_title = strip_markdown(slide_model.title or "").strip()
    clean_subtitle = strip_markdown(slide_model.subtitle or "").strip()
    has_title = bool(clean_title)
    has_subtitle = bool(clean_subtitle)
    has_bullets = any(bool(strip_markdown(b.text or "").strip()) for b in slide_model.bullets)
    has_left = any(bool(strip_markdown(b.text or "").strip()) for b in slide_model.left)
    has_right = any(bool(strip_markdown(b.text or "").strip()) for b in slide_model.right)

    if slide_model.role in ("title", "section_header", "title_only"):
        return not (has_title or has_subtitle)
    elif slide_model.role == "two_content":
        return not (has_title or has_left or has_right)
    else:  # title_content / other
        return not (has_title or has_bullets)


def _send_shape_to_back(slide: Any, shape: Any) -> None:
    """Move shape to the back of the z-order (behind all placeholders and content)."""
    try:
        spTree = slide.shapes._spTree
        spTree.remove(shape.element)
        insert_idx = 0
        for idx, child in enumerate(spTree):
            tag = child.tag.split("}")[-1]
            if tag in ("nvGrpSpPr", "grpSpPr"):
                insert_idx = idx + 1
        spTree.insert(insert_idx, shape.element)
    except Exception as exc:
        logger.debug("Failed to adjust shape z-order: %s", exc)


def _resolve_accent_color(prs: pptx.Presentation, profile: TemplateProfile | None) -> str:
    """Resolve primary or accent color hex string from profile or template presentation XML."""
    if profile and profile.ppt_style and profile.ppt_style.theme_colors:
        tc = profile.ppt_style.theme_colors
        for key in ("primary", "accent", "accent1", "secondary"):
            val = tc.get(key)
            if val and val.startswith("#") and val.upper() not in ("#FFFFFF", "#FFF", "#000000", "#000"):
                return val
        for val in tc.values():
            if val and val.startswith("#") and val.upper() not in ("#FFFFFF", "#FFF", "#000000", "#000"):
                return val

    # Try extracting accent1/primary/dk2 from PPTX master theme XML
    try:
        master = prs.slide_masters[0]
        for rel in master.part.rels.values():
            if "theme" in rel.reltype:
                theme_blob = rel.target_part.blob.decode("utf-8", errors="ignore")
                m = re.search(r'<a:(?:accent1|dk2|accent2)>\s*<a:srgbClr val="([0-9A-Fa-f]{6})"', theme_blob)
                if m:
                    return f"#{m.group(1)}"
    except Exception:
        pass

    return "#1E3A8A"


def _hex_to_rgb(hex_str: str) -> RGBColor:
    """Convert hex string (e.g. #01B1ED or 1E3A8A) to RGBColor."""
    hex_clean = hex_str.lstrip("#")
    if len(hex_clean) == 3:
        hex_clean = "".join(c * 2 for c in hex_clean)
    if len(hex_clean) != 6:
        return RGBColor(30, 58, 138)
    r = int(hex_clean[0:2], 16)
    g = int(hex_clean[2:4], 16)
    b = int(hex_clean[4:6], 16)
    return RGBColor(r, g, b)


def _apply_light_decoration(slide: Any, prs: pptx.Presentation, accent_rgb: RGBColor) -> None:
    """Add light generated decoration behind text when source layout decoration_score is 0."""
    try:
        # 1. Thin accent bar positioned near the title
        if slide.shapes.title:
            t = slide.shapes.title
            bar_left = t.left
            bar_top = t.top + t.height + Inches(0.04)
            bar_width = min(Inches(1.8), t.width if t.width else Inches(1.8))
        else:
            bar_left = Inches(0.8)
            bar_top = Inches(1.2)
            bar_width = Inches(1.8)

        bar_height = Inches(0.04)
        bar = slide.shapes.add_shape(
            pptx.enum.shapes.MSO_SHAPE.RECTANGLE,
            bar_left,
            bar_top,
            bar_width,
            bar_height,
        )
        bar.name = "AutoDecoration_AccentBar"
        bar.fill.solid()
        bar.fill.fore_color.rgb = accent_rgb
        bar.line.fill.background()
        _send_shape_to_back(slide, bar)

        # 2. Subtle tinted rectangle in top-right corner
        corner_w = Inches(1.5)
        corner_h = Inches(0.06)
        corner_left = prs.slide_width - corner_w
        corner_top = Inches(0)
        corner = slide.shapes.add_shape(
            pptx.enum.shapes.MSO_SHAPE.RECTANGLE,
            corner_left,
            corner_top,
            corner_w,
            corner_h,
        )
        corner.name = "AutoDecoration_CornerTint"
        corner.fill.solid()
        corner.fill.fore_color.rgb = accent_rgb
        corner.line.fill.background()
        _send_shape_to_back(slide, corner)
    except Exception as exc:
        logger.warning("Failed to add auto decoration: %s", exc)



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
    accent_hex = _resolve_accent_color(prs, profile)
    accent_rgb = _hex_to_rgb(accent_hex)
    layouts_info = profile.ppt_style.layouts if (profile and profile.ppt_style) else []
    layout_dec_scores = {l.index: l.decoration_score for l in layouts_info}

    def get_rotated_layout_idx(role: str) -> tuple[int, str]:
        candidates = layout_candidates_by_role.get(role, [(0, "Default Title")])
        count = role_counters.get(role, 0)
        idx, name = candidates[count % len(candidates)]
        role_counters[role] = count + 1
        return idx, name

    all_referenced_source_ids: set[int] = set()

    # 3. Render slides
    for slide_idx, slide_model in enumerate(deck.slides, start=1):
        if _is_slide_empty(slide_model):
            logger.warning(
                "Skipping empty slide %d ('%s', role='%s') with zero content",
                slide_idx,
                slide_model.title,
                slide_model.role,
            )
            continue

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

        # Check source layout decoration score; if 0, add light decoration behind text
        source_dec_score = layout_dec_scores.get(layout_idx)
        if source_dec_score is None:
            l_non_ph = [s for s in layout.shapes if not s.is_placeholder]
            m_non_ph = [s for s in layout.slide_master.shapes if not s.is_placeholder]
            source_dec_score = len(l_non_ph) * 10 + len(m_non_ph) * 2

        if source_dec_score == 0:
            _apply_light_decoration(slide, prs, accent_rgb)
            logger.info("Slide %d: Applied auto-decoration (source layout '%s' dec_score=0)", slide_idx, layout_name)

        # Speaker notes
        if slide_model.notes and hasattr(slide, "notes_slide"):
            try:
                slide.notes_slide.notes_text_frame.text = strip_markdown(slide_model.notes)
            except Exception:
                pass

    # 4. Add Final "Sources" Slide if citations exist in the body
    if all_referenced_source_ids:
        sources_layout_idx, s_layout_name = get_rotated_layout_idx("title_content")
        src_slide = prs.slides.add_slide(prs.slide_layouts[sources_layout_idx])
        logger.info("Added Sources Slide using Layout %d ('%s')", sources_layout_idx, s_layout_name)

        if src_slide.shapes.title:
            _set_fitted_title(src_slide.shapes.title, "Sources & References", is_cover=False)

        ref_ids = sorted(list(all_referenced_source_ids))
        source_bullets: list[BulletItem] = []

        for sid in ref_ids:
            s_info = (sources_map or {}).get(sid) or (sources_map or {}).get(str(sid)) or {}
            kind = s_info.get("kind", "web")
            title = s_info.get("title", "")
            url_or_fn = s_info.get("url_or_filename") or s_info.get("url", "")
            accessed = s_info.get("accessed_at", "")

            if kind == "kb":
                text = f"{title} - {url_or_fn}" if (title and url_or_fn and title != url_or_fn) else (title or url_or_fn or f"Document {sid}")
            else:
                acc_str = f" (accessed {accessed})" if accessed else ""
                if title and url_or_fn:
                    text = f"{title} - {url_or_fn}{acc_str}"
                elif url_or_fn:
                    text = f"{url_or_fn}{acc_str}"
                else:
                    text = f"{title}{acc_str}"

            txt = f"[{sid}] {text}"
            source_bullets.append(BulletItem(text=txt, level=0, source_ids=[sid]))

        _fill_content_placeholder(src_slide, source_bullets, set())
        _process_picture_placeholders(src_slide)
        _remove_unused_placeholders(src_slide)

        # Check decoration score for sources slide layout
        src_dec_score = layout_dec_scores.get(sources_layout_idx)
        if src_dec_score is None:
            s_layout = prs.slide_layouts[sources_layout_idx]
            l_non_ph = [s for s in s_layout.shapes if not s.is_placeholder]
            m_non_ph = [s for s in s_layout.slide_master.shapes if not s.is_placeholder]
            src_dec_score = len(l_non_ph) * 10 + len(m_non_ph) * 2

        if src_dec_score == 0:
            _apply_light_decoration(src_slide, prs, accent_rgb)
            logger.info("Sources Slide: Applied auto-decoration (layout '%s' dec_score=0)", s_layout_name)

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
        sub_text = strip_markdown(model.subtitle or "Executive Presentation & Strategy")
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
            sub_text = strip_markdown(model.subtitle or "Strategic Focus Area & Key Initiatives")
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

    # Strip markdown and clean multi-space / line breaks
    clean_title = " ".join(strip_markdown(title_text).split())

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
    total_words = sum(len(strip_markdown(b.text).split()) for b in capped_bullets)
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
        clean_text = strip_markdown(b_item.text)
        words = clean_text.split()
        if len(words) > 22:
            text_str = " ".join(words[:22]) + "..."
        else:
            text_str = clean_text

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
