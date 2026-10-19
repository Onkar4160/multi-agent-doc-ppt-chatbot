# Porting plan: DocForge improvements → this project

Goal: bring the verified DocForge results into this project (FastAPI + LangGraph + google-genai + Streamlit) **without changing its architecture** and **without lowering output quality**. Keep token use low: copy and adapt working code, do not redesign.

Source of the working code: `D:\Temp\docforge\src\docforge\` (186 passing tests in `D:\Temp\docforge\tests\unit\`). Design decisions: `D:\Temp\docforge\docs\architecture\DECISIONS.md` (ADR-019 clone templates, ADR-020 model chain / replies).
Templates to verify with: `data/sample_templates/` (Green Cream Simple Aesthetic Watercolor, Company_Template, presentation_Template).

## Order (do one step, run `pytest`, show result, then next)

### 1. Templates keep their design (highest value)
Problem: `app/services/pptx_renderer.py` deletes sample slides and builds from layouts; Canva-style templates have blank layouts, so the design is lost.
Port:
- `docforge/template/slide_design.py` (analyse sample slides → bases: title box, subtitle, content region, photo frames, fonts, palette) → into `app/services/analyzers/pptx_analyzer.py` / `app/models/template_profile.py`.
- `docforge/render/pptx/clone.py` (clone a base per slide, re-link images, remove samples at end, rotate designs, wide areas for charts/tables/KPIs) → new `app/services/renderers/pptx_clone.py`, used by `pptx_renderer.render_pptx` when the template's design lives on its slides; keep the current layout path otherwise.
- `docforge/layout/backdrop.py` (colour behind a box from fills/pictures/inherited backgrounds).
- From `docforge/render/pptx/canvas.py`: `placeholder_text` (keeps template run style, grows box instead of overflowing), `set_geometry`, `tighten`, `separate`, `inherited_size`.
- From `docforge/design/system.py`: `tokens_from_template`, `darken_until`, `tint`.
- Text measurement: `docforge/layout/text.py`, `docforge/layout/fonts.py`.
Check: render a sample deck on all three templates; artwork kept on every slide, no "Lorem ipsum"/sample text, no overflow, no "(continued)" splits.

### 2. Reliability
- `app/llm/client.py`: comma-separated fallback chain (`gemini-3.8-flash`, then `gemini-3.7-flash,gemini-3.5-flash,gemini-3.1-flash-lite`); 404 → skip model 24 h; 429 with `QuotaFailure` PerDay or long `RetryInfo` → skip until retry time; short 429 → wait advised delay. Logic: `docforge/llm/gateway.py` + `_quota_details` in `docforge/llm/providers/http.py`. Free tier = 20 requests/model/day.
- `app/api/chat.py` + `app/agents/graph.py`: every message must store a reply; wrap interpretation and generation; plain-language errors (`friendly_error` in `docforge/orchestrator/generate.py`). "try again" retries the failed task; questions get an answer; attaching a file without text still triggers a reply.
- Template analysis cache must be versioned (old cached profiles recomputed).

### 3. Quality checks
Into `app/agents/validator.py`: text overflow (measured), font floors scaled by slide height, contrast against real backdrop, template sample-text residue, only our own shapes checked for out-of-bounds. One "shorten" re-plan round when text does not fit and slide count is fixed (`fit_problems` in `generate.py`). Source: `docforge/validate/pptx_checks.py`, `docforge/validate/repair.py`.

### 4. Research + photos
- Scholarly sources (OpenAlex, arXiv, Semantic Scholar): `docforge/research/academic.py` → `app/agents/web_research.py`.
- Photos (Openverse, optional Pexels), credits, no repeats, fill template photo frames: `docforge/media/images.py`.
- Ask a clarification question when the topic is an ambiguous acronym (e.g. "CJP").

### 5. UI (stay on Streamlit)
`ui/streamlit_app.py`: chat history sidebar, file upload with role (template/source/dataset), live progress steps, slide thumbnails (`docforge/render/pptx/preview.py`), quality report, download card. Reference look: `D:\Temp\docforge\src\docforge\web\static\index.html`.

## Rules
- Keep this repo's structure, names and style; small, focused edits; add tests per step.
- Never commit `.env`, keys, `storage/`, generated files.
- Report after each step: what changed, test result, anything not done.
