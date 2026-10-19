# Product Requirements Document

| Field | Value |
|---|---|
| Product | Multi-Agent Document & Presentation Platform (this repository) |
| Status | Stage 1 complete — requirements baselined |
| Stack constraint | FastAPI backend · LangGraph orchestration · Google Gemini via `google-genai` · Streamlit UI · SQLite/SQLAlchemy · python-pptx / python-docx / openpyxl |
| Related | [PLAN](PLAN.md) · [STATUS](STATUS.md) · [ARCHITECTURE](architecture/ARCHITECTURE.md) · [DECISIONS](architecture/DECISIONS.md) · [DATA_MODELS](DATA_MODELS.md) · [QUALITY](QUALITY.md) · [TESTING](TESTING.md) |

## 1. Product summary

The platform turns a natural-language request, plus optional templates and source files, into a finished, editable, evidence-backed **PowerPoint (PPTX)**, **Word (DOCX)** or **Excel (XLSX)** file. The user refines the result in the same conversation; a follow-up changes only what it targets.

The product is judged by the delivered file and its evidence — relevance, accuracy, completeness, usability, structural integrity and presentation quality — not by the pipeline finishing without an exception.

## 2. Goals and non-goals

### 2.1 Goals

- **G1 Understanding.** Infer purpose, audience, tone, depth, length and format from natural language, including references to earlier turns and existing artifacts.
- **G2 Evidence.** Research when the request needs it; keep every material factual claim traceable to a source or explicitly marked as an assumption, estimate or interpretation.
- **G3 Planning.** Decide the information architecture (storyline, section hierarchy, workbook model) before rendering.
- **G4 Template intelligence.** Read templates structurally and keep their visual identity in new compositions of any length.
- **G5 Quality.** Render professional, editable files; inspect them; repair what can be repaired; report the rest honestly.
- **G6 Safe editing.** Apply targeted changes with minimal collateral change, backed by immutable versions.
- **G7 Reproducibility.** Deterministic rendering from a stored content model; offline, repeatable tests.

### 2.2 Non-goals (v1)

- Real-time multi-user co-editing of one artifact.
- Output formats other than PPTX, DOCX, XLSX.
- AI image synthesis (openly licensed / stock photos and user images only).
- Macro-enabled files as input or output; VBA is never executed.
- Re-authoring SmartArt or embedded OLE objects (kept when untouched).
- Paywalled or login-restricted sources.

## 3. Users

| Persona | Typical need | Quality bar |
|---|---|---|
| Consultant / analyst | Client-ready deck in a corporate template, market research | Template fidelity, evidence, storyline |
| Student / researcher | Report with structure and references | Depth, citations, coherence |
| Finance / operations | Workbook with calculations, KPIs, charts | Formula correctness, clear structure |
| Manager | Quick briefing deck or memo, then tweaks | Speed, concise messages, safe edits |

## 4. User scenarios

| ID | Scenario | Expected outcome |
|---|---|---|
| S1 | "Create a 10-slide investor update on the European EV charging market for a non-technical board." No template. | Researched deck with a generated design, charts/comparisons where the content calls for them, sources slide. |
| S2 | User uploads a 6-slide designed template and asks for 14 slides. | 14 slides keeping the template's artwork, fonts and colours; no sample text; compositions adapted to content. |
| S3 | "Write a 12-page report on microplastics in drinking water with an executive summary and APA references." | DOCX with title page, summary, headings, tables, references in APA. |
| S4 | Report requested with a DOCX template attached. | Template styles, headers/footers, page setup and numbering reused; sample body removed. |
| S5 | "Build a 5-year SaaS revenue model with assumptions, monthly calculations and a dashboard." | XLSX with Assumptions, Calculations, Dashboard, Sources sheets; real formulas verified by recalculation. |
| S6 | User uploads a CSV/XLSX and asks for a summary workbook and a 5-slide findings deck. | Dataset profiled; workbook and deck built from the data with stated assumptions. |
| S7 | "Make the heading font size 20pt." on an existing DOCX. | Only heading styles change; content and structure identical. |
| S8 | "Rewrite slide 4 to focus on costs and add a chart." | Only slide 4 changes; other slides identical. |
| S9 | "Make it better." on a 20-slide deck. | Clarification question with concrete options, not a silent regeneration. |
| S10 | "Actually, make this a Word report instead." | New deliverable inheriting objective and research. |
| S11 | Topic with thin or conflicting evidence. | Artifact states limitations; quality report lists unsupported or conflicting claims. |
| S12 | "Make a deck on CJP." (ambiguous acronym) | One clarification question listing likely meanings; the answer completes the original request. |
| S13 | A generation step fails (quota, model retired, network). | Plain-language reply saying what failed, that nothing was delivered, and what to do; "try again" re-runs it. |

## 5. Functional requirements

Priority: **M** must (v1) · **S** should (v1 if feasible) · **C** could (later).

### 5.1 Conversation and request understanding (FR-CONV)

| ID | Requirement | Pri |
|---|---|---|
| FR-CONV-1 | Accept free text with optional attached files in a persistent chat session; files without text get a reply. | M |
| FR-CONV-2 | Classify each message: new task, continuation, artifact modification, regeneration, objective/scope change, clarification answer, question, retry. | M |
| FR-CONV-3 | Extract an output specification: format, purpose, audience, tone, language, detail level, length (unit, value, explicit vs inferred), style notes. | M |
| FR-CONV-4 | Infer the format when not stated ("deck" → PPTX, "model" → XLSX) and record the inference as an assumption. | M |
| FR-CONV-5 | Resolve references to earlier turns, artifacts and elements ("slide 4", "the chart", "that table", "the second section", "the heading font"). | M |
| FR-CONV-6 | Represent each modification as a change request with scope (artifact / slide / section / sheet / element / style) and operation. | M |
| FR-CONV-7 | Ask for clarification when confidence is below threshold, the change would be materially destructive, or the topic is ambiguous (S9, S12). | M |
| FR-CONV-8 | Persist sessions, messages, tasks, interpretations and artifact versions across restarts. | M |
| FR-CONV-9 | Answer questions about the current artifact or its sources without regenerating it. | S |
| FR-CONV-10 | Every user message receives a stored reply, including failures explained in plain language (S13). | M |

### 5.2 Research and evidence (FR-RES)

| ID | Requirement | Pri |
|---|---|---|
| FR-RES-1 | Decide whether research is needed and its depth (none / light / standard / deep) from objective and deliverable. | M |
| FR-RES-2 | Produce a research plan: prioritised questions the deliverable must answer. | M |
| FR-RES-3 | Search the web (Tavily; DuckDuckGo fallback) and fetch page text where permitted. | M |
| FR-RES-4 | Search scholarly literature (OpenAlex, Semantic Scholar, arXiv); cite papers with venue, year and DOI. | M |
| FR-RES-5 | Ingest user sources (PDF, DOCX, PPTX, XLSX, CSV, TXT, MD) as first-class sources; index them for retrieval (Pinecone or local). | M |
| FR-RES-6 | Register each source with locator, title, publisher, publication date, retrieval time, type and authority rating. | M |
| FR-RES-7 | Extract atomic evidence (facts, figures with unit and as-of date, findings, quotes) with source locators. | M |
| FR-RES-8 | Detect conflicts and gaps between sources; record them. | M |
| FR-RES-9 | Every factual claim in a content plan carries evidence IDs or an explicit status: assumption, interpretation, estimate, unsupported. | M |
| FR-RES-10 | Report research coverage (answered / partial / unanswered questions) with the result. | M |
| FR-RES-11 | Reuse research for follow-ups and format changes within a task. | S |
| FR-RES-12 | Treat retrieved text as data, never as instructions (prompt-injection boundary). | M |

### 5.3 Template understanding (FR-TPL)

| ID | Requirement | Pri |
|---|---|---|
| FR-TPL-1 | Accept PPTX/POTX, DOCX/DOTX and XLSX/XLTX templates. | M |
| FR-TPL-2 | PPTX: slide size, theme colours/fonts, masters, layouts, placeholders (type, geometry, inherited text styles), backgrounds, and every shape on sample slides (kind, geometry, z-order, grouping, fill, text style, image). | M |
| FR-TPL-3 | Classify elements as structural, branding, decorative, content placeholder or sample content. | M |
| FR-TPL-4 | Decide where the design lives (layouts vs designed sample slides) and derive reusable bases: title box, content region, photo frames, palette, fonts. | M |
| FR-TPL-5 | DOCX: page setup, margins, styles (paragraph, character, table, numbering), headers/footers, sections, cover elements, tables. | M |
| FR-TPL-6 | XLSX: sheets, used ranges, tables, defined names, formulas and dependencies, number formats, conditional formats, validations, charts and ranges. | M |
| FR-TPL-7 | Produce a stored, versioned template manifest and design tokens; cache by file hash and analyser version. | M |

### 5.4 Content planning (FR-PLAN)

| ID | Requirement | Pri |
|---|---|---|
| FR-PLAN-1 | Produce a format-specific plan (deck / document / workbook) before rendering. | M |
| FR-PLAN-2 | Deck plan: narrative arc; per slide purpose, assertion headline, key message, typed content blocks, visual archetype, claim links, speaker notes. | M |
| FR-PLAN-3 | Document plan: section hierarchy with purpose, argument, evidence and block types; front and back matter. | M |
| FR-PLAN-4 | Workbook plan: sheet roles (readme, assumptions, data, calculations, report, sources), typed columns with units and formats, formulas, named ranges, charts, expected-value checks. | M |
| FR-PLAN-5 | Length follows explicit user requirements exactly; otherwise it follows content need, never the template's length. | M |
| FR-PLAN-6 | Typed content blocks: bullets, paragraph, table, chart, timeline, process, comparison, KPI, quote, image, callout. | M |
| FR-PLAN-7 | Plan validation: no duplicate messages, no empty units, citation coverage, length compliance, archetype–content fit. | M |

### 5.5 PowerPoint generation (FR-PPTX)

| ID | Requirement | Pri |
|---|---|---|
| FR-PPTX-1 | Editable output only: real text, native charts and tables, shapes; no rasterised text. | M |
| FR-PPTX-2 | With a template: keep masters, theme and artwork; remove sample content; compose new slides from the template's layouts or designed sample slides. | M |
| FR-PPTX-3 | Without a template: generate a design (palette, fonts, grid) suited to audience and tone. | M |
| FR-PPTX-4 | Archetypes: title, agenda, section, bullets, two-column, comparison, timeline, process, chart, table, KPI, quote, image+text, summary/closing, references. Content decides the archetype. | M |
| FR-PPTX-5 | Text fitting from measured text size: shrink within floors, grow template boxes, shorten via re-plan, or split. | M |
| FR-PPTX-6 | Speaker notes, citation markers and a references slide when sources exist. | M |
| FR-PPTX-7 | Any slide count, independent of the template's slide count. | M |
| FR-PPTX-8 | Openly licensed or stock photos in template photo frames and image slides, cropped to fit, credited. | S |

### 5.6 Word generation (FR-DOCX)

| ID | Requirement | Pri |
|---|---|---|
| FR-DOCX-1 | Title page, optional executive summary/abstract, table of contents, headings H1–H3, body, lists, tables, figures with captions. | M |
| FR-DOCX-2 | Citations in numeric, APA or Harvard style with a references section. | M |
| FR-DOCX-3 | Headers, footers, page numbers, margins, consistent styles. | M |
| FR-DOCX-4 | With a template: reuse styles, numbering, headers/footers and section setup; remove sample body content. | M |
| FR-DOCX-5 | Style changes (e.g. heading size) are applied to styles, not as scattered direct formatting. | M |
| FR-DOCX-6 | Depth follows purpose; no padding to reach a page count. | M |

### 5.7 Excel generation (FR-XLSX)

| ID | Requirement | Pri |
|---|---|---|
| FR-XLSX-1 | Named sheets with defined roles; raw data, calculations and reports separated. | M |
| FR-XLSX-2 | Excel tables, typed columns, number formats, units in headers, freeze panes, filters. | M |
| FR-XLSX-3 | Real formulas for calculated fields; named ranges for assumptions. | M |
| FR-XLSX-4 | Native charts with correct ranges; KPI/dashboard sheet where relevant. | M |
| FR-XLSX-5 | Conditional formatting and data validation where meaningful. | S |
| FR-XLSX-6 | Independent recalculation: zero `#REF!`/`#NAME?`/unintended `#DIV/0!`; expected-value checks pass; circular references flagged. | M |
| FR-XLSX-7 | Sources and assumptions sheet linking figures to sources. | M |
| FR-XLSX-8 | Ingest CSV/XLSX datasets with type inference and a cleaning report. | M |

### 5.8 Validation and repair (FR-VAL)

| ID | Requirement | Pri |
|---|---|---|
| FR-VAL-1 | Format-specific checks after every render, reading the saved file. | M |
| FR-VAL-2 | Deterministic repairs first, LLM-assisted repair (e.g. one "shorten" re-plan) second; re-validate up to a configured limit. | M |
| FR-VAL-3 | Quality report separates detected, corrected and unresolved findings, and checks not performed (with reason). | M |
| FR-VAL-4 | Every file re-opens with python-pptx / python-docx / openpyxl; LibreOffice conversion when available. | M |
| FR-VAL-5 | Visual checks from rendered previews when a renderer is available; otherwise geometric checks, reported as such. | S |
| FR-VAL-6 | LLM content review against plan and evidence: repetition, shallowness, unsupported claims, tone. | S |

### 5.9 Editing and versioning (FR-EDIT)

| ID | Requirement | Pri |
|---|---|---|
| FR-EDIT-1 | Every generation or edit creates an immutable version with parent link, change summary and diff. | M |
| FR-EDIT-2 | Edits are patches to the content model; only affected units are re-planned or rewritten. | M |
| FR-EDIT-3 | Regression guard: units outside the change scope are identical in the model and equivalent in the file. | M |
| FR-EDIT-4 | Download any version; revert to a previous version. | M |
| FR-EDIT-5 | Convert between formats (DOCX ↔ PPTX, data → XLSX) inheriting objective and evidence. | S |

### 5.10 Application and UX (FR-UX)

| ID | Requirement | Pri |
|---|---|---|
| FR-UX-1 | Streamlit UI: chat, chat history, file upload with role (template / source / dataset), live progress, downloads, sources, quality report, slide thumbnails. | M |
| FR-UX-2 | REST API (FastAPI) covering the same capabilities; asynchronous runs with pollable status and per-step trace. | M |
| FR-UX-3 | Authenticated access (JWT); sessions, files and artifacts scoped to their owner. | M |
| FR-UX-4 | Output preferences (format, length, audience, tone) can be given in the message or the UI. | S |
| FR-UX-5 | Command-line entry point for batch generation and evaluation runs. | S |

## 6. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 Reproducibility | Rendering is deterministic for a given content model + template + configuration; LLM calls are cacheable and mockable for tests. |
| NFR-2 Testability | Each stage has automated tests; the full suite runs offline without API keys. |
| NFR-3 Performance | Researched 10-slide deck ≤ 6 min; targeted edit ≤ 60 s (excluding provider spikes); progress visible per step. |
| NFR-4 Cost control | Per-task LLM and search budgets; usage logged per call; free-tier quota exhaustion handled by the model fallback chain. |
| NFR-5 Reliability | Retries with backoff; retired/out-of-quota models skipped; partial results never presented as complete; every message gets a reply. |
| NFR-6 Security | Uploads size-limited and checked by content (magic bytes, zip-bomb limits); macros never executed; keys only from environment; source text never treated as instructions. |
| NFR-7 Privacy | Files and artifacts stored per session; deleting a session removes its files, versions and evidence. |
| NFR-8 Portability | Windows, macOS, Linux with Python 3.12; LibreOffice and PowerPoint optional and feature-detected. |
| NFR-9 Modularity | Agents, providers, renderers and validators behind interfaces; a new format or provider does not change the orchestrator core. |
| NFR-10 Observability | Structured logs with run IDs; per-node trace rows with timings; quality and usage metrics per run. |
| NFR-11 Accessibility | Real heading structure, alt text for images and charts, WCAG AA text contrast against the real background. |

## 7. Constraints and assumptions

- Architecture is fixed by the owner: FastAPI + LangGraph + `google-genai` + Streamlit (see DECISIONS, Stage 2).
- LLM: Gemini model chain configured in `.env` (`GEMINI_MODEL`, comma-separated `GEMINI_FALLBACK_MODEL`); free tier ≈ 20 requests per model per day.
- Research: Tavily (`TAVILY_API_KEY`) with DuckDuckGo fallback; OpenAlex/arXiv/Semantic Scholar keyless; Pinecone (`PINECONE_*`) for the knowledge base.
- Photos: Openverse keyless; Pexels when `PEXELS_API_KEY` is set.
- Files: python-pptx, python-docx, openpyxl, lxml; formula recalculation library chosen in Stage 10.
- LibreOffice is optional; dependent checks report "not performed" when absent.

## 8. Acceptance criteria

Accepted when all **M** requirements are implemented and these pass in the evaluation suite (TESTING, Stage 12):

| ID | Criterion |
|---|---|
| AC-1 | Each scenario S1–S13 has at least one automated end-to-end case passing offline with mocked LLM responses, and S1, S2, S3, S5, S8 have a recorded live run. |
| AC-2 | 100 % of generated files re-open without error; with LibreOffice present, 100 % convert to PDF. |
| AC-3 | PPTX: zero unresolved critical findings (measured text overflow, off-slide own shapes, template sample text, empty placeholders) across the evaluation set. |
| AC-4 | XLSX: zero formula errors; 100 % of declared expected-value checks pass within tolerance. |
| AC-5 | ≥ 95 % of factual claims in plans carry evidence IDs or an explicit non-fact status; 100 % of cited IDs resolve to registered sources. |
| AC-6 | Targeted edits: 100 % of out-of-scope units unchanged in the edit regression set; requested change present. |
| AC-7 | Explicit length honoured exactly for slides/sheets and within ±15 % for pages/words. |
| AC-8 | Template fidelity: only template fonts/colours/artwork used; designated branding kept; no sample text. |
| AC-9 | Ambiguous requests (edit or topic) in the evaluation set produce a clarification, not a generation. |
| AC-10 | Every delivered version has a quality report with detected / corrected / unresolved counts and not-performed checks. |
| AC-11 | Every message in the evaluation set receives a stored reply, including injected failures. |

Human-rated content and design quality uses the QUALITY rubric (Stage 2); target mean ≥ 4/5.

## 9. Scope boundaries

**In:** three formats; templates; web, scholarly and file research; conversational editing and versioning; validation and repair; Streamlit UI; REST API; CLI for evaluation.
**Out (v1):** co-editing; image generation; macros; paywalled sources; SmartArt authoring; PDF as a primary output.

## 10. Unresolved-question register

| ID | Question | Default until resolved | Owner | Status |
|---|---|---|---|---|
| Q1 | Which citation styles beyond numeric/APA/Harvard are required? | Those three. | Product | Open |
| Q2 | Multi-user accounts for hosted use? | One configured user with JWT; owner-scoped data. | Ops | Resolved for v1 |
| Q3 | Right-to-left and CJK layout support? | Unicode text works; layout tuned for left-to-right Latin. | Product | Open |
| Q4 | Is LibreOffice available in production? | Optional; visual/convert checks reported as not performed. | Ops | Open |
| Q5 | Retention period for files, artifacts and evidence? | Until the user deletes the session. | Product | Open |
| Q6 | Which Gemini models to standardise on as Google retires older ones? | Configurable chain; 404 → skip for 24 h. | Ops | Open |
| Q7 | Semantic Scholar keyless limit is often exhausted — obtain a key? | Continue with other indexes, report the gap. | Ops | Open |
| Q8 | Should the content model (IR) replace `DeckModel`/`DocumentModel` outright or wrap them? | Neither: the existing models are extended in place into the IR (DECISIONS ADR-003). | Eng | Resolved |
| Q9 | Job runner: FastAPI background tasks vs a separate worker queue? | Background tasks for v1; revisit for multi-user load. | Eng | Open |
| Q10 | Maximum upload size per file? | `UPLOAD_MAX_MB` (default 25 MB). | Ops | Resolved |
