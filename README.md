# Multi-Agent Document & PPT Generation Chatbot (POC)

A chatbot backend + UI that analyses uploaded DOCX/PDF/PPTX/image templates, researches the web, retrieves enterprise knowledge (RAG), and generates editable DOCX and PPTX files in the template's style — with citations, versioning, and traceability.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, Pydantic v2, SQLAlchemy + SQLite |
| Agents | LangGraph (supervisor pattern) |
| LLM | Google Gemini via `google-genai` |
| Embeddings | sentence-transformers `all-MiniLM-L6-v2` (384 dim) |
| Vector DB | Pinecone serverless |
| Web Search | Tavily (fallback: DuckDuckGo) |
| Files | python-docx, python-pptx, PyMuPDF, pdfplumber, Pillow |
| UI | Streamlit (HTTP → FastAPI) |
| Auth | JWT (single demo user) |

## Quick Start

### 1. Clone & install
```bash
git clone <repo-url>
cd multi-agent-doc-ppt-chatbot
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

### 2. Configure
```bash
copy .env.example .env
# Edit .env with your API keys
```

### 3. Run backend
```bash
uvicorn app.main:app --reload --port 8000
```

### 4. Run UI
```bash
streamlit run ui/streamlit_app.py
```

## Project Structure

```
app/
├── main.py              # FastAPI entry point
├── core/                # Config, database, security
├── api/                 # REST endpoints
├── models/              # SQLAlchemy ORM models
├── llm/                 # Gemini client wrapper
├── agents/              # LangGraph multi-agent system
└── services/            # Business logic, analyzers, renderers
ui/
└── streamlit_app.py     # Chat UI
data/
├── sample_templates/    # Example templates for testing
├── sample_kb/           # Sample knowledge base documents
└── outputs/             # Generated output files
storage/
├── uploads/             # User-uploaded files
├── artifacts/           # Generated DOCX/PPTX versions
└── llm_cache/           # On-disk LLM response cache
```

## Architecture

```
User ──▶ Streamlit UI ──HTTP──▶ FastAPI
                                  │
                          LangGraph Supervisor
                         ┌────┼────┼────┐
                    Researcher  KB   Generator  Editor
                    (Tavily)  (RAG)  (Gemini)  (Gemini)
                                  │
                          Renderers (DOCX/PPTX)
                                  │
                          ArtifactVersion (DB)
```
