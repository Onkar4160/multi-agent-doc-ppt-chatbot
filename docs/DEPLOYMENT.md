# Deployment and Operations

Environment setup, configuration, running, operations and troubleshooting. Stage 3 covers the local environment; container deployment and monitoring are completed in Stage 13 ([PLAN](PLAN.md)).

Related: [ARCHITECTURE](architecture/ARCHITECTURE.md) · [TESTING](TESTING.md) · [DECISIONS](architecture/DECISIONS.md)

## 1. Requirements

| Component | Version | Notes |
|---|---|---|
| Python | 3.12 | project virtual environment in `.venv` |
| OS | Windows, macOS, Linux | developed on Windows 11 |
| Gemini API key | — | required for generation; the Google Cloud project must allow the Generative Language API |
| Tavily key | — | recommended for web research (DuckDuckGo fallback otherwise) |
| Pinecone key | — | optional knowledge base; local vectors otherwise |
| LibreOffice | optional | enables conversion/visual checks (reported as not performed when absent) |

## 2. Setup

```powershell
cd multi-agent-doc-ppt-chatbot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env      # then edit .env
python -m app.cli health         # checks keys, templates and model chain
```

## 3. Configuration (`.env`)

Every variable in `.env.example` is read by `app/core/config.py` (a test enforces this).

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | Gemini access (required) |
| `LLM_PRIMARY_MODEL` / `GEMINI_MODEL` | `gemini-3.8-flash` | first model in the chain |
| `LLM_FALLBACK_MODEL` / `GEMINI_FALLBACK_MODEL` | `gemini-3.7-flash,gemini-3.5-flash,gemini-3.1-flash-lite` | comma-separated fallbacks: retired (404) → skipped 24 h; out of quota (429) → skipped until reset |
| `LLM_MAX_RETRIES` | 3 | attempts per model for transient errors |
| `LLM_MAX_CALLS_PER_RUN` | 30 | real AI requests one chat message may use |
| `TAVILY_API_KEY` | — | web search |
| `PINECONE_API_KEY`, `PINECONE_INDEX`, `PINECONE_CLOUD`, `PINECONE_REGION` | — | knowledge base |
| `ACADEMIC_SEARCH` | true | OpenAlex / Semantic Scholar / arXiv |
| `SEMANTIC_SCHOLAR_API_KEY` | — | raises Semantic Scholar's keyless rate limit |
| `IMAGES_ENABLED`, `PEXELS_API_KEY`, `MAX_DECK_IMAGES` | true, —, 4 | photos for template photo frames |
| `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_EXPIRE_MINUTES` | — | login tokens; **change the secret** |
| `DEMO_USERNAME`, `DEMO_PASSWORD` | demo / demo123 | the configured user |
| `DATABASE_URL` | `sqlite:///./storage/app.db` | SQLite file |
| `UPLOAD_MAX_MB` | 25 | per-file upload limit |
| `DEFAULT_DOCX_TEMPLATE_PATH`, `DEFAULT_PPTX_TEMPLATE_PATH` | sample templates | used when no template is attached |
| `LOG_LEVEL`, `LOG_FORMAT` | INFO, text | `json` gives one JSON object per line with `run_id` |
| `MOCK_LLM` | false | **tests only**; the client refuses it outside pytest |

## 4. Running

```powershell
# terminal 1 – API
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --port 8000

# terminal 2 – UI
.\.venv\Scripts\Activate.ps1
python -m streamlit run ui/streamlit_app.py       # http://localhost:8501, log in with DEMO_USERNAME / DEMO_PASSWORD
```

Set `$env:API_BASE_URL="http://127.0.0.1:<port>"` before starting the UI when the API is not on port 8000.

Command line (same workflow as the chat):

```powershell
python -m app.cli generate "Make a 6-slide deck on community solar" --out out
python -m app.cli generate "Write a 2-page brief on heat pumps" --template my.docx --out out --budget 20
python -m app.cli analyze-template data/sample_templates/Company_Template.pptx
python -m app.cli eval acceptance --out out\acceptance   # offline acceptance report (AC-2..AC-10)
python -m app.cli eval conversation                       # request-understanding accuracy
```

Optional: set `RESEARCH_CONTACT=you@example.org` (Wikipedia asks API clients to identify themselves). `LLM_THINKING_BUDGET=512` keeps calls fast; raise it (or -1) for more deliberate planning.

Run log: `storage/logs/app.log` (rotated, 5 MB × 3); every line carries the run ID shown in the chat.

Optional: install LibreOffice so every delivered file is also converted to PDF as a check (otherwise the quality report lists the check as not performed).

## 5. Startup behaviour

1. Logging configured (`LOG_LEVEL`, `LOG_FORMAT`).
2. Tables created; missing columns added (`ADDED_COLUMNS` in `core/database.py`).
3. Runs left `running` by a restart are marked failed and get a reply ("…the server restarted… Say “try again”").
4. Default workspace templates analysed (or loaded from cache when the analyser version matches). This step calls Gemini once per template on first start, so **the API does not start without a working key**.

## 6. Operations

- **Logs:** every line inside a run carries `[run_…]`; the run's LLM usage (calls, cache hits, tokens, per model) is stored in `agent_runs.result_json.usage` and logged at the end of the run.
- **Per-step trace:** `GET /runs/{run_id}/trace` or the UI's Trace tab.
- **Storage:** `storage/` holds the database, uploads, outputs, photos, previews and caches; back it up as a unit. Deleting `storage/llm_cache` or `storage/search_cache` only costs repeated requests.
- **Free tier:** about 20 requests per model per day. A researched deck uses roughly 6–12 calls; keep several models in the fallback chain.

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Reply: "the AI provider refused the API key (Google says: “Your project has been denied access…”)" | The key's Google Cloud project is blocked or lacks the API | Create a key in a project with the Generative Language API enabled (aistudio.google.com), update `GEMINI_API_KEY`, restart |
| Reply: "…usage limit is reached…" | Free-tier daily quota used on every model in the chain | Wait for the reset, add models to `LLM_FALLBACK_MODEL`, or enable billing |
| Log: `model=… skipped for 1440 min (retired or unknown model)` | Model retired by Google | Update `LLM_PRIMARY_MODEL` / `LLM_FALLBACK_MODEL` to current models |
| API exits at startup with `GEMINI_API_KEY missing or placeholder` | No key in `.env` | Add the key |
| Reply: "…needed more than its budget of N AI calls…" | `LLM_MAX_CALLS_PER_RUN` reached | Ask for a shorter deliverable or raise the limit |
| Upload refused ("Macro-enabled…", "Corrupt file…", "unsafe size") | Upload safety rules (ADR-023) | Save without macros / re-export the file |
| Download link says "Not authenticated" | Link opened without the token (e.g. copied from an old message) | Download from the UI's file card |
