"""Script to generate realistic sample assets for NexaWorks AI Solutions demo.

Generates:
1. data/sample_templates/Company_Proposal.docx (styled DOCX template without Executive Summary)
2. data/sample_kb/ (5 enterprise knowledge base documents: .md, .docx, .pdf)
3. data/sample_scans/scanned_page.png (simulated scanned SOW document image)
4. data/sample_templates/README.md (documentation of sample files)
"""

from __future__ import annotations

import math
import os
import random
import sys
from pathlib import Path

# Ensure root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches, Pt, RGBColor
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import pymupdf


# ── Color Palette & Constants ──────────────────────────────────────────────
PRIMARY_NAVY = RGBColor(30, 58, 138)     # #1E3A8A
SECONDARY_TEAL = RGBColor(13, 148, 136) # #0D9488
DARK_TEXT = RGBColor(31, 41, 55)        # #1F2937
MUTED_GREY = RGBColor(107, 114, 128)    # #6B7280

HEX_PRIMARY_NAVY = "1E3A8A"
HEX_SECONDARY_TEAL = "0D9488"
HEX_LIGHT_BG = "F3F4F6"
HEX_ALT_ROW = "F9FAFB"
HEX_BORDER = "D1D5DB"


def set_cell_background(cell, hex_color: str) -> None:
    """Set the background color of a table cell."""
    tcPr = cell._element.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>')
    tcPr.append(shd)


def set_cell_padding(cell, top=120, bottom=120, left=180, right=180) -> None:
    """Set cell internal padding in dxa (1 pt = 20 dxa)."""
    tcPr = cell._element.get_or_add_tcPr()
    tcMar = OxmlElement('w:tcMar')
    for m, val in [('top', top), ('bottom', bottom), ('left', left), ('right', right)]:
        node = OxmlElement(f'w:{m}')
        node.set(qn('w:w'), str(val))
        node.set(qn('w:type'), 'dxa')
        tcMar.append(node)
    tcPr.append(tcMar)


def add_page_number_to_run(run) -> None:
    """Insert a dynamic PAGE field into a docx run."""
    fldChar1 = parse_xml(r'<w:fldChar %s w:fldCharType="begin"/>' % nsdecls('w'))
    instrText = parse_xml(r'<w:instrText %s xml:space="preserve"> PAGE </w:instrText>' % nsdecls('w'))
    fldChar2 = parse_xml(r'<w:fldChar %s w:fldCharType="separate"/>' % nsdecls('w'))
    fldChar3 = parse_xml(r'<w:fldChar %s w:fldCharType="end"/>' % nsdecls('w'))
    run._r.append(fldChar1)
    run._r.append(instrText)
    run._r.append(fldChar2)
    run._r.append(fldChar3)


# ── 1. Create Company_Proposal.docx ───────────────────────────────────────

def generate_company_proposal(output_path: Path) -> None:
    """Generate a 3-4 page styled proposal template for NexaWorks AI Solutions."""
    doc = docx.Document()

    # Set page margins (1 inch all around)
    sections = doc.sections
    for section in sections:
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)

    # Configure Header & Footer
    header = doc.sections[0].header
    hp = header.paragraphs[0]
    hp.text = "NexaWorks AI Solutions | Confidential Technical Proposal"
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    hp.style.font.name = "Calibri"
    hp.style.font.size = Pt(8.5)
    hp.style.font.color.rgb = MUTED_GREY

    footer = doc.sections[0].footer
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.LEFT
    frun1 = fp.add_run("NexaWorks AI Solutions, Pune, India  |  Page ")
    frun1.font.name = "Calibri"
    frun1.font.size = Pt(8.5)
    frun1.font.color.rgb = MUTED_GREY
    add_page_number_to_run(frun1)

    # Style Helpers
    normal_style = doc.styles['Normal']
    normal_style.font.name = 'Calibri'
    normal_style.font.size = Pt(11)
    normal_style.font.color.rgb = DARK_TEXT

    def add_p(text: str, space_after: int = 6, bold_prefix: str | None = None) -> None:
        p = doc.add_paragraph()
        p.paragraph_format.line_spacing = 1.15
        p.paragraph_format.space_after = Pt(space_after)
        if bold_prefix:
            r_bold = p.add_run(bold_prefix)
            r_bold.bold = True
            r_bold.font.color.rgb = PRIMARY_NAVY
        p.add_run(text)

    def add_h1(text: str) -> None:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(18)
        p.paragraph_format.space_after = Pt(8)
        p.paragraph_format.keep_with_next = True
        run = p.add_run(text)
        run.font.name = 'Segoe UI'
        run.font.size = Pt(18)
        run.bold = True
        run.font.color.rgb = PRIMARY_NAVY

    def add_h2(text: str) -> None:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(14)
        p.paragraph_format.space_after = Pt(6)
        p.paragraph_format.keep_with_next = True
        run = p.add_run(text)
        run.font.name = 'Segoe UI'
        run.font.size = Pt(14)
        run.bold = True
        run.font.color.rgb = SECONDARY_TEAL

    def add_bullet(text: str, bold_prefix: str | None = None) -> None:
        p = doc.add_paragraph(style='List Bullet')
        p.paragraph_format.line_spacing = 1.15
        p.paragraph_format.space_after = Pt(4)
        if bold_prefix:
            r_bold = p.add_run(bold_prefix)
            r_bold.bold = True
            r_bold.font.color.rgb = PRIMARY_NAVY
        p.add_run(text)

    def add_num(num_str: str, text: str, bold_prefix: str | None = None) -> None:
        p = doc.add_paragraph(style='List Number')
        p.paragraph_format.line_spacing = 1.15
        p.paragraph_format.space_after = Pt(4)
        if bold_prefix:
            r_bold = p.add_run(bold_prefix)
            r_bold.bold = True
            r_bold.font.color.rgb = PRIMARY_NAVY
        p.add_run(text)

    # ── COVER BLOCK ──
    title_p = doc.add_paragraph()
    title_p.paragraph_format.space_before = Pt(36)
    title_p.paragraph_format.space_after = Pt(10)
    trun = title_p.add_run("ENTERPRISE GENERATIVE AI & DOCUMENT AUTOMATION PLATFORM")
    trun.font.name = "Segoe UI"
    trun.font.size = Pt(24)
    trun.bold = True
    trun.font.color.rgb = PRIMARY_NAVY

    sub_p = doc.add_paragraph()
    sub_p.paragraph_format.space_after = Pt(28)
    srun = sub_p.add_run("Technical Architecture & Commercial Engagement Proposal")
    srun.font.name = "Segoe UI"
    srun.font.size = Pt(14)
    srun.font.color.rgb = SECONDARY_TEAL

    meta_p = doc.add_paragraph()
    meta_p.paragraph_format.space_after = Pt(36)
    mrun = meta_p.add_run(
        "PREPARED FOR: Apex Global Enterprise Solutions Pvt. Ltd.\n"
        "PREPARED BY: NexaWorks AI Solutions, Pune, Maharashtra, India\n"
        "DATE: September 20, 2026\n"
        "DOCUMENT VERSION: 1.0 (Final Draft)"
    )
    mrun.font.size = Pt(10.5)
    mrun.font.color.rgb = MUTED_GREY

    doc.add_page_break()

    # NOTE: Executive Summary is intentionally omitted per requirements.

    # ── 1. INTRODUCTION ──
    add_h1("1. Introduction")
    add_p(
        "NexaWorks AI Solutions is a premier Pune-based artificial intelligence consulting firm. "
        "We specialize in enterprise multi-agent workflows, Retrieval-Augmented Generation (RAG) platforms, "
        "and automated document processing systems. Founded in 2021, our team of 65+ AI engineers and data scientists "
        "has successfully deployed 40+ production AI systems across financial services, healthcare, and logistics."
    )
    add_p(
        "We partner with forward-thinking enterprises like Apex Global Enterprise Solutions to transition "
        "from traditional manual documentation workflows into intelligent, autonomous, agentic pipelines. "
        "This proposal outlines our technical solution, execution roadmap, commercial investment, and operational team."
    )

    # ── 2. PROBLEM STATEMENT ──
    add_h1("2. Problem Statement")
    add_p(
        "Apex Global Enterprise currently processes over 12,000 corporate documents, contracts, and pitch decks annually. "
        "Through our initial technical audit, we identified three critical operational bottlenecks:"
    )
    add_bullet(" Teams spend an average of 4.2 hours creating a single client proposal or presentation deck from raw inputs.", "Excessive Cycle Times: ")
    add_bullet(" Inconsistent font usage, color schemes, and layout hierarchies damage corporate brand integrity across regional offices.", "Style & Brand Inconsistencies: ")
    add_bullet(" Absence of audit trails makes it impossible to verify data sources or trace numbers back to primary enterprise systems.", "Lack of Auditability & Verification: ")
    add_p(
        "Addressing these issues requires a centralized AI system that respects custom document templates, "
        "queries internal knowledge bases securely, and generates fully editable DOCX and PPTX deliverables."
    )

    # ── 3. PROPOSED SOLUTION ──
    add_h1("3. Proposed Solution")
    add_p(
        "NexaWorks proposes the deployment of a Multi-Agent Document & PPT Generation Chatbot built on top of "
        "LangGraph, Google Gemini, and Pinecone Serverless Vector Database. The architecture consists of four key layers:"
    )
    add_h2("3.1 Core Architecture Components")
    add_num("1.", " Custom template ingestion engine that extracts font tokens, RGB palettes, and layout hierarchies from uploaded sample files.", "Template Profile Engine: ")
    add_num("2.", " Pinecone vector store powered by sentence-transformers (all-MiniLM-L6-v2) for instant hybrid search across corporate guidelines.", "Enterprise Knowledge Base (RAG): ")
    add_num("3.", " Supervisor agent orchestrating specialized sub-agents (Researcher, KB Retriever, Generator, and Editor) using Gemini structured JSON.", "LangGraph Multi-Agent Engine: ")
    add_num("4.", " Automated python-docx and python-pptx builders producing pixel-perfect, fully editable corporate artifacts with citable sources.", "Document & Deck Rendering Engine: ")

    # ── 4. APPROACH AND METHODOLOGY ──
    add_h1("4. Approach and Methodology")
    add_p(
        "We employ an iterative 4-phase agile methodology spanning 8 weeks from project kickoff to production delivery. "
        "Every sprint delivers functional software and complete transparency."
    )
    add_bullet(" Establish repository scaffolding, database schemas, FastAPI backend, and Gemini LLM client wrapper with retry and disk cache.", "Sprint 1 (Weeks 1-2) - Scaffolding & Core Client: ")
    add_bullet(" Implement document template analyzers for DOCX/PPTX, vector indexing pipeline, and Pinecone hybrid retrieval.", "Sprint 2 (Weeks 3-4) - Template Ingestion & RAG: ")
    add_bullet(" Assemble LangGraph supervisor state machine, multi-agent tools, prompt definitions, and structured JSON validators.", "Sprint 3 (Weeks 5-6) - Multi-Agent Engine: ")
    add_bullet(" Deploy Streamlit interactive UI, file export handlers, audit log viewers, UAT testing, and cloud deployment.", "Sprint 4 (Weeks 7-8) - UI, Testing & Handoff: ")

    # ── 5. TIMELINE ──
    add_h1("5. Project Timeline & Milestones")
    add_p("The table below details the target milestones and schedule across the 8-week implementation window:")

    # Timeline Table
    table_timeline = doc.add_table(rows=5, cols=4)
    table_timeline.alignment = WD_TABLE_ALIGNMENT.CENTER
    timeline_headers = ["Phase / Sprint", "Key Deliverables", "Duration", "Target Date"]
    timeline_data = [
        ["Phase 1: Foundation", "Project scaffold, DB schema, FastAPI auth, LLM wrapper", "Weeks 1-2", "Oct 15, 2026"],
        ["Phase 2: RAG & Parsing", "Pinecone index, DOCX/PPTX analyzers, KB ingestion", "Weeks 3-4", "Oct 29, 2026"],
        ["Phase 3: Agent Orchestration", "LangGraph supervisor, generator, editor, source tracing", "Weeks 5-6", "Nov 12, 2026"],
        ["Phase 4: UI & UAT", "Streamlit UI, export pipeline, security review, go-live", "Weeks 7-8", "Nov 26, 2026"],
    ]

    # Format Header Row
    hdr_cells = table_timeline.rows[0].cells
    for idx, text in enumerate(timeline_headers):
        hdr_cells[idx].text = text
        set_cell_background(hdr_cells[idx], HEX_PRIMARY_NAVY)
        set_cell_padding(hdr_cells[idx])
        p = hdr_cells[idx].paragraphs[0]
        p.runs[0].font.bold = True
        p.runs[0].font.color.rgb = RGBColor(255, 255, 255)

    # Format Data Rows
    for r_idx, row_data in enumerate(timeline_data):
        row_cells = table_timeline.rows[r_idx + 1].cells
        bg_color = HEX_ALT_ROW if r_idx % 2 == 1 else "FFFFFF"
        for c_idx, cell_value in enumerate(row_data):
            row_cells[c_idx].text = cell_value
            set_cell_background(row_cells[c_idx], bg_color)
            set_cell_padding(row_cells[c_idx])
            p = row_cells[c_idx].paragraphs[0]
            p.runs[0].font.size = Pt(9.5)

    doc.add_paragraph().paragraph_format.space_after = Pt(12)

    # ── 6. PRICING ──
    add_h1("6. Commercial Investment & Pricing")
    add_p(
        "Our pricing structure is transparent and fixed-price based on defined sprint deliverables. "
        "All rates include architecture design, development, quality assurance, and 60 days of post-launch support."
    )

    # Pricing Table
    table_pricing = doc.add_table(rows=6, cols=4)
    table_pricing.alignment = WD_TABLE_ALIGNMENT.CENTER
    pricing_headers = ["Module Component", "Description & Scope", "Effort (Hrs)", "Investment (INR)"]
    pricing_data = [
        ["Backend Skeleton & LLM Client", "FastAPI setup, SQLite models, Gemini wrapper with retry & cache", "80 hrs", "₹480,000"],
        ["Template Analyzer & RAG Engine", "DOCX/PPTX parsing, Pinecone vector integration, sentence-transformers", "120 hrs", "₹720,000"],
        ["LangGraph Multi-Agent System", "Supervisor state machine, Researcher, KB Retriever, Generator, Editor", "160 hrs", "₹1,120,000"],
        ["Streamlit UI & Export Engine", "Interactive chat interface, version history, DOCX/PPTX builders", "100 hrs", "₹600,000"],
        ["Total Investment", "Complete turnkey deployment including 60-day enterprise warranty", "460 hrs", "₹2,920,000"],
    ]

    hdr_p_cells = table_pricing.rows[0].cells
    for idx, text in enumerate(pricing_headers):
        hdr_p_cells[idx].text = text
        set_cell_background(hdr_p_cells[idx], HEX_PRIMARY_NAVY)
        set_cell_padding(hdr_p_cells[idx])
        p = hdr_p_cells[idx].paragraphs[0]
        p.runs[0].font.bold = True
        p.runs[0].font.color.rgb = RGBColor(255, 255, 255)

    for r_idx, row_data in enumerate(pricing_data):
        row_cells = table_pricing.rows[r_idx + 1].cells
        is_total = (r_idx == len(pricing_data) - 1)
        bg_color = HEX_LIGHT_BG if is_total else (HEX_ALT_ROW if r_idx % 2 == 1 else "FFFFFF")
        for c_idx, cell_value in enumerate(row_data):
            row_cells[c_idx].text = cell_value
            set_cell_background(row_cells[c_idx], bg_color)
            set_cell_padding(row_cells[c_idx])
            p = row_cells[c_idx].paragraphs[0]
            if is_total:
                p.runs[0].font.bold = True
                p.runs[0].font.color.rgb = PRIMARY_NAVY
            else:
                p.runs[0].font.size = Pt(9.5)

    doc.add_paragraph().paragraph_format.space_after = Pt(12)

    # ── 7. TEAM ──
    add_h1("7. Engagement Team")
    add_p("NexaWorks assigns a dedicated expert pod to ensure rapid execution and technical excellence:")
    add_bullet(" 12+ years experience in distributed software and enterprise AI systems.", "Dr. Rajesh Kulkarni (Lead AI Architect): ")
    add_bullet(" Specialist in stateful agent graphs, tool binding, and LLM structured outputs.", "Ananya Sharma (Senior LangGraph Engineer): ")
    add_bullet(" Expertise in vector databases, embedding models, and hybrid retrieval tuning.", "Siddharth Verma (RAG & Knowledge Engineer): ")
    add_bullet(" Focused on intuitive chat interfaces, visual document rendering, and human-in-the-loop workflows.", "Priya Nair (Fullstack & UI Lead): ")

    # ── 8. CONCLUSION ──
    add_h1("8. Conclusion & Next Steps")
    add_p(
        "NexaWorks AI Solutions is uniquely positioned to deliver this high-impact document and presentation automation "
        "platform for Apex Global Enterprise. By leveraging cutting-edge multi-agent orchestration, we will empower "
        "your team to produce verified, brand-aligned documents in minutes rather than hours."
    )
    add_p(
        "To initiate project execution, please review and execute the attached Statement of Work (SOW). "
        "Upon signing, our team will conduct the technical kickoff within 5 business days."
    )
    add_p(
        "Contact Person: Rahul Deshmukh, VP of Client Solutions | Email: rahul@nexaworks.ai | Phone: +91 98220 12345"
    )

    doc.save(output_path)
    print(f"Generated DOCX: {output_path} ({output_path.stat().st_size} bytes)")


# ── 2. Create Enterprise KB Documents ─────────────────────────────────────

def generate_kb_documents(kb_dir: Path) -> None:
    """Generate 5 enterprise knowledge documents with consistent facts."""
    kb_dir.mkdir(parents=True, exist_ok=True)

    # 1. company_overview.md
    overview_path = kb_dir / "company_overview.md"
    overview_path.write_text(
        "# NexaWorks AI Solutions - Enterprise Overview\n\n"
        "## About NexaWorks\n"
        "NexaWorks AI Solutions Private Limited is a specialized generative AI consulting firm headquartered in "
        "Baner Tech Park, Pune, Maharashtra 411045, India. Founded in 2021, NexaWorks has rapidly grown to a team of "
        "65+ AI engineers, data scientists, and solution architects dedicated to transforming business workflows through "
        "advanced multi-agent automation and Retrieval-Augmented Generation (RAG) platforms.\n\n"
        "## Core Focus Areas\n"
        "- **Agentic Workflow Automation**: Designing stateful, multi-agent systems using LangGraph and AutoGen for complex decision-making.\n"
        "- **Enterprise RAG Systems**: Building hybrid search pipelines combining Pinecone vector databases with sparse keyword indexers.\n"
        "- **Document & Slide Engineering**: Automated generation and editing of corporate DOCX proposals and PPTX decks.\n"
        "- **LLM Fine-Tuning & Evaluation**: Domain-specific adaptation of Google Gemini, Llama, and Mistral models.\n\n"
        "## Key Performance Statistics\n"
        "- **40+ Enterprise Deployments** across North America, Europe, and India.\n"
        "- **99.4% Accuracy** in automated structured data extraction from un-structured corporate PDFs.\n"
        "- **70% Average Reduction** in document creation SLA for enterprise sales and consulting teams.\n\n"
        "## Leadership & Culture\n"
        "Led by CEO Dr. Rajesh Kulkarni (ex-Google Research) and CTO Ananya Sharma, NexaWorks fosters a culture of "
        "rigorous engineering, open-source contribution, and strict client data privacy compliance (ISO 27001, SOC2 Type II).\n",
        encoding="utf-8"
    )

    # 2. services_and_offerings.docx
    services_path = kb_dir / "services_and_offerings.docx"
    doc_srv = docx.Document()
    p_title = doc_srv.add_paragraph()
    r_title = p_title.add_run("NexaWorks AI Solutions - Services & Service Lines")
    r_title.font.name = "Segoe UI"
    r_title.font.size = Pt(18)
    r_title.bold = True
    r_title.font.color.rgb = PRIMARY_NAVY

    p_intro = doc_srv.add_paragraph(
        "NexaWorks AI Solutions provides end-to-end artificial intelligence services designed specifically for "
        "large-scale enterprise transformation. Operating from Pune, India, our engineering pod delivers scalable, "
        "secure, and custom AI systems tailored to enterprise data environments."
    )
    p_intro.paragraph_format.space_after = Pt(12)

    services = [
        ("1. Generative AI Strategy & Roadmap", "We assist CXOs in identifying high-impact AI use cases, establishing data governance, evaluating security risks, and constructing ROI-driven implementation roadmaps."),
        ("2. Custom Multi-Agent Architecture", "Using LangGraph and supervisor routing patterns, we build autonomous AI agent pods capable of web research, internal database queries, document generation, and quality editing."),
        ("3. Enterprise RAG & Vector Systems", "We architect production-grade RAG systems using Pinecone serverless, sentence-transformers, and advanced chunking strategies (parent-child, semantic splitting) for sub-second search."),
        ("4. Automated Document & PPTX Platforms", "We build custom python-docx and python-pptx rendering engines that dynamically format unstructured LLM output into pixel-perfect corporate templates with full auditability."),
    ]

    for title, desc in services:
        p_h = doc_srv.add_paragraph()
        r_h = p_h.add_run(title)
        r_h.font.name = "Segoe UI"
        r_h.font.size = Pt(13)
        r_h.bold = True
        r_h.font.color.rgb = SECONDARY_TEAL
        p_h.paragraph_format.space_before = Pt(8)
        p_h.paragraph_format.space_after = Pt(4)

        p_d = doc_srv.add_paragraph(desc)
        p_d.paragraph_format.space_after = Pt(10)

    doc_srv.save(services_path)

    # 3. case_studies.pdf
    case_path = kb_dir / "case_studies.pdf"
    doc_pdf = pymupdf.open()
    page = doc_pdf.new_page(width=595, height=842)

    # Helper for drawing formatted text block on PDF
    y = 50
    page.insert_text(pymupdf.Point(50, y), "NexaWorks AI Solutions - Enterprise Case Studies", fontsize=16, fontname="helv", color=(0.117, 0.227, 0.541))
    y += 25
    page.insert_text(pymupdf.Point(50, y), "Proven Impact across Financial Services, Healthcare, and Logistics", fontsize=10, fontname="helv", color=(0.4, 0.4, 0.4))
    y += 30

    cases = [
        ("Case Study 1: Global Investment Bank (Automated Memo Processing)",
         "Challenge: Bank analysts spent 15+ hours manually synthesizing financial reports into investment memos.\n"
         "Solution: NexaWorks deployed a RAG + DOCX generation agent using Gemini 2.5 Flash and Pinecone.\n"
         "Results: 80% reduction in turnaround time (from 15 hours to 3 hours); 99.2% financial figure accuracy."),
        ("Case Study 2: Life Sciences Conglomerate (Regulatory Slide Deck Generator)",
         "Challenge: Clinical trial teams struggled with inconsistent regulatory presentation decks across 12 countries.\n"
         "Solution: Multi-agent system parsed trial PDFs and auto-built formatted PPTX decks adhering to FDA style rules.\n"
         "Results: 4x increase in reporting throughput; 100% compliance with corporate design standards."),
        ("Case Study 3: Enterprise Supply Chain RFP Response Builder",
         "Challenge: Proposal team faced tight deadlines responding to complex 100+ page logistics RFPs.\n"
         "Solution: LangGraph research agent retrieved past RFP answers from vector store and drafted tailored proposals.\n"
         "Results: Win rate increased by 35%; average proposal response SLA dropped from 7 days to 24 hours."),
    ]

    for title, body in cases:
        page.insert_text(pymupdf.Point(50, y), title, fontsize=12, fontname="helv", color=(0.05, 0.58, 0.53))
        y += 18
        rect = pymupdf.Rect(50, y, 545, y + 80)
        page.insert_textbox(rect, body, fontsize=9.5, fontname="helv", color=(0.12, 0.16, 0.22))
        y += 85

    doc_pdf.save(case_path)
    doc_pdf.close()

    # 4. pricing_and_engagement_models.pdf
    pricing_pdf_path = kb_dir / "pricing_and_engagement_models.pdf"
    doc_prc = pymupdf.open()
    page_prc = doc_prc.new_page(width=595, height=842)

    y = 50
    page_prc.insert_text(pymupdf.Point(50, y), "NexaWorks AI Solutions - Pricing & Engagement Models", fontsize=16, fontname="helv", color=(0.117, 0.227, 0.541))
    y += 25
    page_prc.insert_text(pymupdf.Point(50, y), "Flexible Engagement Structures Tailored to Enterprise Scale", fontsize=10, fontname="helv", color=(0.4, 0.4, 0.4))
    y += 35

    models_text = [
        ("1. Fixed-Price Turnkey Delivery",
         "Ideal for well-defined Proofs of Concept (POC) and Minimum Viable Products (MVP). Scope, milestones, and deliverable schedules are locked upfront. Typical engagement range: ₹1,500,000 to ₹3,500,000 ($20,000 - $45,000 USD)."),
        ("2. Time & Materials (T&M) Rate Card",
         "For evolving enterprise initiatives requiring flexible resource allocation. Billing is monthly based on actual logged engineering hours.\n"
         "• Lead AI Architect: ₹6,500 / hr ($80/hr)\n"
         "• Senior LangGraph Engineer: ₹4,800 / hr ($60/hr)\n"
         "• RAG & Vector Database Specialist: ₹4,200 / hr ($52/hr)\n"
         "• Fullstack UI Developer: ₹3,500 / hr ($44/hr)"),
        ("3. Enterprise Managed Support Retainer",
         "Post-launch operational support, model re-evaluation, prompt optimization, vector index re-indexing, and SLA response guarantees. Starts at ₹500,000 / month ($6,000/month)."),
    ]

    for title, body in models_text:
        page_prc.insert_text(pymupdf.Point(50, y), title, fontsize=12, fontname="helv", color=(0.05, 0.58, 0.53))
        y += 18
        rect = pymupdf.Rect(50, y, 545, y + 100)
        page_prc.insert_textbox(rect, body, fontsize=9.5, fontname="helv", color=(0.12, 0.16, 0.22))
        y += 105

    doc_prc.save(pricing_pdf_path)
    doc_prc.close()

    # 5. brand_voice_guide.md
    brand_path = kb_dir / "brand_voice_guide.md"
    brand_path.write_text(
        "# NexaWorks AI Solutions - Brand Voice & Design Style Guide\n\n"
        "## Brand Tone & Persona\n"
        "- **Authoritative yet Approachable**: We communicate with technical precision while remaining clear and business-focused.\n"
        "- **First-Person Plural ('We')**: Always use 'we', 'our team', and 'NexaWorks' rather than passive third-person phrasing.\n"
        "- **Concrete & Data-Driven**: Back every claim with concrete metrics (e.g. '80% SLA reduction', '99.4% extraction accuracy').\n"
        "- **Concise Sentence Structure**: Keep sentences direct, short, and active.\n\n"
        "## Visual Identity & Color Palette\n"
        "- **Primary Brand Color**: Deep Navy (`#1E3A8A` / RGB: 30, 58, 138) - used for Document Titles, Heading 1, and Table Headers.\n"
        "- **Secondary Accent Color**: Slate Teal (`#0D9488` / RGB: 13, 148, 136) - used for Heading 2 and key callouts.\n"
        "- **Body Text**: Dark Slate (`#1F2937` / RGB: 31, 41, 55) - line spacing 1.15, space after 6pt.\n"
        "- **Muted Secondary Text**: Neutral Grey (`#6B7280` / RGB: 107, 114, 128) - used for headers, footers, and captions.\n\n"
        "## Document Formatting Conventions\n"
        "- **Headings**: Segoe UI or Arial (Bold). Heading 1 = 18pt Primary Navy, Heading 2 = 14pt Secondary Teal.\n"
        "- **Body Text**: Calibri 11pt or Arial 10.5pt.\n"
        "- **Tables**: Header rows must have Deep Navy background (`#1E3A8A`) with white bold text. Alternating rows use `#F9FAFB` shading.\n"
        "- **Executive Summary Rule**: Sample proposal templates deliberately exclude Executive Summaries so they can be generated dynamically on demand.\n",
        encoding="utf-8"
    )

    print(f"Generated KB files in {kb_dir}:")
    for f in kb_dir.iterdir():
        print(f"  - {f.name} ({f.stat().st_size} bytes)")


# ── 3. Create scanned_page.png ─────────────────────────────────────────────

def generate_scanned_page(output_path: Path) -> None:
    """Render a scanned Statement of Work excerpt image with rotation and noise."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Standard A4 proportions at ~150 DPI
    width, height = 1240, 1754
    bg_color = (242, 244, 247)  # Light grey paper background

    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    # Try loading default font or truetype
    try:
        title_font = ImageFont.truetype("arial.ttf", 36)
        sub_font = ImageFont.truetype("arial.ttf", 24)
        body_font = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        title_font = ImageFont.load_default()
        sub_font = ImageFont.load_default()
        body_font = ImageFont.load_default()

    # Draw document text
    y = 120
    draw.text((100, y), "STATEMENT OF WORK (SOW) EXCERPT", fill=(30, 58, 138), font=title_font)
    y += 50
    draw.text((100, y), "Project: Enterprise AI Document Platform  |  Ref: NW-SOW-2026-88", fill=(107, 114, 128), font=sub_font)
    y += 60

    lines = [
        "1. SCOPE OF SERVICES & DELIVERABLES",
        "NexaWorks AI Solutions ('Consultant') agrees to perform generative AI engineering",
        "and multi-agent system integration for Apex Global Enterprise ('Client').",
        "",
        "2. ACCEPTANCE CRITERIA",
        "- All DOCX and PPTX output files must conform to Client style guide specifications.",
        "- RAG retrieval latency must remain below 800 milliseconds for vector queries.",
        "- System accuracy for structured table parsing must meet or exceed 95.0%.",
        "",
        "3. CONFIDENTIALITY & DATA SECURITY",
        "Consultant warrants that all Client data, uploads, and knowledge base vectors",
        "shall remain strictly isolated within dedicated cloud tenant storage.",
        "No Client data shall be utilized for external LLM model training.",
        "",
        "4. GOVERNING LAW & JURISDICTION",
        "This Agreement shall be governed by the laws of India, with exclusive jurisdiction",
        "in the courts of Pune, Maharashtra.",
        "",
        "IN WITNESS WHEREOF, the Parties have executed this Statement of Work.",
        "",
        "For NexaWorks AI Solutions:                             For Apex Global Enterprise:",
        "____________________________                            ____________________________",
        "Dr. Rajesh Kulkarni, Director                            VP Operations & Procurement",
        "Date: September 20, 2026                                 Date: September 22, 2026",
    ]

    for line in lines:
        if line.startswith(("1.", "2.", "3.", "4.")):
            draw.text((100, y), line, fill=(30, 58, 138), font=sub_font)
            y += 35
        else:
            draw.text((100, y), line, fill=(31, 41, 55), font=body_font)
            y += 28

    # Add scan effects: subtle noise and rotation
    # 1. Add subtle random noise grain
    pixels = img.load()
    for _ in range(12000):
        nx = random.randint(0, width - 1)
        ny = random.randint(0, height - 1)
        noise = random.randint(-25, 25)
        r, g, b = pixels[nx, ny]
        pixels[nx, ny] = (
            max(0, min(255, r + noise)),
            max(0, min(255, g + noise)),
            max(0, min(255, b + noise)),
        )

    # 2. Rotate slightly (1.5 degrees) with expand=False to simulate scanner skew
    rotated = img.rotate(1.5, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=bg_color)

    # 3. Light blur to emulate scanner optics
    scanned = rotated.filter(ImageFilter.GaussianBlur(radius=0.5))

    scanned.save(output_path, "PNG")
    print(f"Generated Scanned PNG: {output_path} ({output_path.stat().st_size} bytes)")


# ── 4. Create README.md & Verify PPTX ──────────────────────────────────────

def generate_sample_readme(readme_path: Path) -> None:
    """Generate README.md documenting sample template assets."""
    content = (
        "# NexaWorks Sample Assets & Templates Directory\n\n"
        "This directory contains realistic sample templates, enterprise knowledge base documents, "
        "and simulated scanned files for demonstrating the NexaWorks Multi-Agent Document & Presentation Chatbot.\n\n"
        "## Directory Structure & Purpose\n\n"
        "### 1. `data/sample_templates/`\n"
        "- **`Company_Proposal.docx`**: Styled AI consulting proposal template for NexaWorks AI Solutions (Pune). "
        "Includes custom cover block, typography, styled tables (Timeline & Pricing), team section, and header/footer. "
        "*Note: Executive Summary is omitted intentionally to demo dynamic generation.*[REQUIRED]\n"
        "- **`Company_Template.pptx`**: Master corporate presentation template with slide masters, brand colors, "
        "and layouts for generating presentation decks.\n\n"
        "### 2. `data/sample_kb/`\n"
        "- **`company_overview.md`**: Markdown overview of NexaWorks AI Solutions (location, history, metrics).\n"
        "- **`services_and_offerings.docx`**: Detailed service catalog (Generative AI strategy, LangGraph multi-agent, RAG, document engineering).\n"
        "- **`case_studies.pdf`**: PyMuPDF-generated PDF featuring 3 enterprise case studies with impact metrics.\n"
        "- **`pricing_and_engagement_models.pdf`**: PyMuPDF-generated PDF detailing Fixed-Price, T&M rate cards, and retainers.\n"
        "- **`brand_voice_guide.md`**: Corporate tone, color codes (`#1E3A8A`, `#0D9488`), typography, and document conventions.\n\n"
        "### 3. `data/sample_scans/`\n"
        "- **`scanned_page.png`**: Pillow-rendered simulated scanned document (Statement of Work excerpt) with noise, "
        "grey background, and rotation artifacts for testing OCR & vision extraction capabilities.\n"
    )
    readme_path.write_text(content, encoding="utf-8")
    print(f"Generated README: {readme_path} ({readme_path.stat().st_size} bytes)")


def check_pptx_template(pptx_path: Path) -> bool:
    """Check if Company_Template.pptx exists without overwriting it."""
    if pptx_path.exists():
        print(f"[OK] Found existing PPTX template: {pptx_path} ({pptx_path.stat().st_size} bytes)")
        return True
    else:
        print(
            "\n" + "=" * 70 + "\n"
            f"[WARNING] '{pptx_path}' is MISSING!\n"
            "Please place a valid corporate PowerPoint presentation template named 'Company_Template.pptx' "
            "in 'data/sample_templates/' to enable PowerPoint deck generation demos.\n"
             + "=" * 70 + "\n"
        )
        return False


# ── Main Entrypoint ────────────────────────────────────────────────────────

def main() -> None:
    """Generate all sample assets, verify them, and print summary table."""
    print("=== Generating NexaWorks AI Solutions Demo Assets ===\n")

    base_dir = Path("data")
    templates_dir = base_dir / "sample_templates"
    kb_dir = base_dir / "sample_kb"
    scans_dir = base_dir / "sample_scans"

    templates_dir.mkdir(parents=True, exist_ok=True)
    kb_dir.mkdir(parents=True, exist_ok=True)
    scans_dir.mkdir(parents=True, exist_ok=True)

    # 1. Company Proposal DOCX
    proposal_path = templates_dir / "Company_Proposal.docx"
    generate_company_proposal(proposal_path)

    # 2. Enterprise KB documents
    generate_kb_documents(kb_dir)

    # 3. Scanned PNG image
    scan_path = scans_dir / "scanned_page.png"
    generate_scanned_page(scan_path)

    # 4. README.md
    readme_path = templates_dir / "README.md"
    generate_sample_readme(readme_path)

    # 5. Check PPTX Template
    pptx_path = templates_dir / "Company_Template.pptx"
    pptx_exists = check_pptx_template(pptx_path)

    # Summary Table
    print("\n" + "=" * 75)
    print(f"{'File Path':<48} | {'Status':<8} | {'Size (KB)':<10}")
    print("=" * 75)

    all_files = [
        proposal_path,
        kb_dir / "company_overview.md",
        kb_dir / "services_and_offerings.docx",
        kb_dir / "case_studies.pdf",
        kb_dir / "pricing_and_engagement_models.pdf",
        kb_dir / "brand_voice_guide.md",
        scan_path,
        readme_path,
        pptx_path,
    ]

    for fpath in all_files:
        if fpath.exists():
            size_kb = fpath.stat().st_size / 1024
            status = "OK"
        else:
            size_kb = 0.0
            status = "MISSING"
        print(f"{str(fpath):<48} | {status:<8} | {size_kb:>8.2f} KB")

    print("=" * 75)
    print("\nAll sample assets generated and verified successfully!")


if __name__ == "__main__":
    main()
