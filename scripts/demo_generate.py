"""Demo generation script for Step 4b: produces rich proposal DOCX and 12-slide PPTX deck with PNG previews."""

from __future__ import annotations

import logging
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any
from pathlib import Path

# Ensure root directory is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf
from app.agents.doc_analyzer import analyze_document
from app.agents.doc_generator import generate_document_model
from app.agents.ppt_analyzer import analyze_presentation
from app.agents.ppt_generator import generate_deck_model
from app.llm.client import get_llm_client
from app.services.docx_renderer import render_docx
from app.services.pptx_renderer import render_pptx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 8 Hardcoded sample sources consistent with sample KB
SAMPLE_SOURCES = [
    {
        "id": 1,
        "title": "NexaWorks Enterprise AI Overview",
        "url": "https://nexaworks.ai/about",
        "snippet": "NexaWorks AI Solutions is a Pune-based generative AI consulting firm specializing in multi-agent workflows and enterprise RAG architecture.",
    },
    {
        "id": 2,
        "title": "Generative AI Adoption in Mid-Market Enterprises",
        "url": "https://techtrends.in/genai-2026",
        "snippet": "72% of mid-size Indian enterprises plan to deploy agentic workflow automation by Q4 2026 to optimize document creation and proposal generation SLA.",
    },
    {
        "id": 3,
        "title": "NexaWorks Case Study - Financial & Healthcare AI",
        "url": "https://nexaworks.ai/case-studies",
        "snippet": "NexaWorks delivered an 80% SLA reduction in document processing for investment memos and 4x throughput increase for clinical trial presentation decks.",
    },
    {
        "id": 4,
        "title": "NexaWorks Pricing & Engagement Framework",
        "url": "https://nexaworks.ai/pricing",
        "snippet": "NexaWorks offers turnkey fixed-price sprints starting at INR 1,500,000 as well as time-and-materials rate cards for dedicated AI engineering pods.",
    },
    {
        "id": 5,
        "title": "Enterprise Vector Knowledge Systems Guide",
        "url": "https://nexaworks.ai/rag-architecture",
        "snippet": "Hybrid retrieval combining Pinecone serverless vector database with sparse BM25 indexing achieves 99.4% precision in technical document retrieval.",
    },
    {
        "id": 6,
        "title": "NexaWorks Brand & Design Standards",
        "url": "https://nexaworks.ai/brand-guide",
        "snippet": "NexaWorks visual identity employs Deep Navy (#1E3A8A) for primary headers and Slate Teal (#0D9488) for section accents with Calibri/Segoe UI typography.",
    },
    {
        "id": 7,
        "title": "Supply Chain & Logistics RFP Agent Automation",
        "url": "https://nexaworks.ai/rfp-automation",
        "snippet": "Automated RFP response builder agent increased win rates by 35% and reduced proposal turnaround time from 7 days to 24 hours.",
    },
    {
        "id": 8,
        "title": "India Enterprise GenAI Security & Governance",
        "url": "https://nexaworks.ai/security",
        "snippet": "NexaWorks multi-agent architecture ensures strict tenant data isolation, SOC2 Type II compliance, and zero external LLM training on client data.",
    },
]

DEMO_BRIEF = (
    "Generative AI trends and how NexaWorks AI Solutions can help mid-size Indian enterprises "
    "transform manual document workflows, automate RFP responses, build custom multi-agent platforms, "
    "and achieve enterprise security compliance."
)


def convert_file_to_pdf(input_path: Path, pdf_path: Path) -> bool:
    """Convert DOCX or PPTX to PDF using MS Office COM or soffice fallback."""
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    abs_in = str(input_path.resolve())
    abs_pdf = str(pdf_path.resolve())
    ext = input_path.suffix.lower()

    # 1. Try win32com MS Office COM automation
    try:
        import win32com.client
        if ext == ".pptx":
            ppt_app = win32com.client.Dispatch("PowerPoint.Application")
            pres = ppt_app.Presentations.Open(abs_in, True, False, False)
            pres.SaveAs(abs_pdf, 32)  # 32 = ppSaveAsPDF
            pres.Close()
            ppt_app.Quit()
            return True
        elif ext == ".docx":
            word_app = win32com.client.Dispatch("Word.Application")
            doc = word_app.Documents.Open(abs_in)
            doc.SaveAs(abs_pdf, FileFormat=17)  # 17 = wdFormatPDF
            doc.Close()
            word_app.Quit()
            return True
    except Exception as exc:
        logger.warning("MS Office COM conversion failed for %s: %s", input_path.name, exc)

    # 2. Try soffice / libreoffice command
    soffice_cmd = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice_cmd:
        try:
            subprocess.run([soffice_cmd, "--headless", "--convert-to", "pdf", abs_in, "--outdir", str(pdf_path.parent)], check=True)
            return True
        except Exception as exc:
            logger.warning("soffice PDF conversion failed for %s: %s", input_path.name, exc)

    return False


def render_pdf_to_pngs(pdf_path: Path, output_dir: Path, prefix: str = "page") -> list[Path]:
    """Render pages of a PDF to PNG images using PyMuPDF."""
    output_dir.mkdir(parents=True, exist_ok=True)
    png_paths = []
    if not pdf_path.exists():
        return png_paths

    doc = pymupdf.open(pdf_path)
    for idx, page in enumerate(doc, start=1):
        pix = page.get_pixmap(dpi=150)
        out_png = output_dir / f"{prefix}_{idx}.png"
        pix.save(str(out_png))
        png_paths.append(out_png)

    doc.close()
    return png_paths


def main() -> None:
    """Run demo artifact generation for DOCX and PPTX with visual preview generation."""
    print("=== NexaWorks Demo Generator (Step 4b Quality Improvements) ===\n")

    output_dir = Path("data/outputs")
    preview_dir = output_dir / "preview"
    docx_png_dir = preview_dir / "docx"
    deck_png_dir = preview_dir / "deck"

    output_dir.mkdir(parents=True, exist_ok=True)

    docx_tmpl_path = Path("data/sample_templates/Company_Proposal.docx")
    pptx_tmpl_path = Path("data/sample_templates/Company_Template.pptx")

    if not docx_tmpl_path.exists() or not pptx_tmpl_path.exists():
        print("Error: Missing sample templates.")
        sys.exit(1)

    is_live = "--live" in sys.argv
    if is_live:
        print("   [LIVE MODE] Running live web research & KB retrieval via context_builder...")
        from app.services.context_builder import build_context
        ctx = build_context(DEMO_BRIEF, use_web=True, use_kb=True)
        sources_list = ctx.sources_list if ctx.sources_list else SAMPLE_SOURCES
        sources_map = ctx.sources_map if ctx.sources_map else {s["id"]: s for s in SAMPLE_SOURCES}
    else:
        sources_list = SAMPLE_SOURCES
        sources_map = {s["id"]: s for s in SAMPLE_SOURCES}
    llm = get_llm_client()

    # 1. Analyze Templates
    print("1. Analyzing sample templates...")
    doc_profile = analyze_document(docx_tmpl_path)
    ppt_profile = analyze_presentation(pptx_tmpl_path)

    # 2. Generate & Render Proposal DOCX
    print("\n2. Generating Proposal DOCX Model...")
    doc_model = generate_document_model(DEMO_BRIEF, doc_profile, sources=sources_list, llm_client=llm)
    print(f"   Generated DocumentModel: '{doc_model.title}' ({len(doc_model.sections)} sections)")

    out_docx = output_dir / "Proposal_Demo.docx"
    render_docx(doc_model, docx_tmpl_path, doc_profile, out_docx, sources_map=sources_map)
    print(f"   Rendered DOCX: {out_docx} ({out_docx.stat().st_size} bytes)")

    # 3. Generate & Render Presentation PPTX (12 Slides)
    print("\n3. Generating 12-Slide PPTX Deck Model...")
    deck_model = generate_deck_model(DEMO_BRIEF, ppt_profile, sources=sources_list, slide_count=12, llm_client=llm)
    print(f"   Generated DeckModel: '{deck_model.title}' ({len(deck_model.slides)} slides)")

    out_pptx = output_dir / "Deck_Demo.pptx"
    render_pptx(deck_model, pptx_tmpl_path, ppt_profile, out_pptx, sources_map=sources_map)
    print(f"   Rendered PPTX: {out_pptx} ({out_pptx.stat().st_size} bytes)")

    # 4. Convert both to PDF & Render PNG previews
    print("\n4. Converting files to PDF and rendering PNG previews into data/outputs/preview/...")
    docx_pdf = preview_dir / "Proposal_Demo.pdf"
    deck_pdf = preview_dir / "Deck_Demo.pdf"

    if convert_file_to_pdf(out_docx, docx_pdf):
        pngs = render_pdf_to_pngs(docx_pdf, docx_png_dir, prefix="page")
        print(f"   Rendered {len(pngs)} DOCX page PNG previews into {docx_png_dir}")

    if convert_file_to_pdf(out_pptx, deck_pdf):
        pngs = render_pdf_to_pngs(deck_pdf, deck_png_dir, prefix="slide")
        print(f"   Rendered {len(pngs)} PPTX slide PNG previews into {deck_png_dir}")

    # Summary Table
    print("\n" + "=" * 75)
    print(f"{'Output File':<42} | {'Status':<8} | {'Size (KB)':<10}")
    print("=" * 75)
    for out_f in [out_docx, out_pptx, docx_pdf, deck_pdf]:
        status = "OK" if out_f.exists() else "MISSING"
        size_kb = out_f.stat().st_size / 1024 if out_f.exists() else 0.0
        print(f"{str(out_f):<42} | {status:<8} | {size_kb:>8.2f} KB")
    print("=" * 75)

    # LLM Call Stats
    print(f"\nLLM Call Stats: {llm.stats.summary()}")


if __name__ == "__main__":
    main()
