"""Deterministic Quality Validator for generated DOCX documents and PPTX presentation decks."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.deck_model import DeckModel
from app.models.document_model import DocumentModel


class ValidationIssue(BaseModel):
    """An issue identified by deterministic quality validation."""

    severity: Literal["error", "warning"]
    where: str  # e.g., "pptx:slide_3" or "docx:section_2"
    message: str


class ValidationReport(BaseModel):
    """Unified validation report detailing pass/fail status and issues."""

    passed: bool
    score: float = Field(ge=0.0, le=100.0)
    issues: list[ValidationIssue] = Field(default_factory=list)


PLACEHOLDER_REGEX = re.compile(
    r"(click to add|lorem ipsum|insert text|\[placeholder\]|<placeholder>|sample text)",
    re.IGNORECASE,
)
NUMBER_OR_PERCENT_REGEX = re.compile(
    r"(\d+%|\$\d+|\d+\s*percent|\b\d{2,}\b|\bv?\d+\.\d+\b|\b(?:19|20)\d{2}\b|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b|\bversion\s+\d+\b|\brelease\s+\d+\b)",
    re.IGNORECASE,
)


def validate_outputs(
    doc_model: DocumentModel | None = None,
    deck_model: DeckModel | None = None,
    expected_slide_count: int = 12,
    valid_source_ids: set[int] | None = None,
    expected_doc_outline: list[str] | None = None,
    follow_template_outline: bool = False,
    min_target_words: int | None = 1200,
) -> ValidationReport:
    """Perform deterministic quality validation on DocumentModel and DeckModel.

    Args:
        doc_model: Generated DocumentModel instance.
        deck_model: Generated DeckModel instance.
        expected_slide_count: Target slide count from Plan.
        valid_source_ids: Set of valid integer source IDs in registry.
        expected_doc_outline: List of expected section headings.
        follow_template_outline: If True, enforce fuzzy match against template headings.

    Returns:
        ValidationReport containing pass status, score (0-100), and issue list.
    """
    issues: list[ValidationIssue] = []
    valid_sids = valid_source_ids or set()

    # ── 1. Validate PPTX Deck Model ──────────────────────────────────────────
    if deck_model:
        slides = deck_model.slides
        
        # Check slide count
        if len(slides) != expected_slide_count:
            issues.append(ValidationIssue(
                severity="error",
                where="pptx:deck",
                message=f"Slide count mismatch: generated {len(slides)} slides, expected {expected_slide_count}.",
            ))

        for idx, slide in enumerate(slides, start=1):
            slide_ref = f"pptx:slide_{idx}"
            
            # Combine all slide text
            bullets_text = []
            for b in (slide.bullets or []):
                bullets_text.append(b.text)
            for b in (slide.left or []):
                bullets_text.append(b.text)
            for b in (slide.right or []):
                bullets_text.append(b.text)

            all_slide_text = " ".join([slide.title, slide.subtitle or ""] + bullets_text)

            # Check placeholders
            if PLACEHOLDER_REGEX.search(all_slide_text):
                issues.append(ValidationIssue(
                    severity="error",
                    where=slide_ref,
                    message=f"Placeholder text detected on slide {idx}.",
                ))

            # Content slide checks (title_content, two_content)
            if slide.role in ("title_content", "two_content"):
                bullet_count = len(slide.bullets) if slide.role == "title_content" else (len(slide.left) + len(slide.right))
                if bullet_count < 3:
                    issues.append(ValidationIssue(
                        severity="warning",
                        where=slide_ref,
                        message=f"Content slide {idx} has fewer than 3 bullets ({bullet_count} found).",
                    ))

                word_count = len(all_slide_text.split())
                if word_count > 110:
                    issues.append(ValidationIssue(
                        severity="warning",
                        where=slide_ref,
                        message=f"Content slide {idx} word count ({word_count}) exceeds maximum 110 words.",
                    ))

            # Source citation checks for slides
            for b in (slide.bullets or []) + (slide.left or []) + (slide.right or []):
                for sid in getattr(b, "source_ids", []):
                    if valid_sids and sid not in valid_sids:
                        issues.append(ValidationIssue(
                            severity="error",
                            where=slide_ref,
                            message=f"Slide {idx} cites invalid source_id [{sid}].",
                        ))
                # Check numbers/percentages citation
                if NUMBER_OR_PERCENT_REGEX.search(b.text) and not getattr(b, "source_ids", []):
                    b_snip = b.text[:70] + "..." if len(b.text) > 70 else b.text
                    issues.append(ValidationIssue(
                        severity="error",
                        where=slide_ref,
                        message=f"Bullet on slide {idx} contains numbers/dates/metrics ('{b_snip}') but lacks citation source_ids.",
                    ))

    # ── 2. Validate DOCX Document Model ──────────────────────────────────────
    if doc_model:
        sections = doc_model.sections
        
        # Check section count (proportional / soft check)
        if len(sections) == 0:
            issues.append(ValidationIssue(
                severity="error",
                where="docx:document",
                message="DOCX has zero sections; minimum 1 required.",
            ))
        elif len(sections) < 3:
            issues.append(ValidationIssue(
                severity="warning",
                where="docx:document",
                message=f"DOCX has only {len(sections)} sections; document may be unusually short given the brief.",
            ))

        # Check total word count against minimum target
        total_word_count = sum(
            len(getattr(b, "text", "").split()) + sum(len(it.split()) for it in getattr(b, "items", []))
            for sec in sections
            for b in sec.blocks
        )
        if min_target_words:
            half_target = min_target_words // 2
            if total_word_count < half_target:
                issues.append(ValidationIssue(
                    severity="error",
                    where="docx:document",
                    message=f"DOCX word count ({total_word_count}) is under 50% of minimum target ({half_target} words); document must be expanded.",
                ))
            elif total_word_count < min_target_words:
                issues.append(ValidationIssue(
                    severity="warning",
                    where="docx:document",
                    message=f"DOCX word count ({total_word_count}) is below target {min_target_words} words.",
                ))

        # Check outline fuzzy match only when the brief indicates the template's structure should be followed
        if expected_doc_outline and follow_template_outline:
            generated_headings = [s.heading.lower() for s in sections]
            for exp_h in expected_doc_outline[:6]:
                kw = exp_h.split()[-1].lower() if exp_h.split() else exp_h.lower()
                if not any(kw in gh for gh in generated_headings):
                    issues.append(ValidationIssue(
                        severity="warning",
                        where="docx:document",
                        message=f"Expected section heading '{exp_h}' not found in document.",
                    ))

        has_sources_section = False
        for idx, sec in enumerate(sections, start=1):
            sec_ref = f"docx:section_{idx}"
            if "source" in sec.heading.lower() or "reference" in sec.heading.lower():
                has_sources_section = True

            for block in sec.blocks:
                b_text = getattr(block, "text", "")
                if getattr(block, "items", None):
                    b_text += " " + " ".join(block.items)

                # Check placeholders
                if PLACEHOLDER_REGEX.search(b_text):
                    issues.append(ValidationIssue(
                        severity="error",
                        where=sec_ref,
                        message=f"Placeholder text detected in section {idx}.",
                    ))

                # Source citation checks
                for sid in getattr(block, "source_ids", []):
                    if valid_sids and sid not in valid_sids:
                        issues.append(ValidationIssue(
                            severity="error",
                            where=sec_ref,
                            message=f"Section {idx} block cites invalid source_id [{sid}].",
                        ))

                # Check numbers/percentages citation
                if NUMBER_OR_PERCENT_REGEX.search(b_text) and not getattr(block, "source_ids", []):
                    b_snip = b_text[:70] + "..." if len(b_text) > 70 else b_text
                    issues.append(ValidationIssue(
                        severity="error",
                        where=sec_ref,
                        message=f"Section {idx} text contains numbers/dates/metrics ('{b_snip}') but lacks citation source_ids.",
                    ))

    # Compute overall score and pass status
    error_count = sum(1 for i in issues if i.severity == "error")
    warning_count = sum(1 for i in issues if i.severity == "warning")
    
    score = max(0.0, 100.0 - (error_count * 20.0) - (warning_count * 5.0))
    passed = error_count == 0

    return ValidationReport(passed=passed, score=score, issues=issues)
