# Implementation Status

Last updated: 2026-10-18

## Stage summary

| # | Stage | Status | Evidence |
|---|---|---|---|
| 1 | Requirements | verified | [PRD](PRD.md): scenarios S1–S13, 74 functional and 11 non-functional requirements, AC-1…AC-11, question register Q1–Q10; [PLAN](PLAN.md); coverage register below; `tests/test_docs_consistency.py` |
| 2 | Architecture | verified | [ARCHITECTURE](architecture/ARCHITECTURE.md) (component, graph, sequence, lifecycle diagrams; requirement ownership for all 74 FRs), [DECISIONS](architecture/DECISIONS.md) ADR-001…021 (ADR-003 resolves Q8), [DATA_MODELS](DATA_MODELS.md), [QUALITY](QUALITY.md), [TESTING](TESTING.md), [AGENTS](../AGENTS.md); docs consistency tests 6–10 |
| 3 | Foundation | verified | Legacy pipeline removed (14 files, ADR-019); upload safety (ADR-023); error taxonomy + run-ID logging (ADR-024); owner scoping (ADR-014); per-run LLM budget and usage (ADR-022); 401/403 stop the model chain; `tasks` / `research_dossiers` tables, owner/quality/dossier columns with migrations; interrupted-run recovery; contract schemas (`interpretation.py`, `evidence.py`, `quality.py`) with invariants; `schema_version` on content models; CLI (`app/cli.py`); `pytest.ini` markers; live smoke tests; [DEPLOYMENT](DEPLOYMENT.md); `tests/test_foundation.py` (19 tests) |
| 4 | Conversation | done | `app/agents/interpreter.py` (Interpretation: 8 request kinds, output spec, change scope with node-ID targets, style changes, clarification policy), `services/task_state.py` (tasks across turns), graph integration (`_interpret_and_track`), labelled set of 87 messages (`eval/conversation`), `python -m app.cli eval conversation`; `tests/test_conversation_understanding.py` (14 tests). Offline-verified; the live pass is blocked by the refused Gemini key — see the evaluation record. |
| 5 | Research | done | `app/agents/research_pipeline.py` (plan → search/scholarly/files/KB → authority + recency ranking → page reading → extraction → verification → dedupe → claims → conflicts → coverage → gaps), `services/source_quality.py`, `services/page_fetch.py`, dossier persistence and reuse in the graph, research summary in replies, research digest for questions about a file; `tests/test_research_evidence.py` (14 tests). Live check of search, scholarly and page reading done (record below); LLM extraction verified with a scripted model only (key refused). |
| 6 | Templates | done | Element inventory with roles and reasons for every sample-slide shape (`services/analyzers/pptx_manifest.py`), layout archetypes, master/layout branding, DOCX manifest (`services/analyzers/docx_manifest.py`), XLSX analyser and manifest (`agents/xlsx_analyzer.py`, `services/analyzers/xlsx_manifest.py`), design tokens for all formats, .potx/.dotx/.xltx support (`services/analyzers/template_package.py`), profile cache by SHA-256 + analyser version (`services/template_cache.py`), `analyze-template` CLI for all six types; `tests/test_template_analysis.py` (20 tests): manifest snapshots for the 5 sample templates and a built workbook, roles 50/50 and layout archetypes 12/12 against hand labels. |
| 7 | Planning | done | Planner (`agents/planner.py`), plan validation and repairs (`services/plan_validation.py`), length rules, claim digest, plan-following writers with conformance (deck, document in batches, workbook), offline writer, typed blocks, `plan_content_node`; `tests/test_content_planning.py` (20 tests): AC-5 100 % on fixtures, AC-7 exact for slides/sheets and plan allocation within 5 %. Live LLM pass blocked by the refused key. |
| 8 | PPTX | done | Archetype component library (`services/renderers/pptx_archetypes.py`), generated design system (`services/renderers/pptx_generated.py`), visual slides on widest template bases (clone path) or in place of body placeholders (layout path), template palette from design tokens, tone-based presets; `tests/test_pptx_generation.py` (10 tests): all archetypes on 3 sample templates + generated design with zero critical file findings (AC-3, AC-8 sample text), preview PNG checks. Live S1/S2 run blocked by the refused key. |
| 9 | DOCX | done | Citation styles, TOC, page numbers, running headers, style overrides in named styles, quote/callout/figure blocks (`services/docx_finish.py`). `tests/test_docx_xlsx_edit_qa.py` (14 tests). |
| 10 | XLSX | done | Renderer rewrite (tables, formats, frozen headers, names, charts, data bars, validation) and independent recalculation with expected-value checks (`services/xlsx_check.py`). `tests/test_docx_xlsx_edit_qa.py` (14 tests). |
| 11 | Orchestration & editing | done | LLM-free restyle edits, regression guard on model and files (`services/edit_guard.py`). `tests/test_docx_xlsx_edit_qa.py` (14 tests). |
| 12 | QA & evaluation | done | File checks for all formats, quality report per version, acceptance evaluation `eval/acceptance` + `python -m app.cli eval acceptance`: AC-2 100 %, AC-3 0, AC-4 0 errors / 100 % checks, AC-5 100 %, AC-6 100 %, AC-7 100 %, AC-8 0, AC-10 100 % (offline). `tests/test_docx_xlsx_edit_qa.py` (14 tests). |
| 13 | Delivery | done | DEPLOYMENT and README commands, docs consistency green. Live runs (AC-1 live part) blocked by the refused Gemini key. `tests/test_docx_xlsx_edit_qa.py` (14 tests). |

Test suite: **345 passed, 1 skipped, 3 live deselected** (offline, mocked LLM). Baseline before Stage 1: 183 passed.

## Requirement coverage register (baseline, 2026-10-18)

How the current code meets each PRD requirement. **met** = behaviour exists and is tested; **partial** = exists with gaps (listed); **missing** = not implemented. Later stages update this table; a requirement moves to *met* only with test evidence.

| Requirement | Coverage | Current state / gap |
|---|---|---|
| FR-CONV-1 | met | `POST /chat` with `file_ids`; file-only messages get an immediate reply (`tests/test_reliability.py`). |
| FR-CONV-2 | met | Eight kinds (new task, continuation, modification, regeneration, objective change, clarification answer, question, retry); kind accuracy 100 % on 87 labelled messages offline (held-out `test` measured 96 % before one fix). |
| FR-CONV-3 | met | `OutputSpec`: format, audience, tone, detail level, length (slides/pages/words/sheets, explicit flag), language, citation style; explicit slide counts override the planner (`test_new_task_opens_a_task_…`). Audience/tone not yet used by the writers (Stage 7). |
| FR-CONV-4 | met | Format taken from the first mention; when unstated the inference is recorded in `Interpretation.assumptions` (`test_inferred_format_is_recorded_as_an_assumption`). |
| FR-CONV-5 | partial | Slides (numbers, lists, ordinals, last), sections (number or heading), document tables, sheets resolved to node IDs (target accuracy 100 % on the labelled set); charts and paragraphs-by-content arrive with typed blocks (Stages 7–8). |
| FR-CONV-6 | met | `ChangeRequest` with operation, scope level (artifact/unit/element/style), resolved targets and deterministic `StyleChange`s; operation and style accuracy 100 % on the labelled set. Applying style changes is Stage 9/11. |
| FR-CONV-7 | met | Clarification for ambiguous acronyms, out-of-range parts (listing the real ones), vague improvements, unnamed formatting, edits with no file, ambiguous templates; recall 100 % and no needless questions on the labelled set; tasks wait in `awaiting_clarification`. |
| FR-CONV-8 | met | Sessions, messages, runs, traces, artifacts and versions in SQLite. |
| FR-CONV-9 | met | Questions are routed to `answer_node`; questions about the file itself use document scope plus a research digest (sources with authority and date, coverage, conflicts) (`test_questions_about_sources_are_answered_from_the_stored_research`). |
| FR-CONV-10 | met | `ensure_reply`, plain-language `friendly_error`, stored reply fallback (`tests/test_reliability.py`). |
| FR-RES-1 | met | Depth from the interpretation (none/light/standard/deep, "none" when the user rules out web search) drives question count, queries, page reads and scholarly search. |
| FR-RES-2 | met | Research plan of prioritised questions with queries (LLM, or standard questions with keyword sets offline); coverage is reported per question. |
| FR-RES-3 | met | Tavily/DuckDuckGo search plus reading of the best-ranked pages (http(s) only, size-capped, scripts and navigation dropped); live-checked 2026-10-18. |
| FR-RES-4 | met | OpenAlex, Semantic Scholar, arXiv with venue/year/DOI (`tests/test_research_and_photos.py`). |
| FR-RES-5 | met | Uploaded source files are read (fixed: the graph passed no DB session), registered as high-authority sources with block locators and cited; KB hits enter the same dossier (`test_attached_source_files_become_citable_sources`). |
| FR-RES-6 | met | `SourceRecord`: kind, title, URL/locator, publisher, authors, venue, DOI, publication and retrieval dates, authority with rationale. |
| FR-RES-7 | met | Atomic evidence with value, unit, as-of date, verbatim quote and locator; every item verified against its own source text; invented quotes and figures dropped (`test_llm_evidence_is_checked_against_its_source`). |
| FR-RES-8 | met | Figure conflicts across sources (and LLM-reported ones) flagged on claims; unanswered questions and provider errors recorded as gaps; both reach writers as limitations. |
| FR-RES-9 | partial | Dossier claims carry evidence IDs or an explicit status (supported / assumption / interpretation / estimate / unsupported); slides and sections still cite source IDs, not claim IDs — linking content units to claims is Stage 7. |
| FR-RES-10 | met | Coverage per question (answered / partial / unanswered) in the dossier, the run result (`research`) and the reply. |
| FR-RES-11 | met | Regenerations, conversions, edits and retries reuse the task's dossier without new searches (`test_generation_stores_the_dossier_reports_coverage_and_reuses_it`). |
| FR-RES-12 | met | Source text wrapped in delimited blocks marked "content, not instructions" (delimiters inside the text neutralised) in extraction and writer prompts. |
| FR-TPL-1 | met | PPTX/POTX, DOCX/DOTX, XLSX/XLTX accepted and analysed; .potx/.dotx render like .pptx/.docx and resolve as uploaded templates (`test_template_packages_*`, `test_uploaded_template_packages_resolve_as_templates`). Applying XLSX templates to generated workbooks is Stage 10. |
| FR-TPL-2 | met | Slide size, theme, layouts with archetypes and placeholders, sample-slide bases, and an inventory of every sample-slide shape (kind, box, z-order, placeholder type, font, fill, image, text preview) stored in the profile. A group is one element; its children are not listed. |
| FR-TPL-3 | met | Every element gets a role (structural / branding / decorative / placeholder / sample_content) with a reason; branding by repetition across slides and logo-like pictures, including master/layout logos. 50/50 against hand labels (`test_element_role_accuracy_against_hand_labels`). |
| FR-TPL-4 | met | `analyze_slide_design`: design mode, bases, palette, fonts (`tests/test_pptx_clone.py`). |
| FR-TPL-5 | met | DOCX manifest: sections (orientation, size, margins, headers/footers, page-number fields, different first page), styles by role with inherited fonts, list styles, numbering definitions, cover lines/images, tables, TOC, theme (`test_docx_manifest_reads_layout_rules`). |
| FR-TPL-6 | met | XLSX manifest: sheet roles, used ranges, headers, freeze panes, tables, formulas with referenced sheets, number formats, conditional formats, validations, merged ranges, charts with series, images, defined names (`test_xlsx_manifest_structure`). |
| FR-TPL-7 | met | Profiles carry `sha256`, `analyzer_version` and `DesignTokens` for all formats; `cached_profile` reuses a stored profile for identical bytes and recomputes on a new analyser version (`test_cached_profile_hits_by_content_and_recomputes_on_version`). |
| FR-PLAN-1 | met | `plan_content_node` plans every requested format before writing (`agents/planner.py`); plans stored in the run result (`test_plan_content_node_plans_every_output`). |
| FR-PLAN-2 | met | `DeckPlan`: narrative, per slide purpose, assertion headline, key message, archetype, block kinds, claim IDs, notes intent; writer conformed to the plan (`test_deck_writer_is_conformed_to_its_plan`). Archetype rendering is Stage 8. |
| FR-PLAN-3 | met | `DocumentPlan`: sections with purpose, argument, block kinds, claims, word budget; executive summary, TOC and references flags; written in batches with earlier sections as context. |
| FR-PLAN-4 | met | `WorkbookPlan`: sheet roles, typed columns with formats, formula patterns, assumptions with names and source or rationale, charts, expected-value checks; IR gains `SheetModel.role`, `named_ranges`, `checks` (`test_workbook_plan_roles_checks_and_explicit_sheets`). Recalculation of checks is Stage 10. |
| FR-PLAN-5 | met | `services/length_rules.py`: explicit slides/sheets exact, pages/words ±15 % with one length revision round; otherwise content need, never template length (`test_length_targets_follow_request_not_template`, `test_document_length_revision_round_hits_explicit_target`). Live word-count check pending a working key. |
| FR-PLAN-6 | met | Typed blocks (`models/blocks.py`): bullets, paragraph, table, chart, timeline, process, comparison, KPI, quote, image, callout; shown as bullets/columns until Stage 8 (`test_typed_blocks_render_without_loss`). |
| FR-PLAN-7 | met | `services/plan_validation.py`: duplicates, empty units, unknown/unsupported claims, sources, length, archetype fit, off-brief units, structure; one LLM revision round then deterministic repairs; content checked against the plan in `validate_node`. |
| FR-PPTX-1 | met | Native charts (editable data), native tables, shapes and live text for every archetype; no rasterised text (`test_every_archetype_renders_natively_with_zero_critical_findings`). |
| FR-PPTX-2 | met | Layout path and designed-slide clone path keep the template design (`tests/test_pptx_clone.py`). |
| FR-PPTX-3 | met | Generated 16:9 design (`services/renderers/pptx_generated.py`): cover/section/content/closing styles, title rule, slide numbers, Sources slide; palette and fonts chosen from tone/audience/purpose (formal, friendly, technical, bold); selected by "no template", "without a template", "own/clean design" (`test_generated_design_follows_tone_and_is_16_by_9`). |
| FR-PPTX-4 | met | `services/renderers/pptx_archetypes.py`: chart, table, KPI, timeline, process, comparison, quote, callout drawn natively in the template's content region (layout and clone paths) or the generated grid; archetype chosen by the plan from the evidence (Stage 7). Image+text uses template photo frames. |
| FR-PPTX-5 | partial | Measured fitting with floors, growing template boxes, one "shorten" round, cards sized to content; slide splitting not implemented. |
| FR-PPTX-6 | met | Speaker notes, inline citation markers, Sources & References slide. |
| FR-PPTX-7 | met | Any slide count on any template. |
| FR-PPTX-8 | met | Openverse/Pexels photos in template photo frames with credits. |
| FR-DOCX-1 | met | Title/cover, executive summary, TOC field (documents over 3,000 words or on request), H1–H3, lists, tables, quotes, callouts, figures as data tables with numbered captions. Native Word charts are not generated (python-docx has no chart API). |
| FR-DOCX-2 | met | Numeric, APA and Harvard in-text citations and reference lists (`services/docx_finish.py`; `test_docx_toc_page_numbers_and_apa_citations`, `test_harvard_reference_entry`). |
| FR-DOCX-3 | met | Template headers/footers/margins kept; page-number field and running header added to sections that lack them. |
| FR-DOCX-4 | partial | Template styles, numbering definitions, headers/footers and section setup reused; sample body removed; the template's cover block is not re-created. |
| FR-DOCX-5 | met | `StyleOverrides` written to named styles (Heading 1/2, Title, Normal), no direct formatting; restyle edits need no LLM call (`test_style_overrides_go_into_named_styles`). |
| FR-DOCX-6 | met | Word budget from the plan (explicit length or content need), no padding; one resize round for explicit lengths (Stage 7). |
| FR-XLSX-1 | met | Sheet roles (readme, assumptions, data, calculations, report, sources) from the plan, coloured tabs. |
| FR-XLSX-2 | met | Excel table objects with filters, number formats by column type, units in headers, frozen header rows (`test_xlsx_tables_formats_names_charts_and_recalculation`). |
| FR-XLSX-3 | met | Formulas written as formulas; assumptions as defined names. |
| FR-XLSX-4 | met | Native charts with data and category references across sheets; report sheet. |
| FR-XLSX-5 | met | Data bars on data columns; numeric validation on assumption inputs. |
| FR-XLSX-6 | met | `services/xlsx_check.py` recalculates every formula from the saved file: #REF!, #NAME?, #DIV/0!, circular references, expected-value checks; unsupported functions reported as not checked (`test_recalculation_reports_formula_errors`). |
| FR-XLSX-7 | met | Sources sheet and assumptions with source ID or rationale. |
| FR-XLSX-8 | partial | Spreadsheet parsing for context; no profiling or cleaning report. |
| FR-VAL-1 | met | PPTX, DOCX and XLSX files re-opened and checked after every render (`services/quality_report.py`). |
| FR-VAL-2 | partial | Deterministic plan repairs and conformance, one LLM plan revision, one shorten round, one document resize round; no generic re-validate loop with a configurable limit. |
| FR-VAL-3 | met | `QualityReport` per version (detected / corrected / unresolved / not performed with reason), stored in `artifact_versions.quality_json` and returned by the versions API. |
| FR-VAL-4 | met | Every file re-opened with its library; LibreOffice PDF conversion when installed, otherwise reported as not performed. |
| FR-VAL-5 | partial | PPTX preview PNG checks in tests; DOCX/XLSX visual checks reported as not performed. |
| FR-VAL-6 | missing | LLM content review not implemented; reported as not performed. |
| FR-EDIT-1 | met | `versioning` with parent links, change summaries, diffs. |
| FR-EDIT-2 | met | Edits patch the model; formatting-only requests apply style overrides without an LLM call. |
| FR-EDIT-3 | met | `services/edit_guard.py` compares units in the model (node IDs) and in the files; stored in the version diff (`test_regression_guard_flags_out_of_scope_changes`). |
| FR-EDIT-4 | met | Version download and revert endpoints and UI. |
| FR-EDIT-5 | met | `converter` agent (DOCX ↔ PPTX). |
| FR-UX-1 | met | Streamlit UI with history, roles, progress, cards, thumbnails, quality report (`tests/test_ui_support.py`). |
| FR-UX-2 | met | FastAPI with background runs, `/runs/{id}` status and trace. |
| FR-UX-3 | met | JWT login; sessions, runs, files and artifacts owner-scoped, others' records answer 404; download links carry the JWT (`tests/test_foundation.py::test_other_users_records_look_missing`, `test_uploads_are_owned`). |
| FR-UX-4 | partial | Preferences only through the message text. |
| FR-UX-5 | met | `python -m app.cli health | analyze-template | generate | eval conversation | eval acceptance`. |

## Live verification log

| Date | Check | Result |
|---|---|---|
| 2026-10-18 | `pytest -m live tests/live` with the project `.env` | Scholarly search: pass. Openverse photo: pass. Gemini: **fail** — `gemini-2.5-flash` and `gemini-2.0-flash` (set in `.env`) answer 404 (retired); with the current chain Google answers **403 PERMISSION_DENIED "Your project has been denied access. Please contact support."** The key's Google Cloud project is blocked; live generation is impossible until a working key is supplied. The client now stops at the first 401/403 with that message. |

## Evaluation record

| Date | Suite | Planner | Result |
|---|---|---|---|
| 2026-10-18 | conversation `dev` (47) | offline | kind 100 %, targets 100 %, clarification recall 100 %, all spec/change fields 100 % (tuned on this split) |
| 2026-10-18 | conversation `test` (25, held out) | offline | **first run: 24/25, kind 96 %** — "How do I write a good executive summary?" read as a new task; general rule added (how/what questions ending in "?" are questions); now 25/25 |
| 2026-10-18 | conversation `test2` (15, written after tuning) | offline | 15/15 on first run |
| 2026-10-18 | research pipeline, "heat pumps in cold climates", standard depth | live web + scholarly + page reading; deterministic extraction (Gemini refused) | first run: 14 sources, coverage "4 of 4" but **inflated** (every fact matched every question through topic words), headings quoted, years parsed as values, duplicate facts. Fixed (topic words excluded, standard questions get keyword sets, headings/titles/questions skipped, years never values, per-source dedupe). Rerun: 12 sources (10 web pages, 2 papers), 26 verified facts spread q2 17 / q1 12 / q3 5 / q4 2, 0 duplicates, 0 supported claims without evidence; Semantic Scholar rate-limited without a key (skipped) |
| — | conversation, any split | live Gemini | **not performed**: the configured key is refused (403); run `python -m app.cli eval conversation --live` with a working key |

## Known limitations (baseline)

- No intermediate representation separating plan, content and rendering; edits re-render whole files.
- Decks have no data visuals (charts, tables, timelines, KPIs).
- Research produces findings, not traceable evidence; no coverage or conflict reporting.
- Only PPTX output is checked as a file; DOCX and XLSX are checked as models.
- No evaluation dataset or acceptance measurements yet.
- **Live generation blocked:** the configured Gemini key's project is refused by Google (403) and the `.env` model names are retired (404). Needs a new key and `LLM_PRIMARY_MODEL` / `LLM_FALLBACK_MODEL` updated to current models.
- Download links carry the JWT in the URL (single-user v1; signed short-lived links for hosted use).
- In-flight runs are lost on restart (marked failed with a retry hint), no resumption from a checkpoint.

## Change log

| Date | Change |
|---|---|
| 2026-10-20 | Conversation, edits and slide design (ADR-035–037): follow-ups take their subject from the whole conversation (“a document as well on this same”, “use this template and give me presentation as same”), the planner sees the full conversation and returns a resolved subject, a request with no subject anywhere gets one question; a missing or ambiguous template never blocks a request (default template used, reply says which); edits see real content, use precise operations (one point, one title, one phrase) inside a guarded scope and re-render with the version's own template, design and photos (no date re-stamping); slides: clause-boundary headlines, citations once, readable numbers, template colours (neutral accents for monochrome designs), topic photos inside the template's sample-photo frames, text kept clear of artwork, readable-panel titles, subtitle frames that follow their text, embedded template fonts used for measuring, narrow-area layouts for timeline/comparison/KPI/process, per-slide photo search, no template name on slides; replies name the template used, sizes, anything requested but not delivered, and next steps; rendered output in `storage/outputs` (tests no longer fill `data/outputs`). |
| 2026-10-19 | Live-testing fixes: follow-ups keep the subject ("report on him"); an attachment of another format no longer blocks a request (default template used, reply says so); edits outside the requested scope are restored from the previous version; research adds a relevance-checked Wikipedia article, deeper page reading, follow-up searches for uncovered questions, conflicts only between figures for the same metric, scholarly search only for research topics, and templates no longer count as sources; Word reports get credited photos. Speed: thinking budget per call (`LLM_THINKING_BUDGET`), deck and document plans and writers run in parallel, validation retries only the failing format, numbers from the request are not flagged as uncited. Reliability: run log in `storage/logs/app.log`, a file that cannot be saved is reported instead of silently missing, visual-slide fonts scale with the slide. |
| 2026-10-19 | Stages 9–13: Word citation styles/TOC/page numbers/style overrides; Excel renderer and recalculation checks; LLM-free restyle and edit regression guard; quality reports for every version; acceptance evaluation passing offline. Fixed: timeline slides could show an empty date box; workbook titles shifted tables so formulas pointed at wrong cells. |
| 2026-10-19 | Stage 8: native chart/table/KPI/timeline/process/comparison/quote/callout slides on templates and a generated no-template design with tone presets. Fixed: visual slides could land on template bases carrying sample charts (now the widest content bases). |
| 2026-10-18 | Stage 7: content plans for decks, documents and workbooks, plan validation with one revision round and deterministic repairs, length rules, typed content blocks, plan-following writers. Removed: generic filler slides, silent dropping of slides by title words, a workbook fallback with invented figures. |
| 2026-10-18 | Stage 6: template manifests for PPTX (element roles, layout archetypes, branding), DOCX (sections, styles by role, numbering, cover) and XLSX (new analyser); design tokens; .potx/.dotx/.xltx; profile cache by content hash + analyser version; manifest snapshots and hand-label accuracy tests. Fixed: uploaded .potx/.dotx were rejected as templates by template resolution; DOCX body fonts inherited from document defaults were reported as missing. |
| 2026-10-18 | Stage 5: research pipeline with verified evidence, dossier persistence and reuse, coverage/conflict/gap reporting, citation numbers = source IDs, research digest for questions about a file. Fixed: attached source files were never read (no DB session); citation numbers could point at the wrong source; questions overriding the planner raised a NameError that was silently caught (missing import); questions about a file were researched afresh instead of answered from the file; the default test suite called live search (now offline via `conftest.py`). |
| 2026-10-18 | Stage 4: interpreter (request kinds, output spec, change scope, targets, style changes, clarification policy), task state, graph integration (questions answered, explicit slide counts applied, clarifications asked), labelled evaluation set (87 messages, three splits) and `eval` CLI. |
| 2026-10-18 | Stage 3: foundation — legacy pipeline removed; upload safety; errors + run-ID logging; owner scoping; LLM budget/usage; refused keys stop the chain; new tables/columns + migrations; interrupted-run recovery; contract schemas; CLI; live smoke tests; DEPLOYMENT. Fixed: `UPLOAD_MAX_MB` was documented but ignored (config read `MAX_UPLOAD_MB`); `/files/{id}/profile` crashed when a file had been analysed twice. |
| 2026-10-18 | Stage 2: architecture set (ARCHITECTURE, DECISIONS, DATA_MODELS, QUALITY, TESTING, AGENTS). Q8 resolved: existing content models evolve into the IR (ADR-003). Legacy parallel pipeline scheduled for removal in Stage 3 (ADR-019). |
| 2026-10-18 | Stage 1: PRD, PLAN, STATUS with requirement coverage baseline; docs consistency test. |
| 2026-10-18 | Earlier porting from DocForge (before the stage plan): designed-template cloning, model fallback chain and plain-language failures, rendered-PPTX checks with one shorten round, scholarly research and template photos, Streamlit UI with history, roles, progress, thumbnails and quality report. |
