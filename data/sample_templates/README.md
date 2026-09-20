# NexaWorks Sample Assets & Templates Directory

This directory contains realistic sample templates, enterprise knowledge base documents, and simulated scanned files for demonstrating the NexaWorks Multi-Agent Document & Presentation Chatbot.

## Directory Structure & Purpose

### 1. `data/sample_templates/`
- **`Company_Proposal.docx`**: Styled AI consulting proposal template for NexaWorks AI Solutions (Pune). Includes custom cover block, typography, styled tables (Timeline & Pricing), team section, and header/footer. *Note: Executive Summary is omitted intentionally to demo dynamic generation.*[REQUIRED]
- **`Company_Template.pptx`**: Master corporate presentation template with slide masters, brand colors, and layouts for generating presentation decks.

### 2. `data/sample_kb/`
- **`company_overview.md`**: Markdown overview of NexaWorks AI Solutions (location, history, metrics).
- **`services_and_offerings.docx`**: Detailed service catalog (Generative AI strategy, LangGraph multi-agent, RAG, document engineering).
- **`case_studies.pdf`**: PyMuPDF-generated PDF featuring 3 enterprise case studies with impact metrics.
- **`pricing_and_engagement_models.pdf`**: PyMuPDF-generated PDF detailing Fixed-Price, T&M rate cards, and retainers.
- **`brand_voice_guide.md`**: Corporate tone, color codes (`#1E3A8A`, `#0D9488`), typography, and document conventions.

### 3. `data/sample_scans/`
- **`scanned_page.png`**: Pillow-rendered simulated scanned document (Statement of Work excerpt) with noise, grey background, and rotation artifacts for testing OCR & vision extraction capabilities.
