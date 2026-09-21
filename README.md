# Multi-Agent AI Chatbot for Document & PPT Generation (POC)
 
A multi-agent chatbot that reads uploaded DOCX / PPTX / PDF / image templates, researches the web, retrieves company knowledge (RAG), and generates **editable** DOCX and PPTX files in the same style as the templates. Users can then edit the files in plain English. Every change is saved as a new version, and every fact carries a source citation.
 
Demo company used in the samples: **NexaWorks AI Solutions** (fictional).
 
## Features
 
| Requirement | How it is done |
|---|---|
| Multi-agent chatbot with Supervisor | LangGraph supervisor routes each request to specialist agents |
| Template analysis (DOCX, PPTX) | Analyzers extract fonts, styles, outline, layouts, placeholders and tone into a `TemplateProfile` |
| PDF, DOCX, PPTX, image support | Parsers for each type; scanned pages and images use Gemini vision OCR |
| Real-time web research | Tavily search (DuckDuckGo fallback), results cached per day |
| Enterprise RAG | Pinecone vector DB, `all-MiniLM-L6-v2` embeddings (384 dim) |
| Editable DOCX / PPTX generation | Files are rendered from a JSON model on top of the **uploaded template** (real styles, real layouts, real placeholders) |
| Conversational editing | Editor agent turns an instruction into small edit operations and applies them in code |
| DOCX <-> PPTX conversion | Converter agent maps one JSON model to the other |
| Citations and traceability | Source registry, `[n]` markers, Sources section/slide, per-step agent trace |
| Versioning | Every generation, edit, conversion or revert creates a new version; nothing is overwritten |
| Validation | Deterministic validator checks slide count, bullets, word limits, citations, placeholders |
| Secure, modular APIs | FastAPI, JWT auth, file type/size/zip validation, secrets only in `.env` |
 
## Architecture (short)
 
```
Streamlit UI  --HTTP-->  FastAPI  -->  LangGraph Supervisor
                                         |-- Document Analyzer / PPT Analyzer
                                         |-- Web Research Agent  (Tavily)
                                         |-- RAG Agent           (Pinecone)
                                         |-- Doc Generator / PPT Generator
                                         |-- Validator (no LLM)
                                         |-- Editor / Converter
                                         '-- SQLite: files, artifacts, versions, sources, traces
```
 
Full details and diagrams: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
 
## Tech stack
 
Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy + SQLite, LangGraph, Google Gemini (`google-genai`), Pinecone, sentence-transformers, Tavily / duckduckgo-search, python-docx, python-pptx, PyMuPDF, pdfplumber, Pillow, Streamlit.
 
## Project structure
 
```
app/
  main.py            FastAPI app
  api/               auth, files, kb, chat/runs, artifacts routes
  agents/            supervisor, graph, analyzers, web_research, rag_agent,
                     doc/ppt generators, validator, editor, converter
  services/          parsers, ocr, embeddings, vector_store, ingestion,
                     renderers, versioning, diffing, source_registry
  models/            DB models and Pydantic models (template profile, document/deck model, edit ops)
  llm/client.py      Gemini wrapper (retry, fallback model, disk cache)
ui/streamlit_app.py  Chat UI
scripts/             setup, demo and helper scripts
data/                sample templates, sample knowledge base, sample scans
samples/             sample generated outputs
docs/                architecture and demo script
tests/               pytest suite
```
 
## Setup
 
**Requirements:** Python 3.11+, Git. Accounts (free tiers work): Google AI Studio (Gemini), Pinecone, Tavily.
 
```powershell
git clone <your-repo-url>
cd multi-agent-doc-ppt-chatbot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env
```
 
On macOS/Linux use `source .venv/bin/activate` and `cp .env.example .env`.
 
Edit `.env`:
 
```
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
GEMINI_FALLBACK_MODEL=gemini-2.5-flash-lite
PINECONE_API_KEY=...
PINECONE_INDEX=nexaworks-kb
PINECONE_CLOUD=aws
PINECONE_REGION=us-east-1
TAVILY_API_KEY=...
JWT_SECRET=change-me
DEMO_USERNAME=demo
DEMO_PASSWORD=change-me
MAX_UPLOAD_MB=25
```
 
Use the exact model IDs shown in Google AI Studio if these differ. `.env` is never committed.
 
Create the sample files (only needed if `data/` is empty), then load the knowledge base:
 
```powershell
python scripts/create_sample_assets.py
python scripts/ingest_kb.py
```
 
## Run
 
```powershell
.\scripts\run_all.ps1
```
 
This opens two windows. Or start them by hand:
 
```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
python -m streamlit run ui/streamlit_app.py --server.port 8501
```
 
Open http://localhost:8501 and log in with `DEMO_USERNAME` / `DEMO_PASSWORD`. API docs: http://127.0.0.1:8000/docs.
 
## Usage
 
1. In the sidebar, upload your templates: `data/sample_templates/Company_Proposal.docx` and `data/sample_templates/Company_Template.pptx`. Pick them as the DOCX and PPTX template.
2. Click **Ingest sample KB** (or upload your own knowledge files).
3. Ask:
   > Research the latest Generative AI trends and create a proposal and 12-slide presentation using the same tone and style as the uploaded files.
4. Watch the live agent steps. Download the DOCX and PPTX, and open the citations and validation report.
5. Edit with the quick-action buttons or your own text:
   - Add an executive summary.
   - Make the presentation more concise.
   - Add a competitive analysis section.
   - Update the report using the latest web information.
   - Convert DOCX to PPTX.
6. Open the version history of an artifact to compare, download or revert to an older version.
## API overview (JWT required except `/health` and `/auth/login`)
 
| Area | Endpoints |
|---|---|
| Auth | `POST /auth/login` |
| Files | `POST /files/upload`, `GET /files`, `POST /files/{id}/analyze`, `GET /files/{id}/profile` |
| Knowledge base | `POST /kb/ingest`, `POST /kb/ingest-samples`, `GET /kb/search`, `GET /kb/stats` |
| Research | `POST /research` |
| Chat | `POST /chat`, `GET /runs/{id}`, `GET /runs/{id}/trace`, `GET /sessions/{id}/messages` |
| Artifacts | `POST /artifacts/generate`, `GET /artifacts`, `GET /artifacts/{id}/versions`, `GET /artifacts/{id}/versions/{n}`, `POST /artifacts/{id}/edit`, `POST /artifacts/{id}/revert`, `POST /artifacts/{id}/convert`, `GET /artifacts/{id}/download?version=N` |
 
## Scripts
 
| Script | Purpose |
|---|---|
| `scripts/create_sample_assets.py` | Build sample proposal, knowledge base files and a scanned page |
| `scripts/ingest_kb.py` | Ingest the sample knowledge base into Pinecone |
| `scripts/demo_analyze.py <file>` | Print the template profile of a file |
| `scripts/demo_research.py "<topic>"` | Run web research and print findings and sources |
| `scripts/demo_generate.py --live` | Generate DOCX and PPTX with real research and KB |
| `scripts/demo_chat.py` | Run the full supervisor workflow through the API |
| `scripts/demo_edit.py` | Run the edit, version, convert and revert demo |
| `scripts/smoke_llm.py` | Check that the Gemini key and model work |
 
## Tests
 
```powershell
python -m pytest -q
```
 
Tests use mocks and a temporary database and storage folder. They do not call external services.
 
## Design decisions
 
- **JSON model as the single source of truth.** Files are never edited directly. The editor changes the JSON, then the file is re-rendered with the original template. This keeps style and structure stable across edits.
- **Template fidelity.** Generators start from the uploaded template file, reuse its layouts, placeholders and styles, and never hardcode fonts or colours.
- **The LLM proposes, code applies.** The editor returns small edit operations. Code applies them, so untouched content stays identical.
- **Citations from the start.** Each fact keeps a source id from retrieval to output. The validator checks that ids exist and that numbers are cited.
- **No silent fallbacks.** If Gemini or Pinecone fails, the run fails with the real reason. Mock mode exists only for tests.
- **Rate-limit safety.** One Gemini wrapper handles retry, backoff, a fallback model and a disk cache.
## Security
 
JWT on all routes except health and login, upload type/size/zip checks, safe file names, secrets only in `.env`.
 
## Known limitations
 
- Free-tier API limits (Gemini, Tavily, Pinecone) can slow or block long runs. Wait and retry, or use the fallback model.
- Single demo user; no multi-tenant roles.
- Style fidelity depends on the template having real slide layouts and placeholders. Decks made from flat images or free text boxes cannot be reused as layouts.
- Web sources are only as good as the search results. Check the citations before you use the output.
## Troubleshooting
 
| Problem | Fix |
|---|---|
| `uvicorn` not found | Use `python -m uvicorn ...` inside the activated `.venv` |
| Gemini `404 model not found` | Copy a current model ID from AI Studio into `GEMINI_MODEL` |
| Gemini `429` | Wait one minute, or switch to the Flash-Lite model |
| Pinecone `dimension 384 does not match` | Use a new `PINECONE_INDEX` name; the code creates a 384-dim index |
| `PermissionError` on output file | Close the file in Word or PowerPoint |
| First embedding run is slow | The model downloads once (about 90 MB) |
 
