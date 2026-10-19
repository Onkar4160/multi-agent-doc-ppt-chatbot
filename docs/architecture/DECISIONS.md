# Architecture Decision Records

Format: context → decision → alternatives → consequences. Status: `accepted` · `proposed` · `superseded`.

## ADR-001 — Fixed stack: FastAPI + LangGraph + google-genai + Streamlit
- **Status:** accepted (owner constraint)
- **Context:** The product owner requires the existing stack to be kept.
- **Decision:** Python 3.12; FastAPI backend; LangGraph workflow; Gemini through `google-genai`; Streamlit UI talking to the API over HTTP; SQLite through SQLAlchemy 2; python-pptx / python-docx / openpyxl.
- **Alternatives:** a single-page web client and vendor-neutral REST adapters (as in DocForge) — rejected by the owner.
- **Consequences:** model choice is limited to Gemini unless a second client is added behind `LLMClient` (ADR-004). Streamlit's rerun model shapes the UI (ADR-021).

## ADR-002 — The graph orchestrates; agents are narrow typed calls
- **Status:** accepted
- **Context:** Free agent loops are hard to test, budget and explain.
- **Decision:** A LangGraph `StateGraph` of deterministic nodes owns control flow. Each LLM call belongs to one node, takes a prompt plus context, and returns a Pydantic model validated on arrival. Nodes are wrapped by `traced()` for trace rows and plain-language failure capture.
- **Alternatives:** tool-calling agent loop; one "do everything" prompt.
- **Consequences:** every step is visible in `agent_traces`; nodes are unit-testable with the mock LLM; adding a step means adding a node and an edge.

## ADR-003 — Evolve the existing content models into the IR (resolves PRD Q8)
- **Status:** accepted
- **Context:** The spec needs a typed content model with archetypes, typed blocks, claim links and style, from which files are rendered and to which edits apply. The repo already has `DeckModel`, `DocumentModel`, `WorkbookModel` with stable `node_id`s used by the editor, versioning, diffing, conversion and 180+ tests.
- **Decision:** Extend these models in place into the IR, backward compatible:
  - every model gets `schema_version`;
  - `SlideModel` gains `archetype`, `key_message`, typed `blocks` (bullets, table, chart, timeline, process, comparison, KPI, quote, image, callout) and `claim_ids`; existing `role`, `bullets`, `left`, `right` stay valid and map to archetypes;
  - `DocumentModel` blocks gain chart/figure, callout, quote and caption types, plus `citation_style` and front matter;
  - `WorkbookModel` gains sheet roles, named ranges, column types/formats and expected-value checks;
  - a `style` section on each model holds explicit overrides (e.g. heading size) for deterministic restyling.
  Stored `model_json` from older versions still loads (defaults fill new fields).
- **Alternatives:** a separate `app/models/ir/` package replacing the three models — rejected: it would rewrite the editor, versioning, diffing, converter and their tests (most of Stage 11) for no gain in capability.
- **Consequences:** renderers must accept both the old fields and new blocks; plan/archetype logic must keep old decks rendering identically (regression tests).

## ADR-004 — LLM access through `LLMClient` over `google-genai`
- **Status:** accepted
- **Decision:** All calls go through `app/llm/client.py`: `generate_text`, `generate_json(schema)`, `generate_with_image`. Structured output uses Gemini `response_schema` plus Pydantic validation. Responses are disk-cached by prompt hash when `use_cache=True`. `MOCK_LLM=true` (tests only) returns schema-valid canned objects.
- **Alternatives:** LangChain chat models; direct SDK calls in agents.
- **Consequences:** one place for retries, model chain, cache, usage logging (S3) and budgets (S3); a second vendor would implement the same three methods.

## ADR-005 — Research through providers with explicit degradation
- **Status:** accepted
- **Decision:** Web search: Tavily, DuckDuckGo fallback. Scholarly: OpenAlex, Semantic Scholar, arXiv in parallel (ADR-017). Knowledge base: Pinecone or local vectors. Page fetch for the top web results (S5). Each provider failure is logged and reported as a research gap; nothing is invented to fill it. Search results are cached per query per day.
- **Consequences:** research quality depends on keys and rate limits; coverage reporting (S5) makes gaps visible to the user.

## ADR-006 — Evidence model: Source → EvidenceItem → Claim
- **Status:** accepted (implementation S5)
- **Context:** Findings without locators cannot prove a claim, and source existence is not proof.
- **Decision:** A per-task `ResearchDossier` holds `Source`s (locator, title, publisher, dates, type, authority), atomic `EvidenceItem`s (statement, value/unit/as-of, quote, locator, source ID) and `Claim`s (statement, status: supported / assumption / interpretation / estimate / unsupported, evidence IDs). IR units reference claim IDs; citation numbers are derived at render time from first use. Conflicts and unanswered questions are recorded.
- **Alternatives:** keep `source_ids` on bullets only — insufficient for FR-RES-7…10.
- **Consequences:** writers must cite claim IDs; checks can measure coverage (AC-5); dossiers persist and are reused in follow-ups.

## ADR-007 — Templates: analyse into a manifest, render on the original package
- **Status:** accepted
- **Decision:** Template analysis produces a manifest (`TemplateProfile` today, extended in S6 for all formats) cached by file SHA-256 + `ANALYZER_VERSION`. Renderers open the original template and build on it. PPTX has two paths chosen by `design_mode`: layouts or designed-slide clones (ADR-016).
- **Consequences:** template identity (fonts, colours, artwork, masters) is preserved by construction; stale manifests are recomputed when the analyser changes.

## ADR-008 — Persistence: SQLite + filesystem storage
- **Status:** accepted
- **Decision:** SQLAlchemy 2 models; async sessions for the API, sync sessions for graph nodes; lightweight migrations in `core/database.py`. Files under `storage/` (uploads, outputs, media, previews, caches). JSON documents carry `schema_version`. New tables `tasks` and `research_dossiers`; new columns `quality_json` and `dossier_id` on `artifact_versions` (S3/S5).
- **Alternatives:** Postgres from day one — unnecessary for single-user v1; SQLAlchemy keeps the option open.

## ADR-009 — Validate the rendered file; report what was not checked
- **Status:** accepted
- **Decision:** Checks open the saved file. Tiers: 1 structural, 2 geometric/text-metric, 3 computation, 4 visual (renderer needed), 5 LLM review. Checks that cannot run are recorded as *not performed* with a reason. Repairs are deterministic first, LLM second; loop limit `MAX_REPAIR_ITERATIONS` (default 2). The `QualityReport` separates detected, corrected and unresolved findings.
- **Consequences:** quality claims are evidence-based; verdict shown to the user with every version.

## ADR-010 — Text measurement with real font metrics
- **Status:** accepted
- **Decision:** `services/layout/text.py` measures wrapped text with Pillow and the actual font file (metric-compatible substitutes when missing, average-advance heuristic last); fitting shrinks proportionally to role floors scaled by slide height; template boxes grow rather than overflow.
- **Consequences:** overflow checks agree with fitting; previews are approximate where fonts are substituted.

## ADR-011 — Formula verification with an independent evaluator
- **Status:** proposed (library chosen in Stage 10)
- **Decision:** Workbooks are recalculated outside Excel (candidates: `formulas`, `pycel`); computed errors and expected-value checks become findings; one corrective planning round on formula errors.
- **Consequences:** import cost of the evaluator is paid only when validating workbooks.

## ADR-012 — Background runs with persistent status and per-node traces
- **Status:** accepted
- **Decision:** `POST /chat` stores the user message and an `AgentRun`, returns `run_id`, and runs the graph as a FastAPI background task. Each node writes an `AgentTrace` row; `/runs/{id}` serves status, steps, reply, artifacts, citations and validation. The UI polls.
- **Alternatives:** worker queue (Celery/RQ) — deferred until multi-user load (PRD Q9).
- **Consequences:** a server restart loses in-flight runs; they stay `running` and are marked failed on next start (S3).

## ADR-013 — Clarification policy
- **Status:** accepted
- **Decision:** Ask when interpreter confidence < 0.6, an ambiguous acronym is the whole topic, target resolution gives 0 or > 1 candidates, a change would rewrite > 50 % of units while naming a narrower scope, or a required input is missing. The pending request is stored as `clarification_for` and merged with the user's answer.
- **Consequences:** fewer wrong generations; one extra turn in ambiguous cases (AC-9).

## ADR-014 — JWT authentication with owner-scoped data
- **Status:** implemented (Stage 3)
- **Decision:** One configured user (demo credentials in `.env`) logs in for a JWT; further users can be added to the `users` table. Sessions, files and artifacts carry `owner`; runs and artifacts are visible through their session. Other owners' records answer 404 (existence not revealed); rows from before ownership (owner NULL) stay visible. Browser download links carry the JWT as `?token=` because links cannot send headers.
- **Consequences:** tokens in URLs can end up in browser history; acceptable for single-user v1, to be replaced by short-lived signed download tokens for hosted use.

## ADR-015 — Quota-aware model chain and a reply for every message
- **Status:** accepted (implemented)
- **Decision:** `GEMINI_MODEL` then comma-separated `GEMINI_FALLBACK_MODEL`. 404 → skip 24 h; `QuotaFailure` PerDay or `RetryInfo` > 45 s → skip until retry time; short 429 → wait the advised delay; 401/403 → stop at once with `LLMAuthError` carrying Google's message (the key or project is refused, so no other model can help — observed live on 2026-10-18); all exhausted → `LLMRateLimited(quota=True)`. Every node failure becomes a plain-language `failure`; `ensure_reply` guarantees a stored reply; "try again" re-runs the failed request.

## ADR-016 — Clone mode for templates whose design lives on sample slides
- **Status:** accepted (implemented)
- **Decision:** When artwork sits on sample slides (Canva-style, blank layouts), each analysed sample slide becomes a base (title box, content region avoiding artwork, photo frames, region colour); output slides clone the best base for their archetype, rotating for variety; sample text is removed; samples are deleted at the end.

## ADR-017 — Scholarly sources next to web search
- **Status:** accepted (implemented)
- **Decision:** OpenAlex, Semantic Scholar and arXiv searched keylessly in parallel; papers cited as "Title (Venue, Year)" with DOI links; a failing index is skipped and reported.

## ADR-018 — Openly licensed photos for template photo frames
- **Status:** accepted (implemented)
- **Decision:** Openverse by default, Pexels with a key; most relevant unused photo first; re-encoded without metadata; credited in notes and on the Sources slide; only slides whose design has a photo frame get one.

## ADR-019 — Retire the unused legacy pipeline
- **Status:** implemented (Stage 3)
- **Context:** `services/orchestrator.py`, `services/renderers/{pptx,docx}_renderer.py`, `llm/schemas.py`, unmounted `api/upload.py`, `services/analyzers/{pptx,docx,pdf,image,xlsx}_analyzer.py`, `progressive_discovery.py` and `agents/{generator,researcher,kb_retriever}.py` duplicated live functionality with different schemas; the three agents imported `AgentState`, which no longer existed, so they could not even load.
- **Decision:** Remove them in Stage 3 after confirming no mounted route, graph node or test depends on them; move anything still needed (e.g. `slide_design`) to its live home.
- **Consequences:** one rendering path per format; less confusion for contributors.

## ADR-020 — Offline tests by default; live runs opt-in
- **Status:** accepted
- **Decision:** `tests/conftest.py` forces `MOCK_LLM=true`, a temporary SQLite DB and storage. External HTTP is mocked with `httpx.MockTransport` or patched functions. Live tests carry `@pytest.mark.live`, are deselected by default and need real keys (S3). Evaluation runs live separately (S12).

## ADR-021 — Streamlit as a thin HTTP client
- **Status:** accepted
- **Decision:** The UI calls only the REST API; no imports from `app/`. Long operations are polled; previews and history are cached in `st.session_state`.
- **Consequences:** the same capabilities are available to the API and CLI; UI tests use Streamlit `AppTest` with a fake backend in a separate process.

## ADR-022 — Per-run LLM usage accounting and call budget
- **Status:** implemented (Stage 3)
- **Context:** The free tier allows about 20 requests per model per day; a misbehaving loop could spend it in one message, and cost was invisible.
- **Decision:** `app/llm/usage.py` binds a `RunUsage` to each chat run (`track_run`). `LLMClient` charges every real attempt (retries included) and records cache hits, latency, tokens (`usage_metadata`) and per-model counts. When `LLM_MAX_CALLS_PER_RUN` is reached, `BudgetExceeded` stops the run with a plain reply. Usage is stored in `agent_runs.result_json.usage` and logged.
- **Alternatives:** token-based budgets — calls are what the free tier limits; tokens are recorded for later.
- **Consequences:** writers and research must stay within the budget; the CLI exposes `--budget`.

## ADR-023 — Upload safety rules
- **Status:** implemented (Stage 3)
- **Decision:** `app/core/file_safety.check_upload` judges files by content: OOXML must be a ZIP with `[Content_Types].xml` and the format's main part; ≤ 5000 entries, ≤ 300 MB uncompressed, compression ratio ≤ 150; macro-enabled extensions and `vbaProject.bin` refused; PDF needs `%PDF-`; PNG/JPEG signatures; CSV/TXT/MD must decode and contain no binary. Allowed: docx/dotx, pptx/potx, xlsx/xltx, pdf, png, jpg/jpeg, csv, txt, md. Messages say what to do.
- **Consequences:** renamed or broken files fail at upload with a clear reason instead of failing later inside a parser.

## ADR-024 — Structured logging with run IDs; error taxonomy
- **Status:** implemented (Stage 3)
- **Decision:** `app/core/logging.py` adds `run_id` (context variable) to every record; `LOG_FORMAT=json` for one JSON object per line. `app/core/errors.py` defines `AppError` (status + safe message) with `UserInputError`, `UnsupportedFileError`, `NotFoundError`, `TemplateError`, `ResearchError`, `RenderError`, `BudgetExceeded`; a FastAPI handler returns them as `{"detail": message}`, and `friendly_error` turns them into chat replies.
- **Consequences:** one run's activity can be followed across nodes, LLM calls and renderers.

## ADR-025 — Interpreter as a deterministic layer over the planner; measured on a held-out set
- **Status:** implemented (Stage 4)
- **Context:** The planner (`parse_plan`, one Gemini call or offline inference) already reasons about the message, and the free tier cannot afford a second call per message. The spec needs request kinds, an output specification, change scope with resolved targets and a clarification policy.
- **Decision:** `app/agents/interpreter.py` turns message + `ExecutionPlan` + conversation context + the active artifact's content model into an `Interpretation` without another LLM call: request kind (8 kinds), output spec (format by first mention, audience, tone, detail, length, language, citation style; inferred format recorded as an assumption), `ChangeRequest` (operation, scope level, targets resolved to node IDs, deterministic style changes), research depth, and ADR-013 clarifications (out-of-range parts, vague improvements, unnamed formatting, edits with no file, ambiguous acronyms). The graph stores the interpretation with the run, routes questions to answers, applies explicit slide counts, and keeps task state in `tasks`.
- **Evaluation method:** labelled cases written before the rules, split `dev` (tuned on), `test` (held out; run once, one general rule fixed afterwards), `test2` (written after, run once untouched). Offline planner by default; `python -m app.cli eval conversation --live` scores the Gemini planner.
- **Alternatives:** a dedicated interpreter LLM call (DocForge) — better on open-ended phrasing, but doubles calls per message on the free tier; revisit when quota allows.
- **Consequences:** phrasing outside the vocabulary of the rules falls back to the planner's plan; unusual audiences or tones may go unextracted (the planner still sees the full message).

## ADR-026 — Research pipeline with verified evidence; citation numbers are source IDs
- **Status:** implemented (Stage 5)
- **Context:** Research produced short LLM-written findings without locators; citations were numbered by evidence position and collided with source numbers (a "[1]" could show the wrong source); attached source files were never read because the graph passed no DB session; nothing reported coverage, conflicts or gaps.
- **Decision:** `app/agents/research_pipeline.py` runs: research depth from the interpretation (none / light / standard / deep) → research plan (one LLM call: questions + queries; standard questions with keyword sets offline) → web search, scholarly indexes, uploaded files and knowledge base, each source rated for authority (with rationale) and recency, ranked → the best web pages read as text (`services/page_fetch.py`, http(s), size-capped, scripts/navigation dropped) → evidence extraction (one LLM call over sources wrapped as untrusted data; deterministic extraction offline) → **verification**: an item is kept only if its quote is in its own source, or, without a quote, if every figure it states is → deduplication → claims (a "supported" claim whose evidence failed becomes "unsupported") → figure conflicts (same unit and year, > 25 % apart, different sources) → per-question coverage (answered: two sources or one high-authority source; topic words never count as an answer) → gaps. The dossier is stored per task, linked from the task and every artifact version, reused for regenerations, conversions, edits and retries, summarised in the reply and used to answer questions about a file's sources. Writers receive facts cited by **source ID**, wrapped as data, followed by known limitations to state honestly.
- **Alternatives:** an extraction call per source (DocForge) — more precise, but several calls per request on the free tier.
- **Consequences:** two LLM calls per researched request, as before; the default suite is offline (search patched in `conftest.py`, page reading off under the mock); the LLM extraction path is verified with a scripted model until a working key allows a live pass.

## ADR-027 — Template manifests with element roles; profiles cached by content
- **Context:** Template analysis knew layouts, placeholders and designed-slide bases, but not what each shape on a sample slide is for, so renderers could not tell a logo from sample text or artwork. DOCX analysis missed sections, numbering and cover elements; XLSX templates were not analysed; the same template was re-analysed (with an LLM tone call) on every upload; .potx/.dotx/.xltx could not be opened.
- **Decision:** Profiles gain manifests and `DesignTokens` for every format. PPTX: an inventory of every sample-slide shape with a role and a reason, by deterministic rules (`services/analyzers/pptx_manifest.py`): meta placeholders (date, footer, slide number) are structural; empty placeholders are placeholders; filled placeholders, charts and tables are sample content; short text repeated at the same position with the same text on at least half the slides, and small pictures in an edge band or repeated, are branding (also on masters and layouts); other artwork is decorative. Layouts get an archetype from name and placeholder types. DOCX: `services/analyzers/docx_manifest.py` (sections, styles by role with docDefaults inheritance, numbering, cover, tables, TOC). XLSX: `agents/xlsx_analyzer.py` with `services/analyzers/xlsx_manifest.py` (sheet roles, tables, formulas and the sheets they read, formats, validations, charts, names); no LLM call. Template packages are opened by rewriting the main content type in a temporary copy (`services/analyzers/template_package.py`). `services/template_cache.py` keys stored profiles by file SHA-256 + `ANALYZER_VERSION`.
- **Alternatives:** LLM classification of shapes — costly per template and not reproducible; rules are checked against hand labels instead.
- **Consequences:** analysis is deterministic apart from the tone call, so manifests are snapshot-tested (`tests/fixtures/manifests/`); any change to the rules must bump `ANALYZER_VERSION`, which also invalidates cached profiles. Renderers consume roles and tokens from Stage 8.

## ADR-028 — Plan before writing; deterministic plan checks and repairs
- **Context:** Generators wrote whole artifacts in one call: no narrative or archetypes, padding slides to hit counts, slides dropped by title words, word counts unmanaged, a workbook fallback with invented figures.
- **Decision:** `plan_content_node` makes one planner call per format (`agents/planner.py`) over a claim digest (`services/plan_inputs.py`) and a length target (`services/length_rules.py`). `services/plan_validation.py` checks the plan; blocking issues get one LLM revision, then deterministic repairs guarantee hard rules. Writers fill the plan (deck one call, document in ~1,800-word batches, workbook one call) and are conformed to it; missing units are written from their claims (`agents/offline_writer.py`). Explicit document lengths get one resize round.
- **Alternatives:** single-call generation with longer prompts — cheaper but uncheckable.
- **Consequences:** one or two extra LLM calls per artifact; the offline path is fully deterministic and tested; typed blocks render as bullets until Stage 8.

## ADR-029 — Native archetype components; generated design when there is no template
- **Context:** Decks had five text roles only; data became bullets; without a template the default sample template was used.
- **Decision:** `services/renderers/pptx_archetypes.py` draws each visual archetype with native PowerPoint objects (chart with editable data and a text description, table, shapes with live text) into a content region: the body placeholders' area on layout templates, the widest content base (extended to the free space below) on designed templates, a fixed grid in the generated design. Colours and fonts come from the template's design tokens (`palette_from_profile`) or a tone preset. `services/renderers/pptx_generated.py` renders decks without a template.
- **Alternatives:** images of charts (not editable, fails FR-PPTX-1); one fixed design for every request.
- **Consequences:** charts stay editable in PowerPoint; slide splitting for overflowing text is still open (FR-PPTX-5).

## ADR-030 — Word finishing in named styles and fields
- **Decision:** citations (numeric/APA/Harvard), TOC and PAGE fields, running headers and user style overrides are applied by `services/docx_finish.py` to fields and named styles, never as direct run formatting. Figures render as data tables with numbered captions.
- **Consequences:** Word updates the TOC on open; native Word charts remain out of scope (python-docx has no chart API).

## ADR-031 — Workbooks rendered at declared ranges and recalculated independently
- **Decision:** tables are placed exactly at their declared range as Excel tables; `services/xlsx_check.py` re-opens the saved file and evaluates formulas with a restricted evaluator (no `eval` of raw text: only parsed references, ranges and whitelisted functions) to find #REF!, #NAME?, #DIV/0!, circular references and failed expected-value checks.
- **Alternatives:** LibreOffice headless recalculation — heavier and not always installed; used only for PDF conversion when present.
- **Consequences:** formulas with functions outside the whitelist are reported as not checked, never as passing.

## ADR-032 — One quality report per version
- **Decision:** `services/quality_report.py` builds a `QualityReport` from file checks, content/plan checks, plan repairs and revisions, and the checks not performed (LibreOffice, visual, LLM review) with reasons; stored with the version and returned by the API.

## ADR-033 — Restyle without the LLM; regression guard on model and file
- **Decision:** formatting-only edits (style changes parsed, no content verb) set `StyleOverrides` on the model; `services/edit_guard.py` compares units by node ID and by rendered text and stores the result in the version diff.
- **Consequences:** a restyle costs no LLM call and provably changes no content unit.

## ADR-034 — Encyclopedia source and gap-driven follow-up searches
- **Context:** Live runs on named subjects (people, events) found few sources; questions stayed unanswered; figures for different metrics were flagged as conflicts.
- **Decision:** research looks up a Wikipedia article for the subject (`services/encyclopedia.py`, accepted only when its title matches the request and it is not a film/book named after it) and reads it up to 6,000 characters; the best web pages are read up to 4,000; questions no gathered text covers get one follow-up search each (max 2); figure conflicts require mostly identical statements; model-reported conflicts must span two sources; scholarly search runs for research topics, not for named subjects. Template files are never evidence.
- **Consequences:** no extra LLM calls; Wikimedia requires an identifying User-Agent (`RESEARCH_CONTACT` env var).

